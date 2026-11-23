import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pilot
import runtime_status as health


class RuntimeStatusTests(unittest.TestCase):
    def test_abnormal_exit_records_type_without_exception_contents(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(health, "STATE", Path(directory)):
            with self.assertRaises(ValueError):
                with health.lifecycle("reader"):
                    raise ValueError("private-message-placeholder")
            status = json.loads((Path(directory) / "reader-health.json").read_text())
            journal = (Path(directory) / "lifecycle.jsonl").read_text()
            self.assertEqual((status["state"], status["error_type"]), ("error", "ValueError"))
            self.assertIn('"action": "exited"', journal)
            self.assertNotIn("private-message-placeholder", journal)

    def test_network_health_requires_recent_check_by_current_process(self):
        now = dt.datetime(2026, 1, 15, 9, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.object(pilot, "STATE", Path(directory)), \
                patch.object(pilot, "processes", return_value=[17]), patch("monitor.now", return_value=now):
            path = Path(directory) / "reader-health.json"
            for pid, seconds_ago, state, expected in (
                (17, 10, "connected", True), (17, 100, "connected", False),
                (18, 10, "connected", False), (17, 10, "retrying", False),
            ):
                path.write_text(json.dumps({"pid": pid, "state": state,
                                           "last_network_ok": (now - dt.timedelta(seconds=seconds_ago)).isoformat()}))
                self.assertEqual(pilot.worker_status("reader")["network_confirmed_recently"], expected)

    def test_dead_process_is_not_reported_healthy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pilot, "STATE", Path(directory)), \
                patch.object(pilot, "processes", return_value=[]):
            result = pilot.worker_status("reader")
            self.assertFalse(result["running"])
            self.assertFalse(result["network_confirmed_recently"])


if __name__ == "__main__":
    unittest.main()
