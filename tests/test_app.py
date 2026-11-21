import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ai_service.config import load_model_specs
from ai_service.download_voices import LANGUAGE_TO_MMS, download_voice
from ai_service.sync_models import sync_free_models
from app.config import load_env
from app.context_index import retrieve_relevant_context
from app.live_fetcher import DynamicSourceStore, LiveFetcher, extract_page_details
from app.main import create_app
from app.voice_ai import VoiceAssistant
from scripts.fetch_files import download_and_parse_files
from scripts.fetch_portals import extract_portal_components


class TestEnvConfigAndAiService(unittest.TestCase):
    def test_load_env_only_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('GEMINI_API_KEY="test-secret-123"\n', encoding="utf-8")
            os.environ.pop("GEMINI_API_KEY", None)
            load_env(env_file)
            self.assertEqual(os.environ.get("GEMINI_API_KEY"), "test-secret-123")
            os.environ.pop("GEMINI_API_KEY", None)

    def test_sync_free_models_excludes_computer_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_path = Path(tmp) / "models.json"

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "models/gemini-2.5-computer-use-preview-10-2025",
                                "displayName": "Computer Use",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/gemini-3.5-flash-lite",
                                "displayName": "Gemini 3.5 Flash-Lite",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/gemini-3.8-flash-lite-tts",
                                "displayName": "Gemini 3.8 Flash-Lite TTS",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/gemini-3.8-live",
                                "displayName": "Gemini 3.8 Live",
                                "supportedGenerationMethods": ["bidiGenerateContent"],
                            },
                        ]
                    },
                )

            specs = asyncio.run(
                sync_free_models(
                    api_key="dummy-key",
                    models_path=models_path,
                    transport=httpx.MockTransport(handler),
                )
            )
            ids = [m["id"] for m in specs["availableModels"]]
            self.assertIn("gemini-3.5-flash-lite", ids)
            self.assertNotIn("gemini-2.5-computer-use-preview-10-2025", ids)
            self.assertEqual(load_model_specs(models_path)["active"]["chatModel"], "gemini-3.5-flash-lite")



    def test_download_offline_voice_files(self):
        self.assertEqual(len(LANGUAGE_TO_MMS), 9)
        with tempfile.TemporaryDirectory() as tmp:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"fake-onnx-data")
            out = asyncio.run(download_voice("hi-IN", voices_dir=Path(tmp), transport=httpx.MockTransport(handler)))
            self.assertTrue((out / "model.onnx").exists())
            self.assertTrue((out / "tokens.txt").exists())


class TestScriptsAndContextIndex(unittest.TestCase):
    def test_extract_portal_components_sections_tables_and_files(self):
        html = """
        <html>
          <head><title>PM-KISAN Portal</title></head>
          <body>
            <h2>Eligibility Criteria</h2>
            <p>Landholding farmer families with cultivable land can apply online.</p>
            <table>
              <tr><th>Installment</th><th>Amount</th></tr>
              <tr><td>Annual Support</td><td>Rs 6,000</td></tr>
            </table>
            <div class="notice-board">
              <ul>
                <li><a href="/ekyc">Complete Aadhaar e-KYC before 30 November 2026 for 19th installment</a></li>
                <li><a href="/docs/guidelines.pdf">Download Official Guidelines PDF</a></li>
              </ul>
            </div>
          </body>
        </html>
        """
        parsed = extract_portal_components(html, "https://pmkisan.gov.in")
        self.assertEqual(parsed["title"], "PM-KISAN Portal")
        self.assertEqual(len(parsed["sections"]), 1)
        self.assertEqual(parsed["sections"][0]["heading"], "Eligibility Criteria")
        self.assertEqual(len(parsed["tables"]), 2)
        self.assertEqual(len(parsed["fileLinks"]), 1)
        self.assertEqual(parsed["fileLinks"][0]["url"], "https://pmkisan.gov.in/docs/guidelines.pdf")

    def test_download_and_parse_txt_file_and_retrieve_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl_dir = Path(tmp) / "downloads"

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"Odisha CM-KISAN small farmer wheat subsidy circular 2026.")

            async def _run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    return await download_and_parse_files(
                        source_id="odisha",
                        file_links=[{"text": "Odisha Circular", "url": "https://krushak.odisha.gov.in/circular.txt"}],
                        downloads_dir=dl_dir,
                        client=client,
                    )

            files = asyncio.run(_run())
            self.assertEqual(len(files), 1)
            self.assertIn("Odisha CM-KISAN", files[0]["textExcerpt"])

            ctx = retrieve_relevant_context(
                [{"name": "Krushak Odisha", "url": "https://krushak.odisha.gov.in", "status": "ok", "files": files, "notices": []}],
                query="wheat subsidy",
                state="Odisha",
            )
            self.assertEqual(len(ctx), 1)
            self.assertTrue(len(ctx[0]["files"]) == 1)


class TestDynamicFastAPIEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        sources_path = Path(self.tmp.name) / "sources.json"
        cache_path = Path(self.tmp.name) / "live_cache.json"
        dl_dir = Path(self.tmp.name) / "downloads"
        sources_path.write_text(
            json.dumps([{"id": "pm-kisan", "name": "PM-KISAN", "url": "https://pmkisan.gov.in"}]),
            encoding="utf-8",
        )

        def portal_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text='<title>Official Portal</title><p class="notice">Live 2026 notification for farmers.</p>',
            )

        def gemini_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            gen_cfg = body.get("generationConfig") or {}
            if gen_cfg.get("responseModalities") == ["AUDIO"]:
                return httpx.Response(
                    200,
                    json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/wav", "data": "UklGRiQAAABXQVZF"}}]}}]},
                )
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "profile": {"ownership": "owner", "acres": 3.0, "state": "Karnataka", "district": "Mandya", "crop": "sugarcane", "irrigationType": "canal", "age": 40, "language": "kn-IN"},
                                                "reply": "Dynamic live answer grounded in portal sections and files.",
                                            }
                                        )
                                    }
                                ]
                            },
                            "groundingMetadata": {
                                "groundingChunks": [
                                    {"web": {"uri": "https://raitamitra.karnataka.gov.in", "title": "Raita Mitra Karnataka"}}
                                ]
                            },
                        }
                    ]
                },
            )

        store = DynamicSourceStore(sources_path, cache_path)
        fetcher = LiveFetcher(store=store, downloads_dir=dl_dir, transport=httpx.MockTransport(portal_handler))
        assistant = VoiceAssistant(transport=httpx.MockTransport(gemini_handler))
        app = create_app(live_fetcher=fetcher, voice_assistant=assistant, enable_background_sync=False)
        self.client = TestClient(app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ai_models_and_voice_assist(self):
        os.environ["GEMINI_API_KEY"] = "dummy-key"
        try:
            res = self.client.post("/api/voice-assist", json={"transcript": "Sugarcane farmer in Mandya Karnataka"})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["extractedProfile"]["state"], "Karnataka")
            self.assertIn(data["audioMimeType"], ("audio/wav", None))
            self.assertEqual(len(data["webSources"]), 1)
        finally:
            os.environ.pop("GEMINI_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
