import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_chrome
import monitor


class ChromeHostControlTests(unittest.TestCase):
    def test_missing_host_never_leaves_a_command_for_future_startup(self):
        with tempfile.TemporaryDirectory() as folder,patch('local_chrome.STATE',Path(folder)):
            self.assertFalse(local_chrome.host_running())
            output=io.StringIO()
            with contextlib.redirect_stdout(output):local_chrome.command('close')
            self.assertEqual(json.loads(output.getvalue())['phase'],'host_not_running')
            self.assertFalse((Path(folder)/'command.json').exists())

    def test_existing_host_lock_is_detected_without_modifying_status(self):
        with tempfile.TemporaryDirectory() as folder,patch('local_chrome.STATE',Path(folder)):
            status=Path(folder)/'status.json';status.write_text('{"phase":"running"}',encoding='utf-8')
            with monitor.single_instance(Path(folder)/'host.lock'):
                self.assertTrue(local_chrome.host_running())
                self.assertEqual(status.read_text(encoding='utf-8'),'{"phase":"running"}')
