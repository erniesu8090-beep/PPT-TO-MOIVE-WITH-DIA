import asyncio
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

import app
import media_utils
import render
from subtitle_utils import split_timed_text, wrap_ass_lines, ass_text, format_srt_time, format_ass_time


class DurationTests(unittest.TestCase):
    def test_invalid_durations_and_missing_tools_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'audio.mp3'
            path.write_bytes(b'audio')
            for raw in ('N/A', 'NaN', 'Infinity', 0, -1):
                with self.subTest(raw=raw), patch.object(media_utils.subprocess, 'run', return_value=Mock(
                        returncode=0, stdout=json.dumps({'format': {'duration': raw}}))):
                    with self.assertRaisesRegex(RuntimeError, '音檔時長無效'):
                        media_utils.get_audio_duration(path)
            for exception, message in [(FileNotFoundError(), '找不到 ffprobe'),
                                       (subprocess.TimeoutExpired('ffprobe', 30), '逾時')]:
                with patch.object(media_utils.subprocess, 'run', side_effect=exception):
                    with self.assertRaisesRegex(RuntimeError, message):
                        media_utils.get_audio_duration(path)

    def test_valid_duration_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'audio.mp3'
            path.write_bytes(b'audio')
            with patch.object(media_utils.subprocess, 'run', return_value=Mock(
                    returncode=0, stdout='{"format":{"duration":"12.345"}}')):
                self.assertEqual(media_utils.get_audio_duration(path), 12.345)


