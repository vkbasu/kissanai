import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from ai_service.config import load_model_specs
from ai_service.download_voices import LANGUAGE_TO_MMS, download_voice
from ai_service.local_tts import resolve_edge_voice, synthesize_edge_tts
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
            self.assertIn(data["audioMimeType"], ("audio/mpeg", "audio/wav", None))
            self.assertEqual(len(data["webSources"]), 1)
        finally:
            os.environ.pop("GEMINI_API_KEY", None)


class TestEdgeNeuralTtsAndVoiceGender(unittest.TestCase):
    def test_edge_neural_voice_gender_selection(self):
        cases = [
            ("hi-IN", "female", "hi-IN-SwaraNeural"),
            ("hi-IN", "male", "hi-IN-MadhurNeural"),
            ("en-IN", "female", "en-IN-NeerjaNeural"),
            ("en-IN", "male", "en-IN-PrabhatNeural"),
            ("mr-IN", "female", "mr-IN-AarohiNeural"),
            ("mr-IN", "male", "mr-IN-ManoharNeural"),
            ("ta-IN", "female", "ta-IN-PallaviNeural"),
            ("ta-IN", "male", "ta-IN-ValluvarNeural"),
            ("te-IN", "female", "te-IN-ShrutiNeural"),
            ("te-IN", "male", "te-IN-MohanNeural"),
            ("kn-IN", "female", "kn-IN-SapnaNeural"),
            ("kn-IN", "male", "kn-IN-GaganNeural"),
            ("bn-IN", "female", "bn-IN-TanishaaNeural"),
            ("bn-IN", "male", "bn-IN-BashkarNeural"),
            ("gu-IN", "female", "gu-IN-DhwaniNeural"),
            ("gu-IN", "male", "gu-IN-NiranjanNeural"),
            ("ml-IN", "female", "ml-IN-SobhanaNeural"),
            ("ml-IN", "male", "ml-IN-MidhunNeural"),
        ]
        for locale, gender, expected_voice in cases:
            selected = asyncio.run(resolve_edge_voice(locale, gender))
            self.assertEqual(selected, expected_voice, f"Failed for {locale} ({gender})")

    def test_edge_tts_fallback_to_local_wav(self):
        with patch("ai_service.local_tts.synthesize_local_wav") as mock_local:
            mock_local.return_value = {
                "audioBase64": "UklGRiQAAABXQVZF",
                "audioMimeType": "audio/wav",
                "ttsModel": "local-windows-sapi",
                "voiceGender": "female",
            }
            with patch("ai_service.local_tts.resolve_edge_voice", side_effect=RuntimeError("Network offline")):
                res = asyncio.run(synthesize_edge_tts("नमस्ते किसान भाइयों", "hi-IN", "female"))
                self.assertEqual(res["audioMimeType"], "audio/wav")
                self.assertEqual(res["voiceGender"], "female")
                mock_local.assert_called_once()

    def test_index_html_files_are_identical(self):
        base_dir = Path(__file__).resolve().parent.parent
        root_index = base_dir / "index.html"
        dir_index = base_dir / "index" / "index.html"
        public_index = base_dir / "public" / "index.html"

        self.assertTrue(root_index.exists(), "Root index.html missing")
        self.assertTrue(dir_index.exists(), "index/index.html missing")
        self.assertTrue(public_index.exists(), "public/index.html missing")

        h_root = hashlib.sha256(root_index.read_bytes()).hexdigest()
        h_dir = hashlib.sha256(dir_index.read_bytes()).hexdigest()
        h_public = hashlib.sha256(public_index.read_bytes()).hexdigest()

        self.assertEqual(h_root, h_dir, "root index.html does not match index/index.html")
        self.assertEqual(h_root, h_public, "root index.html does not match public/index.html")


if __name__ == "__main__":
    unittest.main()
