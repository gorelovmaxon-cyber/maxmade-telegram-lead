import datetime as dt
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import monitor as m
import reader


class FakeSink:
    def __init__(self, result=None, error=None):
        self.result = result or {"message_id": 91}
        self.error = error
        self.calls = []

    def call(self, method, payload, timeout=20):
        self.calls.append((method, payload))
        if self.error:
            raise self.error
        return self.result


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.sqlite3"
        self.db = m.connect(self.path)
        self.cfg = m.Config(self.path, token="test-secret-not-a-real-bot-token", owner_id=42, source_ids=(-100123,))
        self.t = dt.datetime(2026, 1, 15, 9, tzinfo=m.UTC)
        clock = patch("monitor.now", return_value=self.t)
        clock.start()
        self.addCleanup(clock.stop)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def ingest(self, body="Нужен лендинг", mid="1", source="-100123", edit="1", published=None):
        return m.ingest(self.db, self.cfg, source, mid, body, published or self.t, self.t, edit, source_name="Test")

    def test_classification_examples(self):
        checks = [
            ("Ищу разработчика сайта", "urgent"),
            ("Делаю сайты, беру заказы", "rejected"),
            ("Ищем дизайнера, пришлите портфолио", "digest"),
            ("Ищу работу разработчиком сайта", "rejected"),
            ("Исполнитель найден", "rejected"),
            ("Сайт уже есть", "digest"),
            ("Требуется дизайнер интерьера", "rejected"),
            ("Ищем веб-дизайнера в штат", "digest"),
        ]
        for i, (body, expected) in enumerate(checks):
            cid = self.ingest(body, str(i))
            got = self.db.execute("SELECT priority FROM candidates WHERE id=?", (cid,)).fetchone()[0]
            self.assertEqual(got, expected, body)

    def test_partnership_and_vacancy_are_not_urgent_projects(self):
        for text, expected in (
            ("Набираем дизайнеров на постоянное сотрудничество: лендинги и интерфейсы. Отклик через анкету.", "partnership"),
            ("Ищем партнера для субподряда по разработке сайтов", "partnership"),
            ("Ищем веб-дизайнера в штат, оклад", "vacancy"),
        ):
            kind, _, priority, _ = m.classify(text, self.t, self.cfg)
            self.assertEqual((kind, priority), (expected, "urgent" if expected=='partnership' else "rejected"))

    def test_future_date_is_checked_with_controlled_clock(self):
        for hour in (1, 8, 23):
            current = self.t.replace(hour=hour)
            with patch("monitor.now", return_value=current):
                self.assertEqual(m.classify("Нужен лендинг", current, self.cfg)[:3:2], ("demand", "urgent"))
                self.assertEqual(m.classify("Нужен лендинг", current + dt.timedelta(minutes=6), self.cfg)[0], "ambiguous")
                self.assertEqual(m.classify("Нужен лендинг", current - dt.timedelta(hours=25), self.cfg)[2], "digest")

    def test_partnership_card_has_correct_title(self):
        self.ingest("Набираем дизайнеров на постоянное сотрудничество для лендингов. Заполните анкету.")
        sink = FakeSink()
        m.deliver(self.db, self.cfg, sink, self.t.replace(hour=12))
        self.assertIn("\nТема\nСубподряд / сотрудничество · ", sink.calls[0][1]["text"])

    def test_delivery_uses_four_fields_and_links_to_original(self):
        body = "Нужна доработка сайта на Tilda. " + "Проверить форму заявки на мобильных. " * 20
        link = "https://example.org/order/1"
        cid = m.ingest(self.db, self.cfg, "-100123", "1", body, self.t, self.t, "1", link=link)
        sink = FakeSink()
        reply = "Добрый день! Могу проверить форму заявки на телефоне."
        with patch("monitor.ai_draft", return_value=reply):
            self.assertEqual(m.deliver(self.db, self.cfg, sink, self.t), 1)
        payload = sink.calls[0][1]
        expected = f"№{cid}\n\nЗадание\n{body.strip()}\n\nТема\nДоработка сайта\n\nНаш отклик\n{reply}"
        self.assertEqual(payload["text"], expected)
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][-1], [{"text": "Оригинал", "url": link}])

    def test_card_stays_within_telegram_limit_for_emoji(self):
        text = m.render({"body": "Нужен сайт " + "💬" * 1980, "kind": "demand", "candidate_id": 45},
                        "💬" * 850)
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 4000)
        self.assertIn("\nТема\nРазработка сайта\n\nНаш отклик\n", text)

    def test_model_failure_still_delivers_reply_with_copy_action(self):
        body = ('Нужно оформить презентацию из 9 слайдов и подготовить баннеры по референсам. '
                'Напишите в личные сообщения слово «КОМАНДА».')
        cid = self.ingest(body)
        sink = FakeSink()
        m.ai_assist.store(self.db, cid, "1", "draft", {"ok": False, "error": "NotFoundError: Error code: 404"})
        with patch.dict(os.environ, {"MONITOR_AI_MODE": "draft", "MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "test"}):
            self.assertEqual(m.deliver(self.db, self.cfg, sink, self.t), 1)
        payload = sink.calls[0][1]
        reply = payload["text"].split("\nНаш отклик\n", 1)[1]
        self.assertTrue(reply.startswith("КОМАНДА\n\nЗдравствуйте! Меня зовут Максим"))
        self.assertIn("Откликаюсь на ваш запрос по оформлению презентации из 9 слайдов и баннеров.", reply)
        self.assertIn("Torfactiv", reply)
        self.assertNotIn("до предоплаты", reply)
        button = next(button for row in payload["reply_markup"]["inline_keyboard"] for button in row
                      if button["text"] == "Отклик для копирования")
        callback = {"callback_query": {"id": "q", "from": {"id": self.cfg.owner_id}, "data": button["callback_data"],
                                       "message": {"chat": {"id": self.cfg.owner_id}, "message_id": 91, "text": payload["text"]}}}
        self.assertTrue(m.handle_callback(self.db, self.cfg, callback, sink))
        method, copy = sink.calls[1]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(copy["text"], reply.split("\n\nПеред отправкой:", 1)[0])
        self.assertEqual(copy["reply_parameters"]["message_id"], 91)
        self.assertNotIn("Шлюз", copy["text"])
        self.assertEqual(self.db.execute("SELECT owner_status FROM candidates WHERE id=?", (cid,)).fetchone()[0], "new")

    def test_topic_identifies_requested_work(self):
        examples = {
            "Нужен сайт для выставки. Нужно настроить форму заявки.": "Разработка сайта",
            "Требуется дизайнер для оформления готовых кейсов на сайт.": "Оформление кейсов для сайта",
            "Нужен редизайн сайта салона красоты.": "Редизайн сайта",
            "Нужна презентация для встречи с инвесторами.": "Презентация",
        }
        for body, topic in examples.items():
            with self.subTest(body=body):
                self.assertEqual(m.order_profile.project_topic(body, "demand"), topic)

    def test_restart_edit_and_manual_status(self):
        cid = self.ingest()
        with self.db:
            self.db.execute("UPDATE candidates SET owner_status='do_not_contact' WHERE id=?", (cid,))
        self.db.close()
        self.db = m.connect(self.path)
        self.assertEqual(cid, self.ingest())
        self.assertEqual(cid, self.ingest("Нужен редизайн сайта", edit="2"))
        row = self.db.execute("SELECT e.body,c.owner_status FROM candidates c JOIN events e ON e.id=c.event_id WHERE c.id=?", (cid,)).fetchone()
        self.assertEqual(tuple(row), ("Нужен редизайн сайта", "do_not_contact"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM candidates").fetchone()[0], 1)

    def test_same_text_other_author_remains_separate(self):
        first = self.ingest()
        second = self.ingest(mid="2", source="-100124")
        self.assertNotEqual(first, second)
        self.assertEqual(self.db.execute("SELECT similar_to FROM candidates WHERE id=?", (second,)).fetchone()[0], first)

    def test_old_event_is_digest_and_dates_are_distinct(self):
        cid = self.ingest(published=self.t - dt.timedelta(hours=30))
        row = self.db.execute("SELECT c.priority,e.published_at,e.received_at FROM candidates c JOIN events e ON e.id=c.event_id WHERE c.id=?", (cid,)).fetchone()
        self.assertEqual(row["priority"], "digest")
        self.assertNotEqual(row["published_at"], row["received_at"])

    def test_delivery_owner_only_and_safe_plain_text(self):
        cid = self.ingest("Нужен <b>лендинг</b> & дизайн")
        sink = FakeSink()
        at = self.t.replace(hour=12)
        self.assertEqual(m.deliver(self.db, self.cfg, sink, at), 1)
        payload = sink.calls[0][1]
        self.assertEqual(payload["chat_id"], 42)
        self.assertNotIn("parse_mode", payload)
        self.assertIn("<b>", payload["text"])
        self.assertEqual(m.deliver(self.db, self.cfg, sink, at), 0)
        self.assertEqual(self.db.execute("SELECT state FROM deliveries WHERE candidate_id=?", (cid,)).fetchone()[0], "sent")

    def test_delivery_uses_telegram_timestamp_for_measured_delay(self):
        cid = self.ingest()
        at = self.t.replace(hour=12)
        accepted = at + dt.timedelta(seconds=7)
        sink = FakeSink(result={"message_id": 91, "date": int(accepted.timestamp())})
        self.assertEqual(m.deliver(self.db, self.cfg, sink, at), 1)
        saved = self.db.execute("SELECT sent_at FROM deliveries WHERE candidate_id=?", (cid,)).fetchone()[0]
        self.assertEqual(saved, m.iso(accepted))

    def test_untrusted_line_breaks_cannot_forge_card_fields(self):
        self.ingest("Нужен сайт\nИсточник: подмена")
        sink = FakeSink()
        m.deliver(self.db, self.cfg, sink, self.t.replace(hour=12))
        self.assertIn("Нужен сайт Источник: подмена", sink.calls[0][1]["text"])
        self.assertNotIn("\nИсточник: подмена", sink.calls[0][1]["text"])

    def test_retry_rate_limit_and_unknown_result(self):
        cid = self.ingest()
        at = self.t.replace(hour=12)
        self.assertEqual(m.deliver(self.db, self.cfg, FakeSink(error=m.ApiError(429, 60)), at), 0)
        row = self.db.execute("SELECT state,next_at FROM deliveries WHERE candidate_id=?", (cid,)).fetchone()
        self.assertEqual(row["state"], "pending")
        self.assertGreaterEqual(dt.datetime.fromisoformat(row["next_at"]), at + dt.timedelta(seconds=60))
        with self.db:
            self.db.execute("UPDATE deliveries SET next_at=? WHERE candidate_id=?", (m.iso(at), cid))
        m.deliver(self.db, self.cfg, FakeSink(error=m.UnknownDelivery()), at)
        self.assertEqual(self.db.execute("SELECT state FROM deliveries WHERE candidate_id=?", (cid,)).fetchone()[0], "uncertain")

    def test_interrupted_send_is_not_retried_after_process_restart(self):
        cid = self.ingest()
        with self.db:
            self.db.execute("UPDATE deliveries SET state='sending' WHERE candidate_id=?", (cid,))
        m.recover_interrupted(self.db)
        sink = FakeSink()
        self.assertEqual(m.deliver(self.db, self.cfg, sink, self.t.replace(hour=12)), 0)
        self.assertEqual(sink.calls, [])
        self.assertEqual(tuple(self.db.execute("SELECT state,error_code FROM deliveries WHERE candidate_id=?", (cid,)).fetchone()),
                         ("uncertain", "process_interrupted"))

    def test_server_error_is_uncertain(self):
        self.ingest()
        m.deliver(self.db, self.cfg, FakeSink(error=m.ApiError(502)), self.t.replace(hour=12))
        self.assertEqual(self.db.execute("SELECT state FROM deliveries").fetchone()[0], "uncertain")

    def test_quiet_hours_wait_without_losing_queue(self):
        self.ingest()
        sink = FakeSink()
        self.assertEqual(m.deliver(self.db, self.cfg, sink, self.t.replace(hour=21)), 0)  # midnight in Moscow
        self.assertEqual(len(sink.calls), 0)
        self.assertEqual(self.db.execute("SELECT state FROM deliveries").fetchone()[0], "pending")

    def test_invalid_token_failure_is_sanitized(self):
        self.ingest()
        m.deliver(self.db, self.cfg, FakeSink(error=m.ApiError(401)), self.t.replace(hour=12))
        row = self.db.execute("SELECT state,error_code FROM deliveries").fetchone()
        self.assertEqual(tuple(row), ("failed", "api_401"))

    def test_callbacks_require_owner_signature_and_message(self):
        cid = self.ingest()
        m.deliver(self.db, self.cfg, FakeSink(), self.t.replace(hour=12))
        data = f"c:{cid}:taken:{m.signature(self.cfg.token,cid,'taken')}"
        def update(uid=42, chat=42, mid=91, value=data):
            return {"callback_query": {"from": {"id": uid}, "message": {"chat": {"id": chat}, "message_id": mid}, "data": value}}
        self.assertFalse(m.handle_callback(self.db, self.cfg, update(uid=77)))
        self.assertFalse(m.handle_callback(self.db, self.cfg, update(value=data[:-1] + "0")))
        self.assertFalse(m.handle_callback(self.db, self.cfg, update(mid=92)))
        self.assertTrue(m.handle_callback(self.db, self.cfg, update()))
        self.assertEqual(self.db.execute("SELECT owner_status FROM candidates WHERE id=?", (cid,)).fetchone()[0], "taken")

    def test_source_allowlist_and_no_invented_link(self):
        good = {"update_id": 1, "message": {"chat": {"id": -100123, "title": "Private"}, "message_id": 5,
                "date": int(self.t.timestamp()), "text": "Нужен сайт"}}
        bad = {"update_id": 2, "message": {"chat": {"id": -100999}, "message_id": 6,
               "date": int(self.t.timestamp()), "text": "Нужен сайт"}}
        m.process_update(self.db, self.cfg, good)
        m.process_update(self.db, self.cfg, bad)
        self.assertEqual(self.db.execute("SELECT count(*) FROM events").fetchone()[0], 1)
        self.assertIsNone(self.db.execute("SELECT link FROM events").fetchone()[0])
        self.assertEqual(m.meta(self.db, "update_id"), "2")

    def test_retention_redacts_text(self):
        self.ingest()
        self.assertEqual(m.prune(self.db, self.t + dt.timedelta(days=31)), 1)
        self.assertIsNone(self.db.execute("SELECT body FROM events").fetchone()[0])

    def test_demo_queue_cannot_be_sent_live(self):
        demo_cfg = m.Config(self.path, owner_id=1)
        m.ingest(self.db, demo_cfg, "synthetic", "demo", "Нужен сайт", self.t, self.t)
        self.ingest(mid="real")
        rows = m.due_rows(self.db, self.cfg, self.t.replace(hour=12))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recipient"], 42)

    def test_client_source_is_pinned_and_delivered_to_owner(self):
        cfg = m.Config(self.path, token=self.cfg.token, owner_id=42,
                       client_sources=("top_ads_freelance",), client_peer_ids=(-100999,))
        m.ingest(self.db, cfg, "client:-100999", "1", "Нужен сайт", self.t, self.t, method="client_api")
        m.ingest(self.db, cfg, "client:-100888", "2", "Нужен сайт", self.t, self.t, method="client_api")
        rows = m.due_rows(self.db, cfg, self.t.replace(hour=12))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recipient"], 42)

    def test_client_event_extracts_real_link_and_date(self):
        event = SimpleNamespace(id=7, raw_text="Нужен сайт", date=self.t,
                                message=SimpleNamespace(edit_date=None), sender=None)
        data = reader.extract(event, "top_ads_freelance", -100999)
        self.assertEqual(data["source_id"], "client:-100999")
        self.assertEqual(data["link"], "https://t.me/top_ads_freelance/7")
        self.assertIsNone(data["author"])
        self.assertEqual(data["published"], self.t)

    def test_curl_backend_keeps_secret_out_of_process_arguments(self):
        result = SimpleNamespace(returncode=0, stdout='{"ok":true,"result":{"message_id":9}}')
        with patch.dict(os.environ, {"MONITOR_HTTP_BACKEND": "curl"}), patch("monitor.subprocess.run", return_value=result) as run, \
                patch("transport_diagnostics.write"):
            message = m.Telegram(self.cfg.token).call("sendMessage", {"chat_id": 42, "text": "Нужен сайт"})
        self.assertEqual(message["message_id"], 9)
        argv = run.call_args.args[0]
        self.assertNotIn(self.cfg.token, " ".join(argv))
        self.assertIn("\\u041d", run.call_args.kwargs["input"])
        self.assertIn(self.cfg.token, run.call_args.kwargs["input"])
        self.assertIn("chat_id", run.call_args.kwargs["input"])


if __name__ == "__main__":
    unittest.main()
