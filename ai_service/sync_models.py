"""Fetch all free-tier Gemini models from Google AI Studio and save specs to ai_service/models.json."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ai_service.config import MODELS_FILE, load_model_specs, save_model_specs
from app.config import get_gemini_api_key, load_env

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

PAID_ONLY_HINTS = ("pro", "ultra", "carbon", "argon", "veo", "imagen", "lyria", "robotics")
SKIP_HINTS = (
    "embedding",
    "aqa",
    "bisect",
    "computer-use",
    "deep-research",
    "customtools",
    "image",
    "transcribe",
    "live-translate",
    "nano-banana",
)
PREFERRED_CHAT_ORDER = (
    "gemini-4-hydrogen",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
)


def classify_model(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Filter and classify a Google AI Studio model entry into chat, tts, or live."""
    full_name = str(raw.get("name") or "")
    model_id = full_name.split("/")[-1].strip()
    if not model_id or "gemini" not in model_id.lower():
        return None

    lower = model_id.lower()
    if any(skip in lower for skip in SKIP_HINTS):
        return None

    if any(paid in lower for paid in PAID_ONLY_HINTS) and "flash" not in lower:
        return None

    methods: List[str] = raw.get("supportedGenerationMethods") or []
    if "tts" in lower:
        category = "tts"
    elif "live" in lower or "bidiGenerateContent" in methods:
        category = "live"
    elif "generateContent" in methods:
        category = "chat"
    else:
        return None

    return {
        "id": model_id,
        "displayName": str(raw.get("displayName") or model_id),
        "category": category,
        "freeTier": True,
        "supportedMethods": methods,
    }


def _pick_preferred(ids: List[str], preferred_order: tuple) -> str:
    for pref in preferred_order:
        if pref in ids:
            return pref
    return ids[0] if ids else ""


async def sync_free_models(
    api_key: Optional[str] = None,
    models_path: Optional[Path] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Dict[str, Any]:
    """Query Google AI Studio /v1beta/models and update ai_service/models.json."""
    load_env()
    key = api_key if api_key is not None else get_gemini_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set in env/.env")

    async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
        resp = await client.get(
            GEMINI_MODELS_URL,
            params={"pageSize": 100},
            headers={"x-goog-api-key": key},
        )
        resp.raise_for_status()
        payload = resp.json()

    classified: List[Dict[str, Any]] = []
    for raw in payload.get("models") or []:
        entry = classify_model(raw)
        if entry:
            classified.append(entry)

    specs = load_model_specs(models_path)
    specs["syncedAt"] = datetime.now(timezone.utc).isoformat()
    if classified:
        local_tts = {"id": "local-pyttsx3", "displayName": "Local Python TTS (Offline)", "category": "tts", "freeTier": True, "supportedMethods": ["local"]}
        specs["availableModels"] = [local_tts] + [m for m in classified if m["category"] != "tts"]
        chat_ids = [m["id"] for m in classified if m["category"] == "chat"]
        tts_ids = [m["id"] for m in classified if m["category"] == "tts"]
        live_ids = [m["id"] for m in classified if m["category"] == "live"]

        if specs["active"].get("chatModel") not in chat_ids and chat_ids:
            specs["active"]["chatModel"] = _pick_preferred(chat_ids, PREFERRED_CHAT_ORDER)
        if not specs["active"].get("ttsModel"):
            specs["active"]["ttsModel"] = _pick_preferred(tts_ids, ("gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"))
        if specs["active"].get("liveModel") not in live_ids and live_ids:
            specs["active"]["liveModel"] = _pick_preferred(live_ids, ("gemini-3.8-live",))

    save_model_specs(specs, models_path)
    return specs


def main() -> None:
    specs = asyncio.run(sync_free_models())
    print(f"Synced {len(specs['availableModels'])} free-tier models to {MODELS_FILE}")
    print("Active models:", specs["active"])


if __name__ == "__main__":
    main()
