import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from ai_service.config import get_active_models, load_model_specs, set_active_models
from ai_service.local_tts import synthesize_edge_tts, synthesize_local_wav
from ai_service.sync_models import sync_free_models
from app.config import get_gemini_api_key, load_env
from app.live_fetcher import DynamicSourceStore, LiveFetcher
from app.voice_ai import VoiceAssistant, resolve_speech_lang

load_env()

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCES_PATH = BASE_DIR / "data" / "sources.json"
CACHE_PATH = BASE_DIR / "data" / "live_cache.json"
DOWNLOADS_DIR = BASE_DIR / "data" / "downloads"
PUBLIC_DIR = BASE_DIR / "public"


def _build_live_portal_fallback_reply(
    sites: List[Dict[str, Any]], query: str, state: str
) -> str:
    """Graceful live-portal response if Gemini hits 429 rate limits across all models."""
    q_words = [w for w in (query or "").lower().split() if len(w) > 3]
    if state:
        q_words.append(state.lower())

    highlights = []
    for s in sites:
        if s.get("status") != "ok":
            continue
        hay = f"{s.get('name', '')} {' '.join(n.get('text', '') for n in s.get('notices', []))}".lower()
        if not q_words or any(w in hay for w in q_words):
            notices = s.get("notices") or []
            if notices:
                highlights.append(f"{s['name']}: {notices[0]['text']}")
            else:
                highlights.append(f"{s['name']} ({s['url']})")
        if len(highlights) >= 3:
            break

    if not highlights:
        highlights = [
            f"{s['name']} ({s['url']})" for s in sites if s.get("status") == "ok"
        ][:3]

    if highlights:
        return (
            "AI rate limit is temporarily busy, so here is the latest live data fetched directly from official portals: "
            + " | ".join(highlights)
        )
    return "AI rate limit is temporarily busy. Please wait a few seconds and try again."


