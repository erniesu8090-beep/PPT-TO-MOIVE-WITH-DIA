import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from job_control import JobControl, child_checkpoint
from project_import import PROJECT_PATHS, import_project


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / 'core'
        self.workspace.mkdir()
        for relative in PROJECT_PATHS:
            path = self.workspace / relative
            if relative.startswith('assets/'):
                path.mkdir(parents=True)
                (path / 'original.txt').write_text('old', encoding='utf-8')
            else:
                path.write_text('old', encoding='utf-8')
        with app.fitz.open() as doc:
            doc.new_page()
            self.pdf = base64.b64encode(doc.tobytes()).decode()
        self.payload = dict(slide_pdf=self.pdf, slide_pdf_name='new.pdf', narration_data='測試口白')

    def run_import(self):
        return import_project(self.workspace, self.payload, app.render_uploaded_slides,
                              app.parse_and_save_narration, app.calculate_dynamic_timings,
                              app.decode_base64_file, app.extract_text_from_pdf_bytes)

    def assert_old(self):
        for relative in PROJECT_PATHS:
            path = self.workspace / relative
            if relative.startswith('assets/'):
                path = path / 'original.txt'
            self.assertEqual(path.read_text(encoding='utf-8'), 'old')

    def test_invalid_script_preserves_existing_project(self):
        self.payload.update(narration_type='pdf', narration_data=base64.b64encode(b'broken').decode())
        with self.assertRaises(Exception):
            self.run_import()
        self.assert_old()

    def test_success_retains_previous_project_and_new_timings(self):
        count, backup = self.run_import()
        self.assertEqual(count, 1)
        self.assertEqual((Path(backup) / 'project_info.json').read_text(), 'old')
        self.assertTrue((self.workspace / 'assets/images/page-01.png').is_file())
        self.assertEqual(json.loads((self.workspace / 'timings.json').read_text(encoding='utf-8'))[0]['audio_status'], 'estimated')

    def test_publish_failure_rolls_back_already_replaced_files(self):
        original = Path.replace
        def replace(source, target):
            if source.name == 'timings.json' and source.parent.name.startswith('import-'):
                raise OSError('simulated disk failure')
            return original(source, target)
        with patch.object(Path, 'replace', replace), self.assertRaisesRegex(OSError, 'simulated'):
            self.run_import()
        self.assert_old()

    def test_duplicate_or_out_of_range_narration_does_not_replace_project(self):
        for script in ('【第1頁】第一段【第1頁】重複頁碼', '【第2頁】超過頁數'):
            self.payload['narration_data'] = script
            with self.assertRaises(ValueError):
                self.run_import()
            self.assert_old()


class PauseTests(unittest.TestCase):
    def test_pause_blocks_checkpoint_and_resume_releases_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = JobControl()
            job.start(Path(tmp) / 'pause')
            job.request(True)
            done = threading.Event()
            thread = threading.Thread(target=lambda: (job.checkpoint(), done.set()))
            thread.start()
            try:
                self.assertFalse(done.wait(0.1))
                self.assertTrue(job.snapshot()['paused'])
                job.request(False)
                self.assertTrue(done.wait(2))
            finally:
                job.finish()
                thread.join(timeout=2)
            self.assertFalse(job.snapshot()['active'])

    def test_child_boundary_waits_and_pending_pause_can_be_withdrawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = JobControl()
            marker = Path(tmp) / 'pause'
            job.start(marker)
            job.request(True)
            done = threading.Event()
            thread = threading.Thread(target=lambda: (child_checkpoint(marker), done.set()))
            thread.start()
            try:
                self.assertFalse(done.wait(0.1))
                job.request(False)
                self.assertTrue(done.wait(2))
                self.assertFalse(marker.exists())
            finally:
                job.finish()
                thread.join(timeout=2)

    def test_inactive_job_rejects_pause(self):
        with self.assertRaises(ValueError):
            JobControl().request(True)
