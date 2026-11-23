"""Offline tests for the optional Claude assistance (no network)."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_assist
import monitor
import order_profile


def temp_db() -> tuple[monitor.sqlite3.Connection, Path]:
    folder = Path(tempfile.mkdtemp(prefix="ai-test-"))
    path = folder / "monitor.sqlite3"
    return monitor.connect(path), path


class ScrubTests(unittest.TestCase):
    def test_contacts_and_links_removed(self):
        raw = ("Пишите @ivan_petrov, сайт https://klinika-romashka.ru/xt, "
               "почта a@b.ru, тел +7 999 123-45-67, t.me/joinchat/abc")
        out = ai_assist.scrub(raw)
        for token in ("@ivan_petrov", "https://", "a@b.ru", "999", "t.me/"):
            self.assertNotIn(token, out)
        self.assertIn("Пишите", out)

    def test_site_url_skips_platforms(self):
        self.assertEqual(ai_assist.site_url("отклик на https://kwork.ru/projects/1"), None)
        self.assertEqual(ai_assist.site_url("пишите https://t.me/somebody"), None)
        self.assertEqual(ai_assist.site_url("наш сайт https://example.org/page"),
                         "https://example.org/page")

    def test_site_url_ignores_images(self):
        self.assertEqual(ai_assist.site_url("скрин https://example.org/a.png"), None)


class ParseTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(ai_assist.parse_json('{"verdict":"fit"}'), {"verdict": "fit"})

    def test_fenced_json(self):
        self.assertEqual(ai_assist.parse_json('```json\n{"a":1}\n```'), {"a": 1})

    def test_json_with_prose(self):
        self.assertEqual(ai_assist.parse_json('Итог: {"v":"weak"} — всё'), {"v": "weak"})

    def test_garbage(self):
        with self.assertRaises(ai_assist.AIError):
            ai_assist.parse_json("нет JSON")


class DraftTests(unittest.TestCase):
    def test_draft_has_confirmed_approaches_and_scrubbed_order_context(self):
        response = {"data": {"draft": "Готовый отклик"}, "model": "test-model",
                    "input_tokens": 1, "output_tokens": 1}
        with mock.patch.object(ai_assist, "_ask", return_value=response) as ask:
            result = ai_assist.draft("Нужен сайт, писать @client_id", "Сайт на Tilda")

        prompt, context, route, _ = ask.call_args.args
        self.assertEqual(result["draft"], "Готовый отклик")
        self.assertEqual(route, "draft")
        self.assertIn("Примеры подачи", prompt)
        self.assertIn("Меня зовут Максим, я разработчик", prompt)
        self.assertIn("сайтов и систем автоматизации", prompt)
        self.assertIn("Откликаюсь на ваш запрос по …", prompt)
        self.assertIn("согласовать цель страницы и путь к заявке", context)
        self.assertIn("Сайт на Tilda", context)
        self.assertNotIn("@client_id", context)

    def test_known_case_link_is_available_in_an_invited_private_reply(self):
        url = order_profile.profile()["cases"]["exchanger-frame"]["url"]
        result = {"data": {"draft": f"Пример макетов: {url}."}, "model": "test-model",
                  "input_tokens": 1, "output_tokens": 1}
        with mock.patch.object(ai_assist, "_ask", return_value=result) as ask:
            draft = ai_assist.draft("Нужен дизайн интерфейса. Как откликнуться: пишите @client_one.")
        self.assertIn(url, draft["draft"])
        self.assertIn(url, ask.call_args.args[1])
        self.assertNotIn("@client_one", ask.call_args.args[1])
        with mock.patch.object(ai_assist, "_ask", return_value=result) as ask:
            with self.assertRaisesRegex(ai_assist.AIError, "неподтверждённая ссылка"):
                ai_assist.draft("Нужен дизайн интерфейса; способ отклика не указан.")
        self.assertNotIn(url, ask.call_args.args[1])

    def test_inappropriate_offer_can_be_held_and_cached_without_an_outreach(self):
        db, _ = temp_db()
        self.addCleanup(db.close)
        cfg = monitor.Config(db, owner_id=1)
        body = "Нужно продвинуть сайт в топ поиска"
        now = monitor.now()
        candidate = monitor.ingest(db, cfg, "synthetic", "1", body, now, now, "1")
        response = {"data": {"status": "hold", "draft": "", "risk": "Запрошено продвижение."},
                    "model": "test-model", "input_tokens": 1, "output_tokens": 1}
        with mock.patch.dict(os.environ, {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "fake"}, clear=True), \
             mock.patch.object(ai_assist, "_ask", return_value=response) as ask:
            first = ai_assist.review(db, candidate, "1", body, "draft")
            second = ai_assist.review(db, candidate, "1", body, "draft")
        self.assertTrue(first["ok"])
        self.assertEqual(first["draft"], "")
        self.assertTrue(second["cached"])
        self.assertEqual(second["draft_note"], "Запрошено продвижение.")
        ask.assert_called_once()


class ConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ai_assist.configured())

    def test_needs_key(self):
        with mock.patch.dict(os.environ, {"MONITOR_AI_ENABLED": "1"}, clear=True):
            self.assertFalse(ai_assist.configured())
        with mock.patch.dict(os.environ, {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "x"}, clear=True):
            self.assertTrue(ai_assist.configured())

    def test_model_override(self):
        with mock.patch.dict(os.environ, {"MONITOR_AI_MODEL": "claude-opus-5-5"}, clear=True):
            self.assertEqual(ai_assist.model_name("draft"), "claude-opus-5-5")

    def test_judge_model_falls_back_to_draft_model(self):
        with mock.patch.dict(os.environ, {"MONITOR_AI_MODEL": "claude-sonnet-5-5"}, clear=True):
            self.assertEqual(ai_assist.model_name("judge"), "claude-sonnet-5-5")

    def test_base_url_is_optional(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ai_assist.base_url(), "")
        with mock.patch.dict(os.environ, {"MONITOR_AI_BASE_URL": " https://example.test "}, clear=True):
            self.assertEqual(ai_assist.base_url(), "https://example.test")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = temp_db()

    def tearDown(self):
        self.db.close()

    def _candidate(self):
        cfg = monitor.Config(self.db, owner_id=1)
        return monitor.ingest(self.db, cfg, "synthetic", "1", "Нужен лендинг для клиники",
                              monitor.now(), monitor.now(), "1")

    def test_roundtrip(self):
        cid = self._candidate()
        result = {"ok": True, "verdict": "fit", "reason": "прямой запрос",
                  "draft": "Здравствуйте!", "site": {"url": "https://x.ru", "summary": "Tilda"},
                  "model": "claude-opus-5-5", "input_tokens": 100, "output_tokens": 50}
        ai_assist.store(self.db, cid, "1", "judge", result)
        hit = ai_assist.cached(self.db, cid, "1", "judge")
        self.assertEqual(hit["verdict"], "fit")
        self.assertEqual(json.loads(hit["site"])["url"], "https://x.ru")

    def test_cache_key_includes_edit_version(self):
        cid = self._candidate()
        ai_assist.store(self.db, cid, "1", "draft", {"ok": True, "draft": "старый"})
        self.assertIsNone(ai_assist.cached(self.db, cid, "2", "draft"))

    def test_budget(self):
        with mock.patch.dict(os.environ, {"MONITOR_AI_MONTHLY_CALLS": "1"}, clear=True):
            cid = self._candidate()
            self.assertEqual(ai_assist.budget_left(self.db), 1)
            ai_assist.store(self.db, cid, "1", "draft", {"ok": True, "draft": "x"})
            self.assertEqual(ai_assist.budget_left(self.db), 0)

    def test_review_without_key_is_off(self):
        cid = self._candidate()
        with mock.patch.dict(os.environ, {}, clear=True):
            got = ai_assist.review(self.db, cid, "1", "Нужен лендинг", "judge")
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "выключено")

    def test_transient_failure_is_retried_instead_of_sealed(self):
        # A timeout used to be stored as the final answer: the card never got a
        # block again, even after the network recovered.
        cid = self._candidate()
        ai_assist.store(self.db, cid, "1", "judge", {"ok": False, "error": "APITimeoutError: timed out"})
        self.assertIsNone(ai_assist.cached(self.db, cid, "1", "judge"))
        self.assertEqual(ai_assist.attempts(self.db, cid, "1", "judge"), 1)

    def test_final_failure_is_still_cached(self):
        cid = self._candidate()
        ai_assist.store(self.db, cid, "1", "judge", {"ok": False, "error": "Модель вернула не JSON"})
        self.assertIsNotNone(ai_assist.cached(self.db, cid, "1", "judge"))

    def test_row_that_claims_success_without_a_draft_is_retried(self):
        # Older versions stored a full pass as ok=1 with an error and no draft.
        # Such a row must not seal the card: there is no block to show on it.
        cid = self._candidate()
        ai_assist.store(self.db, cid, "1", "full",
                         {"ok": True, "verdict": "fit", "error": "Не удалось разобрать ответ модели"})
        self.assertIsNone(ai_assist.cached(self.db, cid, "1", "full"))

    def test_retries_are_bounded_and_stop_spending(self):
        cid = self._candidate()
        with mock.patch.dict(os.environ, {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k"}, clear=True):
            for _ in range(ai_assist.ATTEMPT_LIMIT):
                ai_assist.store(self.db, cid, "1", "judge", {"ok": False, "error": "RateLimitError: 429"})
            self.assertEqual(ai_assist.attempts(self.db, cid, "1", "judge"), ai_assist.ATTEMPT_LIMIT)
            with mock.patch.object(ai_assist, "_ask") as ask:
                got = ai_assist.review(self.db, cid, "1", "Нужен лендинг", "judge")
            ask.assert_not_called()
        self.assertFalse(got["ok"])
        self.assertIn("RateLimitError", got["error"])

    def test_successful_retry_replaces_the_stored_error(self):
        cid = self._candidate()
        ai_assist.store(self.db, cid, "1", "judge", {"ok": False, "error": "APIConnectionError: reset"})
        ai_assist.store(self.db, cid, "1", "judge", {"ok": True, "verdict": "fit", "reason": "прямой запрос"})
        hit = ai_assist.cached(self.db, cid, "1", "judge")
        self.assertEqual(hit["verdict"], "fit")
        self.assertEqual(ai_assist.attempts(self.db, cid, "1", "judge"), 2)

    def test_truncated_answer_is_retried_not_sealed(self):
        # max_tokens means the JSON was cut mid-string: the card got no draft,
        # so it must stay eligible for the next pass instead of being cached.
        cid = self._candidate()
        ai_assist.store(self.db, cid, "1", "full",
                        {"ok": False, "verdict": "fit", "error": "max_tokens: ответ модели обрезан по лимиту 900"})
        self.assertIsNone(ai_assist.cached(self.db, cid, "1", "full"))


class CardTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = temp_db()
        self.cfg = monitor.Config(self.db, owner_id=1, token="1:abcdefghijklmnopqrstuvwxyz")

    def tearDown(self):
        self.db.close()

    def test_two_keyboard_rows(self):
        kb = monitor.keyboard(self.cfg, 7)["inline_keyboard"]
        self.assertEqual(len(kb), 2)
        actions = [button["callback_data"].split(":")[2] for row in kb for button in row]
        self.assertEqual(actions, ["taken", "not_fit", "replied", "dialog", "closed"])

    def test_ai_draft_empty_when_disabled(self):
        row = {"candidate_id": 1, "edit_version": "1", "kind": "demand", "priority": "urgent"}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(monitor.ai_draft(self.db, row))

    def test_route_agrees_for_every_known_kind(self):
        """The cache key written by ai_worker must be the one delivery reads."""
        cases = [("demand", "draft"), ("ambiguous", "judge"), ("offer", "judge"),
                 ("other", "judge"), ("closed_signal", "judge"),
                 ("partnership", "judge"), ("vacancy", "judge"),
                 ("review", "draft"), ("unknown", "draft")]
        for kind, expected in cases:
            self.assertEqual(monitor.ai_route({"kind": kind}), expected, kind)

    def test_verdict_without_draft_is_not_used_as_reply(self):
        cfg = monitor.Config(self.db, owner_id=1)
        cid = monitor.ingest(self.db, cfg, "synthetic", "10", "Ищу подрядчика, страница закрыта",
                             monitor.now(), monitor.now(), "1")
        with self.db:
            self.db.execute("UPDATE candidates SET kind='ambiguous' WHERE id=?", (cid,))
        row = {"candidate_id": cid, "edit_version": "1", "kind": "ambiguous", "priority": "review"}
        ai_assist.store(self.db, cid, "1", "judge", {"ok": True, "verdict": "not_fit",
                                                     "reason": "предложение услуг"})
        with mock.patch.dict(os.environ, {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k"}, clear=True):
            text = monitor.ai_draft(self.db, row)
        self.assertEqual(text, "")

    def test_ai_draft_from_cache(self):
        cfg = monitor.Config(self.db, owner_id=1)
        cid = monitor.ingest(self.db, cfg, "synthetic", "9", "Нужен лендинг для клиники",
                             monitor.now(), monitor.now(), "1")
        row = {"candidate_id": cid, "edit_version": "1", "kind": "demand", "priority": "urgent"}
        ai_assist.store(self.db, cid, "1", "draft", {"ok": True, "draft": "Добрый день!",
                                                     "site": {"summary": "Tilda, 5 с"}})
        with mock.patch.dict(os.environ, {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k"}, clear=True):
            text = monitor.ai_draft(self.db, row)
        self.assertEqual(text, "Добрый день!")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = temp_db()
        self.cfg = monitor.Config(self.db, owner_id=1)

    def tearDown(self):
        self.db.close()

    def test_median_not_mean(self):
        """One very late card must not hide the usual speed."""
        cfg = monitor.Config(self.db, owner_id=1)
        start = monitor.now() - monitor.dt.timedelta(hours=2)
        for index, minutes in enumerate((1, 2, 3, 300)):
            cid = monitor.ingest(self.db, cfg, "synthetic", str(index), f"Нужен лендинг {index}",
                                 start, start, str(index))
            published = start - monitor.dt.timedelta(minutes=minutes)
            with self.db:
                self.db.execute("UPDATE events SET received_at=?, published_at=? WHERE id="
                                "(SELECT event_id FROM candidates WHERE id=?)",
                                (monitor.iso(start), monitor.iso(published), cid))
                self.db.execute("UPDATE deliveries SET state='sent', sent_at=? WHERE candidate_id=?",
                                (monitor.iso(start), cid))
        text = monitor.report(self.db, self.cfg, days=7)
        self.assertIn("Медиана", text)
        self.assertIn("выборка 4", text)
        self.assertNotIn("Среднее время", text)

    def test_empty_sources_do_not_crash(self):
        self.assertIn("Сводка", monitor.report(self.db, self.cfg, days=7))


class FullModeTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = temp_db()
        self.cfg = monitor.Config(self.db, owner_id=1)

    def tearDown(self):
        self.db.close()

    def _make(self):
        cid = monitor.ingest(self.db, self.cfg, "synthetic", "20", "Нужен лендинг",
                             monitor.now(), monitor.now(), "1")
        return cid, {"candidate_id": cid, "edit_version": "1", "kind": "demand", "priority": "urgent"}

    def test_full_mode_uses_one_route_for_every_kind(self):
        with mock.patch.dict(os.environ, {"MONITOR_AI_MODE": "full"}, clear=True):
            for kind in ("demand", "ambiguous", "offer", "vacancy", "closed_signal"):
                self.assertEqual(monitor.ai_route({"kind": kind}), "full", kind)

    def test_grey_mode_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(monitor.ai_route({"kind": "demand"}), "draft")
            self.assertEqual(monitor.ai_route({"kind": "vacancy"}), "judge")

    def test_draft_only_mode_writes_replies_without_judging_or_site_lookup(self):
        cid, row = self._make()
        env = {"MONITOR_AI_MODE": "draft", "MONITOR_AI_ENABLED": "1",
               "MONITOR_AI_API_KEY": "k", "MONITOR_AI_STRICT": "1"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(ai_assist, "draft", return_value={"ok": True, "draft": "Готовый отклик"}) as draft, \
                mock.patch.object(ai_assist, "judge") as judge, \
                mock.patch.object(ai_assist.site_audit, "audit") as audit:
            self.assertFalse(monitor.strict())
            self.assertEqual(monitor.ai_route({"kind": "partnership"}), "draft")
            body = "Нужна доработка сайта https://example.org/"
            route = monitor.ai_route(row)
            self.assertTrue(ai_assist.review(self.db, cid, "1", body, route)["ok"])
            self.assertEqual(monitor.ai_draft(self.db, row), "Готовый отклик")
            draft.assert_called_once_with(body, "")
            judge.assert_not_called()
            audit.assert_not_called()

    def test_hold_counts_as_two_calls(self):
        cid, _ = self._make()
        with mock.patch.dict(os.environ, {"MONITOR_AI_MONTHLY_CALLS": "2"}, clear=True):
            ai_assist.store(self.db, cid, "1", "full", {"ok": True})
            self.assertEqual(ai_assist.calls_this_month(self.db), 2)
            self.assertEqual(ai_assist.budget_left(self.db), 0)
            ai_assist.store(self.db, cid, "1", "draft", {"ok": True})
            self.assertEqual(ai_assist.calls_this_month(self.db), 3)

    def test_retryable_errors_are_recognised(self):
        self.assertTrue(ai_assist.retryable("APIConnectionError: connection reset"))
        self.assertTrue(ai_assist.retryable("RateLimitError: 429"))
        self.assertTrue(ai_assist.retryable("max_tokens: ответ модели обрезан по лимиту 900"))
        self.assertFalse(ai_assist.retryable("Модель вернула не JSON"))
        self.assertFalse(ai_assist.retryable(""))


class FullModeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = temp_db()
        self.cfg = monitor.Config(self.db, owner_id=1)

    def tearDown(self):
        self.db.close()

    def _worker(self, verdict="not_fit"):
        cid = monitor.ingest(self.db, self.cfg, "synthetic", "40", "Нужен лендинг",
                             monitor.now(), monitor.now(), "1")
        fake = {"ok": True, "verdict": verdict, "reason": "предложение услуг", "draft": "Здравствуйте",
                "site": None, "model": "m"}
        return cid, fake

    def test_full_mode_labels_but_keeps_card_by_default(self):
        cid, fake = self._worker()
        env = {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k", "MONITOR_AI_MODE": "full"}
        with mock.patch.dict(os.environ, env, clear=True),              mock.patch.object(ai_assist, "review", return_value=fake),              mock.patch.object(ai_assist, "budget_left", return_value=99):
            stats = monitor.ai_worker(self.db, self.cfg)
        row = self.db.execute("SELECT priority FROM candidates WHERE id=?", (cid,)).fetchone()
        self.assertEqual(row["priority"], "urgent", "карточка не должна исчезать без явного согласия")
        self.assertEqual(stats["reviewed"], 1)

    def test_strict_opt_in_removes_card(self):
        cid, fake = self._worker()
        env = {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k",
               "MONITOR_AI_MODE": "full", "MONITOR_AI_STRICT": "1"}
        with mock.patch.dict(os.environ, env, clear=True),              mock.patch.object(ai_assist, "review", return_value=fake),              mock.patch.object(ai_assist, "budget_left", return_value=99):
            monitor.ai_worker(self.db, self.cfg)
        row = self.db.execute("SELECT priority FROM candidates WHERE id=?", (cid,)).fetchone()
        self.assertEqual(row["priority"], "rejected")

    def test_delivered_cards_are_never_revisited(self):
        cid, fake = self._worker(verdict="fit")
        with self.db:
            self.db.execute("UPDATE deliveries SET state='sent', sent_at=? WHERE candidate_id=?",
                            (monitor.iso(monitor.now()), cid))
        env = {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k", "MONITOR_AI_MODE": "full"}
        with mock.patch.dict(os.environ, env, clear=True),              mock.patch.object(ai_assist, "review", return_value=fake) as review:
            monitor.ai_worker(self.db, self.cfg)
        review.assert_not_called()

    def test_worker_uses_delivery_sources_and_owner_before_limiting_the_queue(self):
        _, fake = self._worker(verdict="fit")
        cfg = monitor.Config(self.db, owner_id=1, source_ids=(-101,))
        now = monitor.now()
        wrong_owner = monitor.ingest(self.db, cfg, "-101", "41", "Нужна презентация для конференции",
                                     now, now, "1")
        with self.db:
            self.db.execute("UPDATE deliveries SET recipient=2 WHERE candidate_id=?", (wrong_owner,))
        body = "Ищу веб-дизайнера, нужен дизайн интерфейса сервиса бронирования в Figma"
        eligible = monitor.ingest(self.db, cfg, "-101", "42", body, now, now, "1")
        env = {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k", "MONITOR_AI_MODE": "full"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ai_assist, "review", return_value=fake) as review:
            stats = monitor.ai_worker(self.db, cfg, limit=1)

        review.assert_called_once_with(self.db, eligible, "1", body, "full")
        self.assertEqual(stats["reviewed"], 1)


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = temp_db()
        self.cfg = monitor.Config(self.db, owner_id=1, token="1:abcdefghijklmnopqrstuvwxyz")
        self.env = {"MONITOR_AI_ENABLED": "1", "MONITOR_AI_API_KEY": "k",
                    "MONITOR_AI_HOLD_MINUTES": "5"}

    def tearDown(self):
        self.db.close()

    def _card(self):
        cid = monitor.ingest(self.db, self.cfg, "synthetic", "30", "Нужен лендинг",
                             monitor.now(), monitor.now(), "1")
        with self.db:
            self.db.execute("UPDATE sources SET name=id WHERE id='synthetic'")
            self.db.execute("INSERT OR IGNORE INTO sources(id,name,method,access_state) "
                            "VALUES('synthetic','synthetic','bot_api','ok')")
        row = self.db.execute("""SELECT d.*, c.priority,c.reason,c.kind,c.owner_status,c.similar_to,
                  e.body,e.published_at,e.received_at,e.edit_version,e.link,e.author,
                  'synthetic' AS source_name FROM deliveries d
                  JOIN candidates c ON c.id=d.candidate_id JOIN events e ON e.id=c.event_id
                  WHERE c.id=?""", (cid,)).fetchone()
        return cid, row

    def test_card_waits_while_block_is_missing(self):
        cid, row = self._card()
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.assertTrue(monitor.waiting_for_ai(self.db, row, monitor.now()))

    def test_card_goes_out_when_block_is_ready(self):
        cid, row = self._card()
        with mock.patch.dict(os.environ, self.env, clear=True):
            ai_assist.store(self.db, cid, "1", monitor.ai_route(row),
                            {"ok": True, "verdict": "fit", "draft": "Здравствуйте, по лендингу"})
            self.assertFalse(monitor.waiting_for_ai(self.db, row, monitor.now()))

    def test_wait_is_bounded(self):
        cid, row = self._card()
        late = monitor.now() + monitor.dt.timedelta(minutes=6)
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.assertFalse(monitor.waiting_for_ai(self.db, row, late))

    def test_hold_off_without_key(self):
        cid, row = self._card()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(monitor.waiting_for_ai(self.db, row, monitor.now()))

    def test_zero_hold_never_blocks(self):
        cid, row = self._card()
        env = dict(self.env, MONITOR_AI_HOLD_MINUTES="0")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(monitor.waiting_for_ai(self.db, row, monitor.now()))


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = temp_db()
        self.cfg = monitor.Config(self.db, owner_id=1)

    def tearDown(self):
        self.db.close()

    def _sink(self):
        return mock.Mock()

    def test_waits_for_monday_morning(self):
        sink = self._sink()
        sunday = monitor.dt.datetime(2026, 11, 8, 12, 0, tzinfo=monitor.UTC)
        self.assertFalse(monitor.send_weekly_report(self.db, self.cfg, sink, sunday))
        monday_early = monitor.dt.datetime(2026, 11, 9, 9, 0, tzinfo=monitor.UTC)
        self.assertFalse(monitor.send_weekly_report(self.db, self.cfg, sink, monday_early))
        sink.call.assert_not_called()

    def test_sends_once_per_week(self):
        sink = self._sink()
        monday = monitor.dt.datetime(2026, 11, 9, 11, 0, tzinfo=monitor.UTC)
        self.assertTrue(monitor.send_weekly_report(self.db, self.cfg, sink, monday))
        self.assertEqual(sink.call.call_count, 1)
        again = monday + monitor.dt.timedelta(hours=3)
        self.assertFalse(monitor.send_weekly_report(self.db, self.cfg, sink, again))
        self.assertEqual(sink.call.call_count, 1)
        next_week = monday + monitor.dt.timedelta(days=7)
        self.assertTrue(monitor.send_weekly_report(self.db, self.cfg, sink, next_week))
        self.assertEqual(sink.call.call_count, 2)

    def test_failed_send_is_retried_next_tick(self):
        sink = self._sink()
        sink.call.side_effect = monitor.UnknownDelivery("сеть")
        monday = monitor.dt.datetime(2026, 11, 9, 11, 0, tzinfo=monitor.UTC)
        self.assertFalse(monitor.send_weekly_report(self.db, self.cfg, sink, monday))
        sink.call.side_effect = None
        self.assertTrue(monitor.send_weekly_report(self.db, self.cfg, sink, monday))


if __name__ == "__main__":
    unittest.main()
