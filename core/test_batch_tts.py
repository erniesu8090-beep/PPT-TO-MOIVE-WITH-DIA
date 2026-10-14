import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class BatchTtsTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_page_at_a_time_and_include_silent_pages(self):
        active = peak = 0
        completed = []

        async def generate(num, text, settings):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            completed.append((num, text))
            active -= 1

        slides = [{"num": n, "narration": "「hello」"} for n in range(8)]
        slides.append({"num": 99, "narration": "  "})
        with patch.object(app, "generate_page_tts", generate):
            await app.generate_batch_tts(slides, {}, lambda msg: None)
        self.assertEqual(peak, 1)
        self.assertEqual(sorted(completed), [(n, "hello") for n in range(8)] + [(99, "")])

    async def test_failure_does_not_cancel_remaining_pages(self):
        completed = []

        async def generate(num, text, settings):
            if num == 2:
                raise RuntimeError("service error")
            await asyncio.sleep(0.01)
            completed.append(num)

        with patch.object(app, "generate_page_tts", generate):
            with self.assertRaisesRegex(RuntimeError, "配音失敗頁碼：2"):
                await app.generate_batch_tts(
                    [{"num": n, "narration": "hello"} for n in range(6)],
                    {}, lambda msg: None)
        self.assertEqual(sorted(completed), [0, 1, 3, 4, 5])

    async def test_stream_preserves_audio_and_boundary_and_closes_connector(self):
        connectors = []

        class Communicate:
            def __init__(self, *args, connector, **kwargs):
                connectors.append(connector)

            async def stream(self):
                yield {"type": "audio", "data": b"audio"}
                yield {"type": "SentenceBoundary", "text": "hello",
                       "offset": 10000000, "duration": 20000000}

        with patch.object(app.edge_tts, "Communicate", Communicate):
            audio, boundaries = await app.collect_tts_stream("hello", "voice", {})
        self.assertEqual(audio, b"audio")
        self.assertEqual(boundaries, [{"text": "hello", "start": 1.0, "end": 3.0}])
        self.assertTrue(connectors[0].closed)

    async def test_connector_survives_sessions_for_retry_and_text_chunks(self):
        import aiohttp

        connectors = []
        test = self

        class Communicate:
            def __init__(self, *args, connector, **kwargs):
                self.connector = connector
                connectors.append(connector)

            async def stream(self):
                # Match edge-tts: each retry/chunk owns a new ClientSession.
                for _ in range(3):
                    async with aiohttp.ClientSession(connector=self.connector) as session:
                        test.assertFalse(session.closed)
                        yield {"type": "audio", "data": b"chunk"}
                    test.assertFalse(self.connector.closed)

        with patch.object(app.edge_tts, "Communicate", Communicate):
            audio, _ = await app.collect_tts_stream("hello", "voice", {})
        self.assertEqual(audio, b"chunkchunkchunk")
        self.assertTrue(connectors[0].closed)

    async def test_stream_failure_closes_connector(self):
        import aiohttp

        connectors = []

        class Communicate:
            def __init__(self, *args, connector, **kwargs):
                self.connector = connector
                connectors.append(connector)

            async def stream(self):
                async with aiohttp.ClientSession(connector=self.connector):
                    raise RuntimeError("service error")
                    yield

        with patch.object(app.edge_tts, "Communicate", Communicate):
            with self.assertRaisesRegex(RuntimeError, "service error"):
                await app.collect_tts_stream("hello", "voice", {})
        self.assertTrue(connectors[0].closed)

    async def test_no_audio_identifies_voice_and_closes_connector(self):
        connectors = []

        class Communicate:
            def __init__(self, *args, connector, **kwargs):
                connectors.append(connector)

            async def stream(self):
                raise app.edge_tts.exceptions.NoAudioReceived("No audio")
                yield

        with patch.object(app.edge_tts, "Communicate", Communicate):
            with self.assertRaisesRegex(RuntimeError, "音色 zh-TW-YunJheNeural"):
                await app.collect_tts_stream("hello", "zh-TW-YunJheNeural", {})
        self.assertTrue(connectors[0].closed)

    async def test_timeout_closes_connector(self):
        connectors = []

        class Communicate:
            def __init__(self, *args, connector, **kwargs):
                connectors.append(connector)

            async def stream(self):
                await asyncio.sleep(10)
                yield {"type": "audio", "data": b"audio"}

        with patch.object(app.edge_tts, "Communicate", Communicate), \
                patch.object(app, "TTS_REQUEST_TIMEOUT", 0.01):
            with self.assertRaisesRegex(RuntimeError, "Edge-TTS"):
                await app.collect_tts_stream("hello", "voice", {})
        self.assertTrue(connectors[0].closed)

    async def test_403_explains_recovery_and_omits_request_url(self):
        import aiohttp

        connectors = []

        class Communicate:
            def __init__(self, *args, connector, **kwargs):
                connectors.append(connector)

            async def stream(self):
                raise aiohttp.WSServerHandshakeError(
                    None, (), status=403, message="Invalid response status")
                yield

        with patch.object(app.edge_tts, "Communicate", Communicate):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403") as error:
                await app.collect_tts_stream("hello", "voice", {})
        self.assertIn("7.3.1", str(error.exception))
        self.assertIn("重新啟動", str(error.exception))
        self.assertNotIn("wss://", str(error.exception))
        self.assertTrue(connectors[0].closed)

    async def test_parallel_page_files_and_subtitle_metadata(self):
        async def collect(text, voice, kwargs):
            await asyncio.sleep(0.01)
            return text.encode(), [{"text": text, "start": 0.0, "end": 2.0}]

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(app, "OUT_DIR", Path(tmp)), \
                patch.object(app, "collect_tts_stream", collect), \
                patch.object(app, "get_audio_duration", return_value=2.0):
            await app.generate_batch_tts(
                [{"num": 1, "narration": "第一頁。"},
                 {"num": 2, "narration": "第二頁。"}],
                {"mode": "single"}, lambda msg: None)
            for num, text in [(1, "第一頁。"), (2, "第二頁。")]:
                self.assertEqual((Path(tmp) / f"page-{num:02d}.mp3").read_bytes(), text.encode())
                meta = json.loads((Path(tmp) / f"page-{num:02d}-meta.json").read_text(encoding="utf-8"))
                self.assertEqual(meta["exact_clauses"][0]["text"], text)
                self.assertEqual(meta["exact_clauses"][0]["start"], 0.0)

    async def test_dual_endpoint_failover_succeeds_on_secondary(self):
        attempted_urls = []

        class FlakyCommunicate:
            def __init__(self, *args, **kwargs):
                import edge_tts.communicate as comm
                attempted_urls.append(getattr(comm, "WSS_URL"))

            async def stream(self):
                # Fail on legacy URL, succeed on new URL
                import edge_tts.communicate as comm
                if "bing.com" in getattr(comm, "WSS_URL", ""):
                    raise RuntimeError("Legacy endpoint blocked (HTTP 403)")
                yield {"type": "audio", "data": b"secondary_audio"}
                yield {"type": "SentenceBoundary", "text": "test", "offset": 0, "duration": 10000000}

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(app, "OUT_DIR", Path(tmp)), \
                patch.object(app.edge_tts, "Communicate", FlakyCommunicate), \
                patch.object(app, "get_audio_duration", return_value=1.0):
            out = await app.generate_page_tts(1, "test", {"mode": "single"})
            self.assertTrue(out.is_file())
            self.assertEqual(out.read_bytes(), b"secondary_audio")
            # Verified that both endpoints were attempted
            self.assertGreaterEqual(len(attempted_urls), 2)


if __name__ == "__main__":
    unittest.main()