class SubtitleTests(unittest.TestCase):
    def test_timestamp_rounding_carries_into_next_minute(self):
        self.assertEqual(format_srt_time(59.9999), '00:01:00,000')
        self.assertEqual(format_ass_time(59.9999), '0:01:00.00')

    def test_dialogue_parser_retains_each_speakers_text(self):
        self.assertEqual(app.parse_dialogue_turns('A: 你好。 B: 歡迎。'),
                         [{'speaker': 'A', 'text': '你好。'}, {'speaker': 'B', 'text': '歡迎。'}])
        self.assertEqual(app.parse_dialogue_turns('A: '), [])

    def test_long_unpunctuated_text_has_bounded_lines_and_time(self):
        events = split_timed_text('這是一段沒有標點的中文測試' * 20, 1.25, 91.25, prefix='[主持人]')
        self.assertEqual(events[0]['start'], 1.25)
        self.assertEqual(events[-1]['end'], 91.25)
        self.assertEqual(sum('[主持人]' in e['text'] for e in events), 1)
        for previous, current in zip(events, events[1:]):
            self.assertAlmostEqual(previous['end'], current['start'])
        for event in events:
            self.assertLessEqual(event['end'] - event['start'], 5.000001)
            lines = wrap_ass_lines(event['text']).split(r'\N')
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(line) <= 18 for line in lines))

    def test_leading_punctuation_and_literal_tags_are_preserved(self):
        text = '，測試，句子。結束！'
        events = split_timed_text(text, 0, 4)
        self.assertEqual(''.join(e['text'] for e in events), text)
        self.assertEqual(ass_text(r'{\pos(0,0)}'), '｛＼pos(0,0)｝')

    def test_render_and_preview_generate_same_captions(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'OUT_DIR', Path(tmp)):
            settings = {'mode': 'single', 'speaker_a': {'name': '主持人'}, 'speaker_b': {'name': '來賓'}}
            text = 'A: ' + '這是一段需要分割的中文內容' * 5 + '。 B: 這是下一位的發言。'
            preview = app.subtitle_preview({'num': 1, 'narration': text, 'voice_settings': settings})
            raw = []
            cursor = 0
            for turn in app.tts_plan(text, settings)['turns']:
                duration = max(1, len(turn['text']) * .15)
                raw.append({'text': turn['text'], 'start': cursor, 'end': cursor+duration,
                            'speaker': turn['speaker'], 'speaker_name': settings['speaker_' + turn['speaker'].lower()]['name']})
                cursor += duration
            Path(tmp, 'timings_dynamic.json').write_text(json.dumps([{'dur': cursor, 'clauses': raw}]), encoding='utf-8')
            Path(tmp, 'voice_settings.json').write_text(json.dumps(settings), encoding='utf-8')
            try:
                os.chdir(tmp)
                srt, ass = render.generate_subtitles({})
            finally:
                os.chdir(original_cwd)
            rendered = [line.split(',', 9)[9].replace(r'\N', '\n') for line in ass.splitlines() if line.startswith('Dialogue:')]
            self.assertEqual(rendered, [e['text'] for e in preview['cues']])
            self.assertIn('WrapStyle: 2', ass)
            self.assertTrue(rendered)
            self.assertIn(',422,422,50,1', ass)


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.out_patch = patch.object(app, 'OUT_DIR', self.directory)
        self.out_patch.start()
        self.addCleanup(self.out_patch.stop)
        self.duration_patch = patch.object(app, 'get_audio_duration', return_value=2.0)
        self.duration_patch.start()
        self.addCleanup(self.duration_patch.stop)
        self.collect = AsyncMock(return_value=(b'audio', [{'text': '測試。', 'start': 0, 'end': 2}]))
        self.collect_patch = patch.object(app, 'collect_tts_stream', self.collect)
        self.collect_patch.start()
        self.addCleanup(self.collect_patch.stop)

    async def test_cache_ignores_visual_changes_but_invalidates_voice_or_text(self):
        await app.generate_page_tts(1, '測試。', {})
        await app.generate_page_tts(1, '測試。', {'pad_time': 2, 'speaker_a': {'name': '新名稱', 'color': '#fff'}})
        self.assertEqual(self.collect.await_count, 1)
        await app.generate_page_tts(1, '測試。', {'rate': '+5%'})
        await app.generate_page_tts(1, '新口白。', {'rate': '+5%'})
        self.assertEqual(self.collect.await_count, 3)

    async def test_force_and_corrupted_audio_regenerate(self):
        audio = await app.generate_page_tts(1, '測試。', {})
        await app.generate_page_tts(1, '測試。', {}, force=True)
        audio.write_bytes(b'corrupt')
        await app.generate_page_tts(1, '測試。', {})
        self.assertEqual(self.collect.await_count, 3)

    async def test_failed_regeneration_keeps_previous_good_audio_and_metadata(self):
        audio = await app.generate_page_tts(1, '測試。', {})
        previous_meta = (self.directory / 'page-01-meta.json').read_bytes()
        self.collect.side_effect = RuntimeError('network failure')
        with patch.object(app.asyncio, 'sleep', AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, 'network failure'):
                await app.generate_page_tts(1, '新口白。', {})
        self.assertEqual(audio.read_bytes(), b'audio')
        self.assertEqual((self.directory / 'page-01-meta.json').read_bytes(), previous_meta)
        self.assertFalse(app.audio_matches_cache(1, '新口白。', {}))

    async def test_silent_page_does_not_call_tts_or_reuse_old_speech(self):
        await app.generate_page_tts(1, '測試。', {})
        self.collect.reset_mock()

        def synthesize(cmd, **kwargs):
            self.assertIn('anullsrc=r=24000:cl=mono', cmd)
            Path(cmd[-1]).write_bytes(b'silence')
            return Mock(returncode=0)

        with patch.object(app.subprocess, 'run', side_effect=synthesize):
            audio = await app.generate_page_tts(1, ' ', {'silent_page_duration': 7})
        self.collect.assert_not_awaited()
        self.assertEqual(audio.read_bytes(), b'silence')
        meta = json.loads((self.directory / 'page-01-meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['mode'], 'silent')
        self.assertEqual(meta['exact_clauses'], [])
        self.assertFalse(app.audio_matches_cache(1, '', {'silent_page_duration': 8}))

    async def test_empty_page_ignores_stale_audio_and_has_no_captions(self):
        await app.generate_page_tts(1, '測試。', {})
        (self.directory / '口白_cleaned.json').write_text('[{"num":1,"narration":""}]', encoding='utf-8')
        (self.directory / 'voice_settings.json').write_text('{"silent_page_duration":7}', encoding='utf-8')
        with patch.object(app, 'WORKSPACE_DIR', self.directory):
            timing = app.calculate_dynamic_timings()[0]
        self.assertTrue(timing['silent'])
        self.assertEqual(timing['dur'], 7)
        self.assertEqual(timing['clauses'], [])


class LockTests(unittest.TestCase):
    def test_writes_and_second_render_reject_busy_project(self):
        server = app.http.server.ThreadingHTTPServer(('127.0.0.1', 0), app.GUIHTTPRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        app.PROJECT_LOCK.acquire()
        try:
            for path in ('/api/upload_project', '/api/test_tts', '/api/save_slide', '/api/save_voice_settings'):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                conn.request('POST', path, '{}', {'Content-Type': 'application/json'})
                response = conn.getresponse()
                self.assertEqual(response.status, 409)
                self.assertFalse(json.loads(response.read())['success'])
                conn.close()
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            conn.request('GET', '/api/render_video')
            response = conn.getresponse()
            self.assertIn('error', response.read().decode())
            conn.close()
            # Read-only caption calculation remains available during rendering.
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            conn.request('POST', '/api/subtitle_preview', json.dumps({'narration': '測試'}), {'Content-Type': 'application/json'})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())['cues'])
            conn.close()
        finally:
            app.PROJECT_LOCK.release()
            server.shutdown()
            server.server_close()
            thread.join()
