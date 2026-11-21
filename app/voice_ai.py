import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ai_service.config import get_active_models, get_fallback_chat_models
from ai_service.local_tts import synthesize_edge_tts, synthesize_local_wav
from app.config import get_gemini_api_key
from app.context_index import retrieve_relevant_context

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

LANGUAGE_TO_BCP47 = {
    "en": "en-IN",
    "english": "en-IN",
    "hi": "hi-IN",
    "hindi": "hi-IN",
    "mr": "mr-IN",
    "marathi": "mr-IN",
    "bn": "bn-IN",
    "bengali": "bn-IN",
    "or": "or-IN",
    "odia": "or-IN",
    "pa": "pa-IN",
    "punjabi": "pa-IN",
    "kn": "kn-IN",
    "kannada": "kn-IN",
    "ta": "ta-IN",
    "tamil": "ta-IN",
    "te": "te-IN",
    "telugu": "te-IN",
}


def resolve_speech_lang(lang_hint: str) -> str:
    cleaned = (lang_hint or "").strip().lower()
    if "-" in cleaned and len(cleaned) <= 8:
        return lang_hint
    return LANGUAGE_TO_BCP47.get(cleaned, "en-IN")


class VoiceAssistant:
    """Single-call, rate-limit-resilient AI assistant powered by live portal/PDF context + Google Search."""

    def __init__(
        self,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout_seconds: float = 25.0,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    async def _post_with_retry(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            for attempt in range(max_retries + 1):
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (429, 503) and attempt < max_retries:
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else (1.5 * (attempt + 1))
                    await asyncio.sleep(min(delay, 5.0))
                    continue
                resp.raise_for_status()
                return resp.json()
        raise RuntimeError("Request failed after retries")

    async def _call_gemini_raw(
        self,
        parts: List[Dict[str, Any]],
        response_mime_type: str = "text/plain",
        temperature: float = 0.45,
        use_google_search: bool = False,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, str]], str]:
        key = api_key if api_key is not None else get_gemini_api_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set in env/.env")

        models_to_try = get_fallback_chat_models(primary=model)

        last_err: Optional[Exception] = None
        for idx, model_name in enumerate(models_to_try):
            search_flags = [True, False] if (use_google_search and idx == 0) else [use_google_search]
            for use_search in search_flags:
                url = f"{GEMINI_API_BASE}/{model_name}:generateContent"
                payload: Dict[str, Any] = {
                    "contents": [{"role": "user", "parts": parts}],
                    "generationConfig": {"temperature": temperature},
                }
                if response_mime_type == "application/json" and not use_search:
                    payload["generationConfig"]["responseMimeType"] = "application/json"
                if use_search:
                    payload["tools"] = [{"google_search": {}}]

                try:
                    data = await self._post_with_retry(
                        url,
                        headers={"Content-Type": "application/json", "x-goog-api-key": key},
                        payload=payload,
                    )
                    candidates = data.get("candidates") or []
                    if not candidates:
                        raise RuntimeError("Gemini returned no candidates")
                    cand = candidates[0]
                    content_parts = (cand.get("content") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in content_parts).strip()

                    web_sources: List[Dict[str, str]] = []
                    grounding = cand.get("groundingMetadata") or {}
                    for chunk in grounding.get("groundingChunks") or []:
                        web = chunk.get("web") or {}
                        uri = str(web.get("uri") or "").strip()
                        title = str(web.get("title") or "").strip()
                        if uri.startswith("https://"):
                            web_sources.append({"url": uri, "name": title or uri})
                    return text, web_sources, model_name
                except httpx.HTTPStatusError as exc:
                    last_err = exc
                    if exc.response.status_code not in (429, 400, 404, 503):
                        raise
                except Exception as exc:
                    last_err = exc

        if last_err:
            raise last_err
        raise RuntimeError("Gemini request failed")

    async def synthesize_speech(
        self,
        text: str,
        api_key: Optional[str] = None,
        tts_model: Optional[str] = None,
        voice_name: str = "Kore",
        language: str = "en-IN",
        voice_gender: str = "female",
    ) -> Dict[str, Any]:
        """Generate audio using Edge Neural TTS (with fallback to local WAV) or direct local WAV."""
        active = get_active_models()
        chosen_model = tts_model or active.get("ttsModel") or "edge-tts"
        gender = voice_gender or active.get("voiceGender") or "female"
        if chosen_model == "local-pyttsx3":
            return await asyncio.to_thread(synthesize_local_wav, text, language, 165, gender)
        try:
            return await synthesize_edge_tts(text=text, lang=language, voice_gender=gender)
        except Exception:
            return await asyncio.to_thread(synthesize_local_wav, text, language, 165, gender)

    async def process_turn_single_call(
        self,
        live_sites: List[Dict[str, Any]],
        audio_base64: str = "",
        mime_type: str = "audio/webm",
        transcript: str = "",
        language_hint: str = "",
        current_profile: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        interview_mode: bool = False,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str, List[Dict[str, str]], str]:
        """Handle audio/text + retrieved live portal/PDF context + Google Search in ONE Gemini call."""
        cur = current_profile or {}
        portal_context = retrieve_relevant_context(
            live_sites,
            query=transcript,
            state=str(cur.get("state") or ""),
            crop=str(cur.get("crop") or ""),
            top_k=5,
        )

        prompt = (
            "You are a warm, knowledgeable Indian agricultural advisor. "
            "Do TWO things in ONE JSON response:\n"
            "1. Extract or update any farm details from the farmer's latest message into `profile`.\n"
            "2. Answer using the retrieved LIVE official portal sections, tables, notices, and downloaded PDF excerpts below "
            "(plus Google Search for anything not covered locally across both STATE and NATIONAL levels). "
            "Write a dynamic, helpful, conversational `reply` (3 to 4 plain-text sentences, no markdown/asterisks) "
            f"strictly in language '{language_hint or 'same language as farmer'}'. "
            "If interview_mode is true, ask one short follow-up question to collect missing farm details.\n\n"
            "Return ONLY valid JSON with this exact shape:\n"
            '{"profile": {"ownership": "", "acres": null, "state": "", "district": "", "crop": "", "irrigationType": "", "age": null, "language": "en-IN"}, "reply": "..."}\n\n'
            f"Interview mode: {interview_mode}\n"
            f"Current profile: {json.dumps(cur, ensure_ascii=False)}\n"
            f"Recent chat: {json.dumps((history or [])[-6:], ensure_ascii=False)}\n"
            f"Retrieved live website & PDF context:\n{json.dumps(portal_context, ensure_ascii=False)}"
        )

        parts: List[Dict[str, Any]] = [{"text": prompt}]
        if audio_base64:
            parts.append({"inline_data": {"mime_type": mime_type or "audio/webm", "data": audio_base64}})
        elif transcript:
            parts.append({"text": f"Farmer message: {transcript}"})
        else:
            raise ValueError("Either audioBase64 or transcript is required")

        raw, web_sources, used_model = await self._call_gemini_raw(
            parts,
            temperature=0.5,
            use_google_search=True,
            api_key=api_key,
            model=model,
        )

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").lstrip("json").strip()

        try:
            parsed = json.loads(cleaned)
            prof = parsed.get("profile") or {}
            reply = str(parsed.get("reply") or "").strip() or raw
        except Exception:
            prof = {}
            reply = raw

        merged_profile = {
            "ownership": str(prof.get("ownership") or cur.get("ownership") or "").strip().lower(),
            "acres": prof.get("acres") if prof.get("acres") is not None else cur.get("acres"),
            "state": str(prof.get("state") or cur.get("state") or "").strip(),
            "district": str(prof.get("district") or cur.get("district") or "").strip(),
            "crop": str(prof.get("crop") or cur.get("crop") or "").strip(),
            "irrigationType": str(prof.get("irrigationType") or cur.get("irrigationType") or "").strip(),
            "age": prof.get("age") if prof.get("age") is not None else cur.get("age"),
            "language": resolve_speech_lang(str(prof.get("language") or language_hint or "en-IN")),
        }

        return merged_profile, reply, web_sources, used_model