def create_app(
    live_fetcher: Optional[LiveFetcher] = None,
    voice_assistant: Optional[VoiceAssistant] = None,
    enable_background_sync: Optional[bool] = None,
) -> FastAPI:
    store = DynamicSourceStore(SOURCES_PATH, CACHE_PATH)
    fetcher = live_fetcher or LiveFetcher(store=store, downloads_dir=DOWNLOADS_DIR)
    assistant = voice_assistant or VoiceAssistant()
    sync_interval = float(os.getenv("LIVE_SYNC_INTERVAL_SECONDS", "900"))
    if enable_background_sync is None:
        enable_background_sync = os.getenv("DISABLE_LIVE_SYNC", "") != "1"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if enable_background_sync and sync_interval > 0:
            async def _sync_loop():
                while True:
                    try:
                        await fetcher.refresh_all(force=False, crawl_subpages=True)
                    except Exception:
                        pass
                    await asyncio.sleep(sync_interval)

            task = asyncio.create_task(_sync_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(title="Smart Farmer Live Assistant", version="4.3.0", lifespan=lifespan)

    @app.get("/")
    async def index():
        return FileResponse(PUBLIC_DIR / "index.html")

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "savedSources": len(fetcher.store.list_sources()),
            "cachedSites": len(fetcher.store.all_cached()),
        }

    @app.get("/api/voice-status")
    async def voice_status():
        active = get_active_models()
        return {
            "aiConfigured": bool(get_gemini_api_key()),
            "model": active["chatModel"],
            "ttsModel": active["ttsModel"],
            "liveModel": active["liveModel"],
            "voiceGender": active.get("voiceGender", "female"),
        }

    @app.get("/api/ai/models")
    async def get_ai_models():
        return load_model_specs()

    @app.post("/api/ai/models/sync")
    async def sync_ai_models():
        api_key = get_gemini_api_key()
        if not api_key:
            return JSONResponse(status_code=400, content={"error": "GEMINI_API_KEY is not set in env/.env"})
        try:
            return await sync_free_models(api_key=api_key)
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Model sync failed: {exc}"})

    @app.post("/api/ai/models/select")
    async def select_ai_models(request: Request):
        try:
            body: Dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Request body must be valid JSON"})

        return set_active_models(
            chat_model=body.get("chatModel"),
            tts_model=body.get("ttsModel"),
            live_model=body.get("liveModel"),
            voice_gender=body.get("voiceGender"),
        )

    @app.get("/api/sources")
    async def list_sources(q: str = Query(default="")):
        query = (q or "").strip().lower()
        cached_map = {s["id"]: s for s in fetcher.store.all_cached()}
        items = []
        for src in fetcher.store.list_sources():
            merged = cached_map.get(src["id"]) or {
                "id": src["id"],
                "name": src["name"],
                "url": src["url"],
                "status": "pending",
                "notices": [],
                "crawledLinks": [],
            }
            if query:
                hay = " ".join(
                    [
                        merged.get("name", ""),
                        merged.get("title", ""),
                        merged.get("url", ""),
                        " ".join(n.get("text", "") for n in merged.get("notices", [])),
                    ]
                ).lower()
                if query not in hay:
                    continue
            items.append(merged)
        return {"count": len(items), "sources": items}

    @app.post("/api/sources")
    async def add_source(request: Request):
        try:
            body: Dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Request body must be valid JSON"})

        url = str(body.get("url") or "").strip()
        name = str(body.get("name") or "").strip()
        entry = fetcher.store.add_source(url, name)
        if not entry:
            return JSONResponse(status_code=400, content={"error": "Valid https:// url is required"})

        snap = await fetcher.fetch_source(entry, force=True, crawl_subpages=True)
        return {"source": snap}

    @app.get("/api/live/status")
    async def live_status():
        sites = fetcher.store.all_cached()
        return {
            "totalSources": len(fetcher.store.list_sources()),
            "checkedCount": len(sites),
            "online": sum(1 for s in sites if s.get("status") == "ok"),
            "sites": sites,
            "notifications": fetcher.get_notifications(limit=25),
        }

    @app.post("/api/live/refresh")
    async def live_refresh(request: Request):
        try:
            body: Dict[str, Any] = await request.json()
        except Exception:
            body = {}
        force = body.get("force", True) is not False
        crawl_subpages = body.get("crawlSubpages", True) is not False
        download_files = body.get("downloadFiles", True) is not False
        return await fetcher.refresh_all(force=force, crawl_subpages=crawl_subpages, download_files=download_files)

    @app.get("/api/notifications")
    async def get_notifications(limit: int = Query(default=20, ge=1, le=50)):
        items = fetcher.get_notifications(limit=limit)
        return {"count": len(items), "notifications": items}

    @app.post("/api/tts")
    async def tts(request: Request):
        try:
            body: Dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Request body must be valid JSON"})

        text = str(body.get("text") or "").strip()
        if not text:
            return JSONResponse(status_code=400, content={"error": "text is required"})

        active = get_active_models()
        voice_gender = str(body.get("voiceGender") or active.get("voiceGender") or "female").strip().lower()
        if voice_gender not in ("female", "male"):
            voice_gender = "female"
        lang = str(body.get("language") or body.get("lang") or "en-IN").strip()
        tts_model = str(body.get("ttsModel") or active.get("ttsModel") or "edge-tts").strip()

        try:
            if tts_model == "local-pyttsx3":
                return await asyncio.to_thread(synthesize_local_wav, text, lang, 165, voice_gender)
            return await synthesize_edge_tts(text=text, lang=lang, voice_gender=voice_gender)
        except Exception:
            try:
                return await asyncio.to_thread(synthesize_local_wav, text, lang, 165, voice_gender)
            except Exception as inner_exc:
                return JSONResponse(status_code=502, content={"error": f"TTS failed: {inner_exc}"})

    @app.post("/api/voice-assist")
    async def voice_assist(request: Request):
        try:
            body: Dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Request body must be valid JSON"})

        audio_base64 = str(body.get("audioBase64") or "").strip()
        mime_type = str(body.get("mimeType") or "audio/webm").strip()
        transcript = str(body.get("transcript") or "").strip()
        language_hint = str(body.get("language") or "").strip()
        history = body.get("history") if isinstance(body.get("history"), list) else []
        current_profile = body.get("currentProfile") if isinstance(body.get("currentProfile"), dict) else {}
        speak_enabled = body.get("speak", True) is not False
        active = get_active_models()
        voice_gender = str(body.get("voiceGender") or active.get("voiceGender") or "female").strip().lower()
        if voice_gender not in ("female", "male"):
            voice_gender = "female"
        interview_mode = bool(body.get("interviewMode", False))
        requested_model = body.get("chatModel") or None
        requested_tts = body.get("ttsModel") or active.get("ttsModel") or "edge-tts"

        if not audio_base64 and not transcript:
            return JSONResponse(status_code=400, content={"error": "Provide audioBase64 or transcript"})

        api_key = get_gemini_api_key()
        if not api_key:
            return JSONResponse(
                status_code=400,
                content={"error": "GEMINI_API_KEY is not set in env/.env. Run py setup_env.py first."},
            )

        await fetcher.refresh_all(force=False, crawl_subpages=True)
        cached_sites = fetcher.store.all_cached()

        used_model = requested_model or active["chatModel"]
        grounded_urls = []
        try:
            farmer, speech_text, grounded_urls, used_model = await assistant.process_turn_single_call(
                live_sites=cached_sites,
                audio_base64=audio_base64,
                mime_type=mime_type,
                transcript=transcript,
                language_hint=language_hint,
                current_profile=current_profile,
                history=history,
                interview_mode=interview_mode,
                model=requested_model,
                api_key=api_key,
            )
        except Exception:
            # Graceful fallback to live-crawled portal data if all Gemini models hit 429
            farmer = dict(current_profile)
            speech_text = _build_live_portal_fallback_reply(
                cached_sites, transcript, str(farmer.get("state") or "")
            )
            used_model = "live-portal-fallback"

        speech_lang = resolve_speech_lang(language_hint or farmer.get("language", "en-IN"))

        newly_fetched = []
        if grounded_urls:
            try:
                newly_fetched = await fetcher.ingest_and_fetch_urls(grounded_urls[:3])
            except Exception:
                newly_fetched = []

        audio_out: Optional[Dict[str, Any]] = None
        tts_error: Optional[str] = None
        if speak_enabled and speech_text:
            try:
                if requested_tts == "local-pyttsx3":
                    audio_out = await asyncio.to_thread(synthesize_local_wav, speech_text, speech_lang, 165, voice_gender)
                else:
                    audio_out = await synthesize_edge_tts(
                        text=speech_text,
                        lang=speech_lang,
                        voice_gender=voice_gender,
                    )
            except Exception as exc:
                try:
                    audio_out = await asyncio.to_thread(synthesize_local_wav, speech_text, speech_lang, 165, voice_gender)
                except Exception as inner_exc:
                    audio_out = None
                    tts_error = str(inner_exc)

        return {
            "aiUsed": used_model != "live-portal-fallback",
            "model": used_model,
            "ttsModel": audio_out["ttsModel"] if audio_out else None,
            "liveModel": active["liveModel"],
            "voiceGender": audio_out.get("voiceGender", voice_gender) if audio_out else voice_gender,
            "extractedProfile": farmer,
            "speechText": speech_text,
            "speechLang": speech_lang,
            "audioBase64": audio_out["audioBase64"] if audio_out else None,
            "audioMimeType": audio_out["audioMimeType"] if audio_out else None,
            "ttsError": tts_error,
            "webSources": grounded_urls,
            "newlySavedSites": newly_fetched,
            "liveSites": fetcher.store.all_cached(),
            "notifications": fetcher.get_notifications(limit=15),
        }

    return app


app = create_app()


def run() -> None:
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "3000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
