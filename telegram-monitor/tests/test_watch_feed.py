"""Веб-мониторинг: снимки changedetection.io превращаются в обычные заказы."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import feeds
import monitor
import watch_feed


class DiffTests(unittest.TestCase):
    def test_first_read_is_a_baseline_not_a_flood(self):
        self.assertEqual(watch_feed.added("", "Заказ на лендинг\nЕщё одна строка"), [])

    def test_only_new_lines_are_returned(self):
        previous = "Первый заказ\nВторой заказ\nТретий заказ"
        current = "Первый заказ\nВторой заказ\nЧетвёртый заказ: нужен сайт на Tilda\nТретий заказ"
        self.assertEqual(watch_feed.added(previous, current),
                         ["Четвёртый заказ: нужен сайт на Tilda"])

    def test_replaced_line_counts_as_new_text(self):
        self.assertEqual(watch_feed.added("Вакансия закрыта", "Тендер на разработку сайта"), ["Тендер на разработку сайта"])

    def test_reordering_alone_adds_nothing(self):
        self.assertEqual(watch_feed.added("а\nб\nв", "в\nа\nб"), [])

    def test_history_keys_survive_both_api_shapes(self):
        self.assertEqual(watch_feed.stamps({"1700000000": 1, "1700000100": 2}), [1700000000, 1700000100])
        self.assertEqual(watch_feed.stamps(["1700000000", 1700000050]), [1700000000, 1700000050])
        self.assertEqual(watch_feed.stamps(None), [])

    def test_snapshot_text_accepts_plain_and_wrapped_answers(self):
        self.assertEqual(watch_feed.snapshot_text("текст"), "текст")
        self.assertEqual(watch_feed.snapshot_text({"snapshot": "текст"}), "текст")
        self.assertEqual(watch_feed.snapshot_text({"unexpected": 1}), "")


class WatchSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = monitor.connect(Path(self.tmp.name) / "monitor.sqlite3")
        self.at = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)
        self.history: dict[str, list[str]] = {"1700000000": "Старая страница\nНужен сайт"}
        self.snapshot = "Старая страница\nНужен сайт"

        def call(path: str):
            if path == "/api/v1/watch":
                return {"w1": {"uuid": "aaaaaaaa-1111", "title": "Тендеры", "url": "https://example.invalid/t",
                               "paused": False, "tags": ["sites"]}}
            if path.endswith("/history"):
                return {ts: ts for ts in self.history}
            if "/history/" in path:
                return self.snapshot
            raise AssertionError(path)

        self.call = call

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_collect_stores_baseline_then_reports_only_new_blocks(self):
        self.assertEqual(watch_feed.collect(self.db, self.at, self.call, minimum=10), [])
        self.history["1700000100"] = "1700000100"
        self.snapshot = "Старая страница\nНужен сайт\nНужен лендинг на Tilda, бюджет 60 000"
        items = watch_feed.collect(self.db, self.at, self.call, minimum=10)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["feed"], "watch")
        self.assertEqual(item["link"], "https://example.invalid/t")
        self.assertIn("Нужен лендинг на Tilda", item["text"])
        self.assertIn("Тендеры", item["text"])
        self.assertEqual(item["published"].tzinfo, dt.timezone.utc)
        # Повторный сбор того же снимка новых блоков не даёт.
        self.assertEqual(watch_feed.collect(self.db, self.at, self.call, minimum=10), [])

    def test_short_noise_is_dropped(self):
        self.history["1700000000"] = "Старая страница\nНужен сайт"
        watch_feed.collect(self.db, self.at, self.call, minimum=10)
        self.history["1700000100"] = "1700000100"
        self.snapshot = "Старая страница\nНужен сайт\n+1"
        self.assertEqual(watch_feed.collect(self.db, self.at, self.call, minimum=10), [])

    def test_paused_watch_and_foreign_tag_are_skipped(self):
        paused = {"uuid": "bbbbbbbb-2222", "title": "Старое", "url": "https://example.invalid/p",
                  "paused": True, "tags": []}
        tagged = {"uuid": "cccccccc-3333", "title": "Чужое", "url": "https://example.invalid/q",
                  "paused": False, "tags": ["other"]}

        def call(path: str):
            if path == "/api/v1/watch":
                return [paused, tagged]
            raise AssertionError(path)

        with patch.dict("os.environ", {"MONITOR_WATCH_TAG": "sites"}):
            self.assertEqual(watch_feed.watches(call), [])

    def test_items_reach_the_common_queue_as_feed_watch(self):
        self.assertEqual(watch_feed.collect(self.db, self.at, self.call, minimum=10), [])  # базовый снимок
        self.history["1700000100"] = "1700000100"
        self.snapshot = "Старая страница\nНужен сайт\nНужен корпоративный сайт, есть макет Figma"
        items = watch_feed.collect(self.db, self.at, self.call, minimum=10)
        cfg = monitor.Config(self.db_path(), owner_id=42, feeds=("watch",))
        self.assertEqual(feeds.ingest(self.db, cfg, items, self.at), 1)
        row = self.db.execute("SELECT s.id,s.name,s.method FROM events e JOIN sources s ON s.id=e.source_id").fetchone()
        self.assertEqual(tuple(row), ("feed:watch", "Веб-мониторинг", "feed"))
        self.assertIn("feed:watch", monitor.allowed_sources(cfg))

    def db_path(self):
        return Path(self.tmp.name) / "monitor.sqlite3"


class RetiredFeedTests(unittest.TestCase):
    def test_kwork_runs_only_when_named_explicitly(self):
        run, skipped = feeds.active(["fl", "kwork", "connect"], explicit=False)
        self.assertEqual(run, ["fl", "connect"])
        self.assertEqual(skipped, ["kwork"])
        run, skipped = feeds.active(["kwork"], explicit=True)
        self.assertEqual((run, skipped), (["kwork"], []))

    def test_parser_and_tests_for_kwork_are_still_present(self):
        self.assertIn("kwork", feeds.FEEDS)
        self.assertTrue(callable(feeds.parse_kwork))

    def test_watch_is_a_known_feed_name(self):
        self.assertIn("watch", feeds.FEEDS)


if __name__ == "__main__":
    unittest.main()
