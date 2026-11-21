"""Download offline Kokoro-82M (English) and VITS/Piper/MMS-TTS ONNX voice models into ai_service/voices/<lang>/."""

import argparse
import asyncio
import io
import tarfile
from pathlib import Path
from typing import Dict, Optional

import httpx

VOICES_DIR = Path(__file__).resolve().parent / "voices"
GITHUB_RELEASE_API = "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/tts-models"

LANGUAGE_TO_MMS: Dict[str, str] = {
    "en-IN": "eng",
    "hi-IN": "hin",
    "mr-IN": "mar",
    "bn-IN": "ben",
    "or-IN": "ori",
    "pa-IN": "pan",
    "kn-IN": "kan",
    "ta-IN": "tam",
    "te-IN": "tel",
}

LANGUAGE_SEARCH_PATTERNS: Dict[str, tuple] = {
    "en-IN": (
        "kokoro-int8-en-v0_19",
        "kokoro-en-v0_19",
        "vits-piper-en_US-lessac-medium",
        "vits-mms-eng",
    ),
    "hi-IN": ("vits-piper-hi_IN", "vits-mms-hin"),
    "mr-IN": ("vits-piper-mr_IN", "vits-mms-mar"),
    "bn-IN": ("vits-piper-bn_IN", "vits-mms-ben"),
    "or-IN": ("vits-piper-or_IN", "vits-mms-ori"),
    "pa-IN": ("vits-piper-pa_IN", "vits-mms-pan"),
    "kn-IN": ("vits-piper-kn_IN", "vits-mms-kan"),
    "ta-IN": ("vits-piper-ta_IN", "vits-mms-tam"),
    "te-IN": ("vits-piper-te_IN", "vits-mms-tel"),
}


def _extract_tar_bz2(raw_bytes: bytes, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw_bytes), mode="r:bz2") as tf:
        for member in tf.getmembers():
            if member.isdir():
                continue
            parts = Path(member.name).parts
            rel = Path(*parts[1:]) if len(parts) > 1 else Path(member.name)
            if rel.is_absolute() or ".." in rel.parts:
                continue
            dest = target_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            src_file = tf.extractfile(member)
            if src_file is not None:
                dest.write_bytes(src_file.read())


async def download_voice(
    lang: str,
    voices_dir: Optional[Path] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Path:
    iso3 = LANGUAGE_TO_MMS.get(lang)
    if not iso3:
        raise ValueError(f"Unsupported language '{lang}'. Choose from: {', '.join(LANGUAGE_TO_MMS)}")

    target_dir = (voices_dir or VOICES_DIR) / lang
    target_dir.mkdir(parents=True, exist_ok=True)

    # Already downloaded?
    if list(target_dir.glob("*.onnx")) and (target_dir / "tokens.txt").exists():
        return target_dir

    patterns = LANGUAGE_SEARCH_PATTERNS.get(lang, (f"vits-mms-{iso3}",))

    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True, transport=transport) as client:
        # 1. Try dynamic lookup from official k2-fsa/sherpa-onnx tts-models GitHub release
        try:
            rel = await client.get(GITHUB_RELEASE_API, headers={"Accept": "application/vnd.github+json"})
            if rel.status_code == 200:
                assets = rel.json().get("assets") or []
                for pat in patterns:
                    match = next(
                        (a for a in assets if pat in a.get("name", "") and a.get("name", "").endswith(".tar.bz2")),
                        None,
                    )
                    if match and match.get("browser_download_url"):
                        archive_resp = await client.get(match["browser_download_url"])
                        archive_resp.raise_for_status()
                        _extract_tar_bz2(archive_resp.content, target_dir)
                        if list(target_dir.glob("*.onnx")):
                            return target_dir
        except Exception:
            pass

        # 2. Fallback: public community MMS-TTS ONNX repository on Hugging Face
        hf_base = f"https://huggingface.co/willwade/mms-tts-multilingual-models-onnx/resolve/main/onnx/{iso3}"
        for filename in ("model.onnx", "tokens.txt"):
            dest = target_dir / filename
            resp = await client.get(f"{hf_base}/{filename}")
            resp.raise_for_status()
            dest.write_bytes(resp.content)

    return target_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Download offline Kokoro & Indian language TTS models.")
    parser.add_argument("--lang", nargs="+", help="Language codes (e.g. hi-IN en-IN)")
    parser.add_argument("--all", action="store_true", help="Download all 9 supported languages")
    args = parser.parse_args()

    targets = list(LANGUAGE_TO_MMS.keys()) if args.all else (args.lang or ["en-IN", "hi-IN"])
    for lang in targets:
        print(f"Downloading offline TTS voice for {lang}...")
        path = asyncio.run(download_voice(lang))
        print(f" -> Saved to {path}")


if __name__ == "__main__":
    main()
