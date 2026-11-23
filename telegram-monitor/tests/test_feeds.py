import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import feeds
import monitor

FL = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>FL</title>
<item><title><![CDATA[Дизайн сайта для клиники на Tilda (Бюджет: 40 000 &#8381;)]]></title>
<link>https://www.fl.ru/projects/5528801/dizayn-sayta.html</link>
<description><![CDATA[Нужен дизайн и сборка лендинга на Tilda для стоматологии, 5 блоков, есть тексты.]]></description>
<category><![CDATA[Сайты / Дизайн сайтов]]></category><pubDate>Tue, 10 Nov 2026 15:51:56 GMT</pubDate></item>
<item><title>bad link</title><link>https://www.fl.ru/users/x</link><pubDate>Tue, 10 Nov 2026 15:51:56 GMT</pubDate></item>
</channel></rss>"""

WS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>WS</title>
<item><title>Доработка действующего сайта завода.</title>
<link>https://workspace.ru/tenders/dorabotka-sayta-19131/</link>
<description>&lt;b&gt;Организатор&lt;/b&gt; : компания Бюджет : от 100 000 до 400 000 руб Описание проекта : нужен редизайн</description>
<pubDate>Tue, 10 Nov 2026 10:47:36 +0300</pubDate></item></channel></rss>"""

KWORK = 'x{"wants":[{"id":3272629,"name":"Создать копию сайта","description":"Нужна главная и форма заявки",' \
        '"priceLimit":"15000.00","date_create":"2026-11-10 17:43:04","user":{"username":"analitik1"}}],"pagination":{}}'


def connect_page(orders, texts):
    flight = '18:["$","main",null,{"initialOrders":' + json.dumps(orders, ensure_ascii=False) + '}]'
    for ref, body in texts.items():
        flight = f"{ref}:T{len(body.encode('utf-8')):x}," + body + flight
    chunk = json.dumps(flight, ensure_ascii=False)[1:-1]
    return '<script>self.__next_f.push([1,"' + chunk + '"])</script>'


class FeedParserTests(unittest.TestCase):
    def test_fl_extracts_id_budget_and_category(self):
        items = feeds.parse_fl(FL)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["id"], "5528801")
        self.assertIn("Бюджет: 40 000 ₽", it["text"])
        self.assertNotIn("(Бюджет", it["text"].splitlines()[0])
        self.assertIn("Раздел FL.ru: Сайты / Дизайн сайтов", it["text"])
        self.assertEqual(it["published"].tzinfo is not None, True)

    def test_workspace_strips_html_and_keeps_budget(self):
        it = feeds.parse_workspace(WS)[0]
        self.assertEqual(it["id"], "19131")
        self.assertIn("от 100 000 до 400 000 руб", it["text"])
        self.assertNotIn("<b>", it["text"])

    def test_kwork_reads_embedded_list_with_buyer_profile(self):
        it = feeds.parse_kwork(KWORK)[0]
        self.assertEqual((it["id"], it["link"]), ("3272629", "https://kwork.ru/projects/3272629"))
        self.assertEqual(it["author"], "kwork.ru/user/analitik1")
        self.assertEqual(it["published"].utcoffset(), dt.timedelta(hours=3))
        with self.assertRaises(ValueError):
            feeds.parse_kwork("<html>captcha</html>")

    def test_connect_keeps_only_telegram_orders_and_takes_contact_from_reply_text(self):
        orders = [
            {"slug": "a-1", "title": "Лендинг на Tilda", "description": "$1b", "source": "Telegram",
             "postedAt": "2026-11-10T12:12:09Z", "budgetMin": 20000, "budgetMax": 40000, "budgetEstimated": False},
            {"slug": "k-2", "title": "Сайт стоматологии", "description": "Kwork", "source": "Kwork",
             "postedAt": "2026-11-10T12:11:04Z"},
        ]
        page = connect_page(orders, {"1b": "Нужен лендинг. Пишите @connectexbot\n\nКак откликнуться: в личку @Client_One или t.me/other"})
        items = feeds.parse_connect(page)
        self.assertEqual([x["id"] for x in items], ["a-1"])
        self.assertEqual(items[0]["author"], "@Client_One")
        self.assertIn("Бюджет: 20000–40000 ₽", items[0]["text"])
        self.assertTrue(items[0]["link"].endswith("/orders/a-1"))

    def test_fresh_drops_old_items(self):
        at = dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc)
        items = [{"published": at - dt.timedelta(hours=h)} for h in (1, 30)]
        self.assertEqual(len(feeds.fresh(items, at, 24)), 1)


class FeedPipelineTests(unittest.TestCase):
    def test_feed_items_enter_shared_queue_once_and_are_delivered_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = monitor.Config(Path(directory) / "m.sqlite3", owner_id=42, feeds=("fl",))
            db = monitor.connect(cfg.db)
            at = monitor.now()
            item = feeds.item("fl", "1", "Нужен сайт на Tilda для салона красоты",
                              "Ищем исполнителя: дизайн и сборка лендинга на Tilda, бюджет 30 000 ₽, срок неделя.",
                              at - dt.timedelta(minutes=5), "https://www.fl.ru/projects/1/x.html")
            self.assertEqual(feeds.ingest(db, cfg, [item], at), 1)
            self.assertEqual(feeds.ingest(db, cfg, [item], at), 0)
            row = db.execute("SELECT s.name,s.method,e.link FROM events e JOIN sources s ON s.id=e.source_id").fetchone()
            self.assertEqual(tuple(row), ("FL.ru", "feed", "https://www.fl.ru/projects/1/x.html"))
            self.assertIn("feed:fl", monitor.allowed_sources(cfg))
            self.assertNotIn("feed:fl", monitor.allowed_sources(monitor.Config(cfg.db, owner_id=42)))
            db.close()

    def test_feed_names_are_validated(self):
        with patch.dict(os.environ, {"MONITOR_FEEDS": "fl, Kwork ,connect"}):
            self.assertEqual(monitor.Config.from_env().feeds, ("fl", "kwork", "connect"))
        with patch.dict(os.environ, {"MONITOR_FEEDS": "https://fl.ru"}):
            with self.assertRaises(ValueError):
                monitor.Config.from_env()


if __name__ == "__main__":
    unittest.main()
