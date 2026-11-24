"""Отчёт по неделе читает базу только на чтение и переносит окно без потерь."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import audit_week
import monitor


def seed(db):
    db.execute("INSERT INTO sources(id,name,method) VALUES('feed:watch','Веб-мониторинг','feed')")
    db.execute("""INSERT INTO events(id,source_id,message_id,edit_version,published_at,received_at,link,body,expires_at)
                  VALUES(1,'feed:watch','m1','v1','2026-11-20T10:00:00+00:00','2026-11-20T10:00:00+00:00',
                         'https://example.invalid/1','Нужен лендинг на Tilda для курса','2026-12-20T10:00:00+00:00')""")
    db.execute("INSERT INTO candidates(id,event_id,kind,reason,priority) VALUES(1,1,'demand','тест','normal')")
    db.execute("""INSERT INTO deliveries(candidate_id,recipient,state,next_at,sent_at)
                  VALUES(1,42,'sent','2026-11-20T10:00:00+00:00','2026-11-20T10:01:00+00:00')""")
    db.commit()


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "monitor.sqlite3"
        self.db = monitor.connect(self.path)
        seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_report_counts_delivered_and_rejected(self):
        value = audit_week.report(self.db, dt.datetime(2026, 11, 19, tzinfo=dt.timezone.utc))
        self.assertEqual(value["events"], 1)
        self.assertEqual(len(value["delivered"]), 1)
        self.assertIn("Tilda", value["delivered"][0]["text"])
        self.assertEqual(value["by_source"][0]["name"], "Веб-мониторинг")

    def test_window_excludes_old_rows(self):
        value = audit_week.report(self.db, dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc))
        self.assertEqual(value["events"], 0)

    def test_export_copies_only_the_window_and_reads_back(self):
        out = Path(self.tmp.name) / "week.sqlite3"
        counts = audit_week.export(self.db, dt.datetime(2026, 11, 19, tzinfo=dt.timezone.utc), out)
        self.assertEqual((counts["events"], counts["candidates"], counts["deliveries"]), (1, 1, 1))
        copy = monitor.connect(out)
        try:
            value = audit_week.report(copy, dt.datetime(2026, 11, 19, tzinfo=dt.timezone.utc))
        finally:
            copy.close()
        self.assertEqual(len(value["delivered"]), 1)


if __name__ == "__main__":
    unittest.main()
