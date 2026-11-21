"""Lightweight, 100% local Python TTS audio generator (Kokoro-82M + sherpa-onnx VITS/MMS-TTS + OS fallbacks)
and Microsoft Edge Neural TTS online stream synthesizer."""

import asyncio
import base64
import platform
import re
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VOICES_DIR = Path(__file__).resolve().parent / "voices"

EDGE_INDIAN_VOICES: Dict[str, Dict[str, str]] = {
    "en-IN": {"female": "en-IN-NeerjaNeural", "male": "en-IN-PrabhatNeural"},
    "hi-IN": {"female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"},
    "mr-IN": {"female": "mr-IN-AarohiNeural", "male": "mr-IN-ManoharNeural"},
    "bn-IN": {"female": "bn-IN-TanishaaNeural", "male": "bn-IN-BashkarNeural"},
    "kn-IN": {"female": "kn-IN-SapnaNeural", "male": "kn-IN-GaganNeural"},
    "ta-IN": {"female": "ta-IN-PallaviNeural", "male": "ta-IN-ValluvarNeural"},
    "te-IN": {"female": "te-IN-ShrutiNeural", "male": "te-IN-MohanNeural"},
    "gu-IN": {"female": "gu-IN-DhwaniNeural", "male": "gu-IN-NiranjanNeural"},
    "ml-IN": {"female": "ml-IN-SobhanaNeural", "male": "ml-IN-MidhunNeural"},
}

_EDGE_VOICES_CACHE: Optional[List[Dict[str, Any]]] = None


def strip_markdown_and_symbols(text: str) -> str:
    """Strip markdown formatting, URLs, code blocks, and symbols so TTS reads clean natural speech."""
    if not text:
        return ""
    # Strip code blocks
    text = re.sub(r"```[\s\S]*?```", " ", text)
    # Strip markdown inline images and links [text](url) -> text
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Strip raw URLs
    text = re.sub(r"https?://\S+", "", text)
    # Strip headers # Header
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    # Strip bold/italic/strike asterisks and underscores
    text = re.sub(r"[*_~]+", "", text)
    # Strip bullets and list numbers at start of lines
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Strip blockquotes and table pipes
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|", " ", text)
    # Strip code backticks and braces/brackets/special characters
    text = re.sub(r"[`\\^<>{}[\]#]", " ", text)
    # Normalize whitespace
    return " ".join(text.split())


async def resolve_edge_voice(lang: str, voice_gender: str = "female") -> str:
    """Resolve an Edge TTS voice ShortName based on locale and gender, falling back dynamically via edge_tts.list_voices()."""
    gender = "male" if (voice_gender or "").strip().lower() == "male" else "female"
    clean_lang = (lang or "en-IN").strip()

    # 1. Exact or prefix match against Indian locales mapping
    for loc, voices in EDGE_INDIAN_VOICES.items():
        if loc.lower() == clean_lang.lower() or loc.lower().startswith(clean_lang.lower() + "-"):
            return voices[gender]

    # 2. Dynamic query from edge_tts.list_voices()
    global _EDGE_VOICES_CACHE
    try:
        import edge_tts  # type: ignore

        if _EDGE_VOICES_CACHE is None:
            _EDGE_VOICES_CACHE = await edge_tts.list_voices()
        voices = _EDGE_VOICES_CACHE
    except Exception as exc:
        raise RuntimeError(f"Failed to query edge_tts voices: {exc}")

    target_gender = "Male" if gender == "male" else "Female"

    # Match exact Locale and Gender
    for v in voices:
        if v.get("Locale", "").lower() == clean_lang.lower() and v.get("Gender", "").lower() == target_gender.lower():
            return str(v["ShortName"])

    # Match exact Locale (any gender)
    for v in voices:
        if v.get("Locale", "").lower() == clean_lang.lower():
            return str(v["ShortName"])

    # Match language code prefix (e.g. 'fr' or 'fr-CA' -> 'fr-FR')
    prefix = clean_lang.split("-")[0].lower()
    for v in voices:
        v_loc = v.get("Locale", "").lower()
        if (v_loc == prefix or v_loc.startswith(prefix + "-")) and v.get("Gender", "").lower() == target_gender.lower():
            return str(v["ShortName"])

    for v in voices:
        v_loc = v.get("Locale", "").lower()
        if v_loc == prefix or v_loc.startswith(prefix + "-"):
            return str(v["ShortName"])

    raise ValueError(f"No Edge Neural TTS voice found for locale: {lang}")


