import json
from pathlib import Path
from typing import Any, Dict, List, Optional

AI_SERVICE_DIR = Path(__file__).resolve().parent
MODELS_FILE = AI_SERVICE_DIR / "models.json"
EXCLUDED_FALLBACK_HINTS = ("computer-use", "deep-research", "customtools", "image", "transcribe")

DEFAULT_SPEC: Dict[str, Any] = {
    "provider": "google-ai-studio",
    "syncedAt": None,
    "active": {
        "chatModel": "gemini-4-hydrogen",
        "ttsModel": "edge-tts",
        "liveModel": "gemini-3.8-live",
        "voiceGender": "female",
    },
    "availableModels": [],
}


def load_model_specs(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or MODELS_FILE
    if not target.exists():
        return json.loads(json.dumps(DEFAULT_SPEC))
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if "active" not in data:
            data["active"] = dict(DEFAULT_SPEC["active"])
        if "availableModels" not in data:
            data["availableModels"] = []
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_SPEC))


def save_model_specs(specs: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or MODELS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(specs, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_active_models(path: Optional[Path] = None) -> Dict[str, str]:
    specs = load_model_specs(path)
    active = specs.get("active") or {}
    return {
        "chatModel": str(active.get("chatModel") or DEFAULT_SPEC["active"]["chatModel"]),
        "ttsModel": str(active.get("ttsModel") or DEFAULT_SPEC["active"]["ttsModel"]),
        "liveModel": str(active.get("liveModel") or DEFAULT_SPEC["active"]["liveModel"]),
        "voiceGender": str(active.get("voiceGender") or DEFAULT_SPEC["active"].get("voiceGender", "female")),
    }


def set_active_models(
    chat_model: Optional[str] = None,
    tts_model: Optional[str] = None,
    live_model: Optional[str] = None,
    voice_gender: Optional[str] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    specs = load_model_specs(path)
    active = specs.setdefault("active", {})
    if chat_model:
        active["chatModel"] = chat_model.strip()
    if tts_model:
        active["ttsModel"] = tts_model.strip()
    if live_model:
        active["liveModel"] = live_model.strip()
    if voice_gender:
        active["voiceGender"] = voice_gender.strip()
    save_model_specs(specs, path)
    return specs


def get_fallback_chat_models(primary: Optional[str] = None, path: Optional[Path] = None) -> List[str]:
    specs = load_model_specs(path)
    active_chat = primary or specs["active"].get("chatModel") or "gemini-4-hydrogen"
    candidates = [active_chat]
    for m in specs.get("availableModels") or []:
        mid = m.get("id", "")
        if (
            m.get("category") == "chat"
            and mid
            and mid not in candidates
            and not any(h in mid.lower() for h in EXCLUDED_FALLBACK_HINTS)
        ):
            candidates.append(mid)
    for fallback in ("gemini-4-hydrogen", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"):
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates[:4]
