import os
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_DIR = BASE_DIR / "env"


def _parse_env_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def load_env(env_path: Optional[Path] = None) -> None:
    """Load API keys from env/.env (or fallback files in env/)."""
    if env_path is not None:
        _parse_env_file(env_path)
        return

    candidates = [
        ENV_DIR / ".env",
        ENV_DIR / ".env.txt",
        ENV_DIR / ".env.example",
    ]
    if ENV_DIR.exists():
        for extra in sorted(ENV_DIR.iterdir()):
            if extra.is_file() and extra not in candidates:
                candidates.append(extra)

    for candidate in candidates:
        _parse_env_file(candidate)


def get_gemini_api_key() -> str:
    """Return the Google AI Studio API key from env/ (never expose to client or prompts)."""
    return os.environ.get("GEMINI_API_KEY", "").strip()