def _write_wav_int16(wav_path: Path, samples: list, sample_rate: int) -> None:
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        pcm = struct.pack(
            f"<{len(samples)}h",
            *(max(-32768, min(32767, int(s * 32767.0))) for s in samples),
        )
        wf.writeframes(pcm)


def _run_sherpa_onnx(text: str, lang: str, wav_path: Path) -> Tuple[bool, str]:
    """Run offline neural Kokoro-82M or VITS/MMS-TTS via sherpa_onnx if voice files exist."""
    voice_dir = VOICES_DIR / (lang or "en-IN")
    onnx_files = sorted(voice_dir.glob("*.onnx"))
    tokens_file = voice_dir / "tokens.txt"
    if not (onnx_files and tokens_file.exists()):
        return False, ""

    import sherpa_onnx  # type: ignore

    espeak_dir = voice_dir / "espeak-ng-data"
    voices_bin = voice_dir / "voices.bin"

    if voices_bin.exists():
        lexicons = ",".join(str(p) for p in sorted(voice_dir.glob("lexicon*.txt")))
        kokoro_kwargs = {
            "model": str(onnx_files[0]),
            "voices": str(voices_bin),
            "tokens": str(tokens_file),
        }
        if espeak_dir.exists():
            kokoro_kwargs["data_dir"] = str(espeak_dir)
        if lexicons:
            kokoro_kwargs["lexicon"] = lexicons

        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(**kokoro_kwargs),
                num_threads=2,
            )
        )
        model_tag = f"local-kokoro-{lang}"
    else:
        vits_kwargs = {
            "model": str(onnx_files[0]),
            "tokens": str(tokens_file),
        }
        if espeak_dir.exists():
            vits_kwargs["data_dir"] = str(espeak_dir)

        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(**vits_kwargs),
                num_threads=2,
            )
        )
        model_tag = f"local-sherpa-mms-{lang}"

    tts = sherpa_onnx.OfflineTts(tts_config)
    audio = tts.generate(text, sid=0, speed=1.0)
    if not audio.samples:
        return False, ""
    _write_wav_int16(wav_path, audio.samples, audio.sample_rate)
    ok = wav_path.exists() and wav_path.stat().st_size > 44
    return ok, model_tag


def _run_windows_sapi(text: str, wav_path: Path, rate: int = 0) -> bool:
    """Use built-in Windows System.Speech via UTF-16LE EncodedCommand."""
    safe_text = text.replace("'", "''")
    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {rate}; "
        f"$s.SetOutputToWaveFile('{str(wav_path)}'); "
        f"$s.Speak('{safe_text}'); "
        "$s.Dispose()"
    )
    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=True,
        timeout=20,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return wav_path.exists() and wav_path.stat().st_size > 44


