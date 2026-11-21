"""Fix and sync env/.env from any file in the env/ folder (API keys only)."""

import getpass
from pathlib import Path
from typing import Optional

from app.config import get_gemini_api_key, load_env

BASE_DIR = Path(__file__).resolve().parent
ENV_DIR = BASE_DIR / "env"


def _extract_key_from_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "GEMINI_API_KEY":
            cleaned = v.strip().strip('"').strip("'")
            if cleaned and "your_google_ai_studio" not in cleaned.lower():
                return cleaned
    return ""


def repair_env_file(env_dir: Optional[Path] = None, api_key: str = "") -> Path:
    """Ensure env/.env exists and contains only API keys."""
    target_dir = env_dir or ENV_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    env_file = target_dir / ".env"
    env_txt = target_dir / ".env.txt"
    env_example = target_dir / ".env.example"

    if not env_file.exists() and env_txt.exists():
        env_txt.rename(env_file)
        print(f"Renamed {env_txt.name} -> {env_file.name}")

    if not env_file.exists():
        if env_example.exists():
            env_file.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            env_file.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
        print(f"Created {env_file}")

    found_key = api_key.strip().strip('"').strip("'") or _extract_key_from_file(env_file)
    if not found_key:
        for candidate in sorted(target_dir.iterdir()):
            if candidate.is_file() and candidate != env_file:
                found_key = _extract_key_from_file(candidate)
                if found_key:
                    print(f"Found GEMINI_API_KEY in {candidate.name} -> synced to .env")
                    break

    if found_key:
        env_file.write_text(f"GEMINI_API_KEY={found_key}\n", encoding="utf-8")

    return env_file


def main() -> None:
    env_file = repair_env_file()
    load_env(env_file)

    if not get_gemini_api_key():
        print("GEMINI_API_KEY is still empty in env/.")
        entered = getpass.getpass("Paste your Google AI Studio API key (input hidden, press Enter): ")
        if entered.strip():
            repair_env_file(api_key=entered)
            load_env(env_file)

    if get_gemini_api_key():
        print("OK: env/.env is configured and GEMINI_API_KEY is loaded.")
        print("Optional: run 'py -m ai_service.sync_models' to fetch all free models.")
        print("Now run: py -m app")
    else:
        print("WARNING: GEMINI_API_KEY is still empty.")


if __name__ == "__main__":
    main()
