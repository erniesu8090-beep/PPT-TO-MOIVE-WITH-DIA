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
import render


class RenderFailureTests(unittest.TestCase):
    def test_tts_failure_sends_terminal_error_and_closes_stream(self):
        server = app.http.server.ThreadingHTTPServer(('127.0.0.1', 0), app.GUIHTTPRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(app, 'generate_batch_tts', AsyncMock(side_effect=RuntimeError('TTS failed'))):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    connection.request('GET', '/api/render_video')
                    response = connection.getresponse()
                    body = response.read().decode('utf-8')
                    self.assertEqual(response.getheader('Connection'), 'close')
                    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith('data: ')]
                    self.assertEqual(events[-1], {'error': 'TTS failed'})
                    self.assertFalse(any(event.get('complete') for event in events))
                    self.assertFalse(app.PROJECT_LOCK.locked())
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def run_in_sandbox(self, *, empty_subtitles=False, fail_burn=False, silent=False):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = root / 'core'
            core.mkdir()
            (core / 'timings.json').write_text(json.dumps([{'i':1,'dur':3,'silent':silent}]), encoding='utf-8')
            clauses = [] if empty_subtitles else [{'text': '測試字幕。', 'start': 0, 'end': 2}]
            (core / 'timings_dynamic.json').write_text(json.dumps([{'dur': 3, 'clauses': clauses}]), encoding='utf-8')
            (core / 'subtitle_settings.json').write_text('{"mode": "both"}', encoding='utf-8')

            def run(cmd, **kwargs):
                if fail_burn and 'ass=temp_subtitles.ass' in cmd:
                    raise subprocess.CalledProcessError(1, cmd)
                return Mock(returncode=0)

            try:
                os.chdir(core)
                with patch.object(render, '__file__', str(core / 'render.py')), \
                        patch.object(render.shutil, 'which', return_value='node'), \
                        patch.object(render.subprocess, 'run', side_effect=run):
                    render.run_render()
            finally:
                os.chdir(original_cwd)

    def test_silent_project_does_not_require_subtitles(self):
        self.run_in_sandbox(empty_subtitles=True, silent=True)

    def test_burn_failure_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, '壓制內嵌字幕影片失敗'):
            self.run_in_sandbox(fail_burn=True)

    def test_missing_subtitles_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, '缺少可用字幕'):
            self.run_in_sandbox(empty_subtitles=True)


if __name__ == '__main__':
    unittest.main()