def synthesize_local_wav(
    text: str, lang: str = "en-IN", rate: int = 165, voice_gender: str = "female"
) -> Dict[str, Any]:
    """Generate a local WAV audio response using offline Kokoro/sherpa-onnx ONNX models or OS TTS fallback."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        raise ValueError("text is required")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    errors = []
    try:
        # 1. Primary: Offline Neural Kokoro-82M (en-IN) or VITS/MMS-TTS (Indian languages) via sherpa-onnx
        try:
            ok, model_tag = _run_sherpa_onnx(cleaned, lang, wav_path)
            if ok:
                data = wav_path.read_bytes()
                return {
                    "audioBase64": base64.b64encode(data).decode("ascii"),
                    "audioMimeType": "audio/wav",
                    "ttsModel": model_tag,
                    "voiceGender": voice_gender,
                }
        except Exception as exc:
            errors.append(f"sherpa-onnx: {exc}")

        # 2. Fallback: pyttsx3 with Windows COM init
        coinit = None
        try:
            if platform.system() == "Windows":
                try:
                    import comtypes  # type: ignore

                    comtypes.CoInitialize()
                    coinit = comtypes
                except Exception:
                    pass

            import pyttsx3  # type: ignore

            engine = pyttsx3.init()
            engine.setProperty("rate", rate)
            engine.save_to_file(cleaned, str(wav_path))
            engine.runAndWait()
            engine.stop()
            if wav_path.exists() and wav_path.stat().st_size > 44:
                data = wav_path.read_bytes()
                return {
                    "audioBase64": base64.b64encode(data).decode("ascii"),
                    "audioMimeType": "audio/wav",
                    "ttsModel": "local-pyttsx3",
                    "voiceGender": voice_gender,
                }
        except Exception as exc:
            errors.append(f"pyttsx3: {exc}")
        finally:
            if coinit is not None:
                try:
                    coinit.CoUninitialize()
                except Exception:
                    pass

        # 3. Windows built-in SAPI5 fallback
        if platform.system() == "Windows":
            try:
                if _run_windows_sapi(cleaned, wav_path):
                    data = wav_path.read_bytes()
                    return {
                        "audioBase64": base64.b64encode(data).decode("ascii"),
                        "audioMimeType": "audio/wav",
                        "ttsModel": "local-windows-sapi",
                        "voiceGender": voice_gender,
                    }
            except Exception as exc:
                errors.append(f"windows-sapi: {exc}")

        # 4. Linux espeak / espeak-ng fallback
        for bin_name in ("espeak-ng", "espeak"):
            try:
                subprocess.run(
                    [bin_name, "-w", str(wav_path), cleaned],
                    check=True,
                    timeout=15,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                if wav_path.exists() and wav_path.stat().st_size > 44:
                    data = wav_path.read_bytes()
                    return {
                        "audioBase64": base64.b64encode(data).decode("ascii"),
                        "audioMimeType": "audio/wav",
                        "ttsModel": f"local-{bin_name}",
                        "voiceGender": voice_gender,
                    }
            except Exception as exc:
                errors.append(f"{bin_name}: {exc}")

        raise RuntimeError("Local TTS unavailable: " + "; ".join(errors))
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass


async def synthesize_edge_tts(
    text: str,
    lang: str = "en-IN",
    voice_gender: str = "female",
) -> Dict[str, Any]:
    """Synthesize speech using Microsoft Edge Neural TTS with automatic fallback to local WAV."""
    gender = "male" if (voice_gender or "").strip().lower() == "male" else "female"
    cleaned = strip_markdown_and_symbols(text)
    if not cleaned:
        raise ValueError("text is required")

    try:
        import edge_tts  # type: ignore

        voice_name = await resolve_edge_voice(lang, gender)
        communicate = edge_tts.Communicate(cleaned, voice_name)
        chunks: List[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and "data" in chunk:
                chunks.append(chunk["data"])

        audio_bytes = b"".join(chunks)
        if not audio_bytes:
            raise RuntimeError(f"Edge TTS returned empty audio stream for {voice_name}")

        return {
            "audioBase64": base64.b64encode(audio_bytes).decode("ascii"),
            "audioMimeType": "audio/mpeg",
            "ttsModel": f"edge-tts-{voice_name}",
            "voiceGender": gender,
        }
    except Exception:
        # If offline, on error, or if locale is unsupported, automatically fall back to synthesize_local_wav
        fallback = await asyncio.to_thread(synthesize_local_wav, cleaned, lang, 165, gender)
        fallback["voiceGender"] = gender
        return fallback
