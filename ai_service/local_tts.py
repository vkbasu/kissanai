"""Lightweight, 100% local Python TTS audio generator (Kokoro-82M + sherpa-onnx VITS/MMS-TTS + OS fallbacks)."""

import base64
import platform
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Dict, Optional, Tuple

VOICES_DIR = Path(__file__).resolve().parent / "voices"


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
    text: str, lang: str = "en-IN", rate: int = 165
) -> Dict[str, str]:
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
                    }
            except Exception as exc:
                errors.append(f"{bin_name}: {exc}")

        raise RuntimeError("Local TTS unavailable: " + "; ".join(errors))
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass
