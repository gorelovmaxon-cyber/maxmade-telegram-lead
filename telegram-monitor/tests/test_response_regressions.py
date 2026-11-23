"""Synthetic regressions for TG-R4–R8. No real source, database or sending."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor as m
import order_profile as p


class ResponseRegressions(unittest.TestCase):
    def test_project_and_negated_employment_are_not_staff_vacancies(self):
        for ending in (
            'Не в штат, разовый проект.',
            'Без трудоустройства, проектная работа.',
            'Трудоустройство не требуется, договор подряда.',
            'Не на полный день. Оклада нет, оплата за результат.',
            'Проектная работа, не штатная вакансия.',
        ):
            with self.subTest(ending=ending):
                self.assertEqual(p.assess('Нужен веб-дизайнер для сайта. ' + ending)['kind'], 'demand')

    def test_explicit_staff_employment_still_wins_over_project_word(self):
        a = p.assess('Ищем веб-дизайнера в штат для проектной работы. Оклад и оформление по ТК.')
        self.assertEqual(a['kind'], 'vacancy')
        self.assertEqual(a['draft'], '')

    def test_conflicting_or_alternative_employment_is_ambiguous(self):
        for ending in ('В штат или проектно, условия обсудим.', 'Без трудоустройства, но полный день и оклад.'):
            with self.subTest(ending=ending):
                self.assertEqual(p.assess('Нужен дизайнер сайта. ' + ending)['kind'], 'ambiguous')

    def test_no_materials_question_or_repeated_copywriting_when_materials_ready(self):
        a = p.assess('Нужен лендинг на Tilda для вебинара. Тексты и дизайн готовы. Бюджет 40000 руб., до пятницы. Пишите в лс.')
        self.assertTrue(a['draft'])
        self.assertNotIn('Какие исходные материалы', a['draft'])
        self.assertNotIn('собрать структуру и тексты', a['draft'])
        self.assertIn('готов', a['approach'])
        self.assertEqual(a['clarification'], '')
        self.assertNotIn('?', a['draft'])

    def test_only_missing_platform_is_asked_when_materials_are_ready(self):
        a = p.assess('Нужен лендинг для вебинара. Все материалы готовы. Пишите в лс.')
        self.assertIn('платформ', a['clarification'])
        self.assertNotIn('материалы', a['clarification'])
        self.assertEqual(a['draft'].count('?'), 1)

    def test_platform_choice_already_delegated_does_not_trigger_question(self):
        a = p.assess('Нужен сайт, платформу выбирайте сами. Материалы есть. Пишите в лс.')
        self.assertEqual(a['clarification'], '')

    def test_required_price_and_timeline_block_unconfirmed_reply(self):
        a = p.assess('Нужен лендинг. Пишите в лс, пришлите цену, кейсы и срок.')
        self.assertEqual(a['draft_status'], 'not_ready')
        self.assertEqual(a['draft'], '')
        self.assertIn('цена предложения Максима', a['missing'])
        self.assertIn('срок исполнения Максима', a['missing'])
        self.assertIn('материал кейса / портфолио для отклика', a['missing'])
        self.assertIn('Не готов к отправке', p.card_guidance('Нужен лендинг. Пишите в лс, пришлите цену и срок.'))

    def test_client_budget_and_deadline_are_not_our_confirmed_offer(self):
        a = p.assess('Нужен сайт на Tilda. Бюджет 50000 руб., срок 7 дней. Пишите в лс, укажите вашу цену и срок.')
        self.assertEqual(a['response']['unknown'], [])
        self.assertEqual(a['draft_status'], 'not_ready')
        self.assertEqual(a['draft'], '')

    def test_explicit_owner_confirmation_can_complete_non_url_requirements(self):
        a = p.assess('Нужен сайт на Tilda. Пишите в лс, укажите вашу цену и срок.',
                     confirmed={'price': '45000 руб. за согласованный объём', 'timeline': '10 рабочих дней после получения материалов'})
        self.assertEqual(a['draft_status'], 'review')
        self.assertIn('45000', a['draft'])
        self.assertIn('10 рабочих дней', a['draft'])
        self.assertEqual(a['missing'], [])
        self.assertNotIn('http', a['draft'])

    def test_other_required_information_is_not_silently_omitted(self):
        for requirement in ('Укажите опыт работы.', 'Пришлите резюме.', 'Пришлите условия сотрудничества.',
                            'Пришлите цену и телефон.', 'Укажите стоимость и ИНН.',
                            'Укажите вашу цену и объясните чем будете полезны.'):
            with self.subTest(requirement=requirement):
                a = p.assess('Нужен лендинг. Пишите в лс. ' + requirement)
                self.assertEqual(a['draft_status'], 'not_ready')
                self.assertEqual(a['draft'], '')
                self.assertTrue(a['missing'])

    def test_unrecognized_detail_alongside_confirmed_price_blocks_reply(self):
        a = p.assess('Нужен лендинг. Пишите в лс. Укажите цену и расскажите чем будете полезны.', confirmed={'price': '45000 рублей'})
        self.assertEqual(a['draft_status'], 'not_ready')
        self.assertEqual(a['draft'], '')
        self.assertTrue(a['response']['unparsed_requirements'])

    def test_required_fields_have_verbatim_evidence_separate_from_route(self):
        a = p.assess('Нужен сайт. Пишите в лс. Пришлите цену и срок.')
        self.assertEqual(a['response']['instruction'], 'Пишите в лс')
        self.assertIn('Пришлите цену и срок', a['response']['requirement_quotes'])

    def test_routes_are_distinct_and_never_replaced_with_private_outreach(self):
        for route, instruction in (
            ('private', 'Пишите в лс @ExampleContact.'),
            ('form', 'Заполните анкету https://example.org/form.'),
            ('platform_bot', 'Отклик только через @ExampleOrderBot.'),
            ('platform_bot', 'Отправьте отклик через https://t.me/ExampleOrderBot.'),
            ('platform_bot', 'Отклик через бота площадки.'),
            ('comment', 'Пишите в комментариях под постом.'),
            ('email', 'Отклик на mail@example.org.'),
            ('exchange', 'Отклик только через биржу; доступ платный.'),
        ):
            with self.subTest(route=route, instruction=instruction):
                a = p.assess('Нужен лендинг. ' + instruction)
                self.assertEqual(a['response']['route'], route)
                self.assertTrue(a['response']['instruction'])
                if route != 'private':
                    self.assertEqual(a['draft'], '')
                    self.assertEqual(a['draft_status'], 'not_ready')

    def test_route_negation_and_explicit_destination_are_respected(self):
        a = p.assess('Нужен лендинг. Не пишите в личку, отклик только в комментариях.')
        self.assertEqual(a['response']['route'], 'comment')
        self.assertEqual(a['draft'], '')
        a = p.assess('Нужен лендинг. Анкету заполнять не нужно. Пишите в лс @ExampleContact.')
        self.assertEqual(a['response']['route'], 'private')
        a = p.assess('Нужен лендинг. Не пишите в комментарии и личку. Отклик через @ExampleOrderBot.')
        self.assertEqual(a['response']['route'], 'platform_bot')

    def test_unknown_or_conflicting_route_cannot_be_ready(self):
        for ending in ('', 'В анкете указан пример. Пока не определили, куда присылать отклик.'):
            with self.subTest(ending=ending):
                a = p.assess('Нужен лендинг. ' + ending)
                self.assertEqual(a['draft_status'], 'not_ready')
                self.assertEqual(a['draft'], '')
                self.assertIn('способ отклика', ' '.join(a['missing']))

    def test_url_requirement_is_a_visible_conflict_even_with_owner_data(self):
        for instruction in ('Пришлите ссылку на портфолио.', 'Обязательно URL кейса.', 'В отклике нужны ссылки на выполненные сайты.',
                            'URL обязателен.', 'В отклике: ссылка на кейс.'):
            with self.subTest(instruction=instruction):
                text = 'Нужен лендинг. Пишите в лс. ' + instruction
                a = p.assess(text, confirmed={'case_material': 'https://example.org/work'})
                self.assertEqual(a['draft_status'], 'not_ready')
                self.assertEqual(a['draft'], '')
                self.assertTrue(a['conflicts'])
                self.assertIn('Конфликт', p.card_guidance(text))

    def test_example_url_and_no_links_instruction_do_not_create_url_requirement(self):
        text = 'Нужен сайт на Tilda. Пример https://example.design. Пишите в лс. Ссылки не нужны, портфолио не присылайте.'
        a = p.assess(text)
        self.assertEqual(a['conflicts'], [])
        self.assertEqual(a['draft_status'], 'review')
        self.assertNotIn('example.design', a['draft'])

    def test_first_reply_never_copies_links_from_task_or_owner_data(self):
        a = p.assess('Нужен лендинг example.design для вебинара. Пишите в лс.')
        self.assertTrue(a['draft'])
        self.assertNotIn('example.design', a['draft'])
        a = p.assess('Нужен лендинг. Пишите в лс, пришлите кейс.', confirmed={'case_material': 'https://example.org/case'})
        self.assertEqual(a['draft'], '')
        self.assertTrue(a['conflicts'])

    def test_long_unknown_requirements_keep_readiness_and_original(self):
        text = 'Нужен сайт. Пишите в лс. ' + ('Пришлите подробное объяснение решения ' + '🎉' * 90 + '. ') * 12
        a = p.assess(text)
        self.assertEqual(a['draft_status'], 'not_ready')
        self.assertEqual(a['draft'], '')
        guide = p.card_guidance(text)
        self.assertLessEqual(len(guide.encode('utf-16-le')) // 2, 2850)
        self.assertIn('Не готов к отправке', guide)

    def test_truncated_saved_fragment_cannot_be_a_complete_reply(self):
        text = 'Нужен лендинг. Пишите в лс. ' + 'Описание ' * 260 + 'Пришлите URL и цену.'
        self.assertEqual(p.assess(text)['draft_status'], 'not_ready')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.sqlite3'
            db = m.connect(path)
            cfg = m.Config(path, owner_id=42, source_ids=(-123,))
            t = dt.datetime(2026, 10, 30, 9, tzinfo=m.UTC)
            with patch('monitor.now', return_value=t):
                m.ingest(db, cfg, '-123', '1', text, t, t)
            saved = db.execute('SELECT body FROM events').fetchone()[0]
            self.assertLessEqual(len(saved), 2000)
            self.assertIn(p.TRUNCATED_MARKER, saved)
            a = p.assess(saved)
            self.assertEqual(a['draft_status'], 'not_ready')
            self.assertEqual(a['draft'], '')
            self.assertIn('полный текст требований', ' '.join(a['missing']))
            db.close()

    def test_negated_stack_does_not_block_confirmed_tilda_work(self):
        a = p.assess('Нужен сайт на Tilda, WordPress не нужен. Пишите в лс.')
        self.assertFalse(a['fit'].startswith('частичное'))
        self.assertTrue(a['draft'])

    def test_requirement_at_end_is_not_lost_and_card_stays_within_telegram_limit(self):
        text = 'Нужен сайт. Пишите в лс. ' + '🎉' * 1400 + '. Пришлите цену и срок.'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.sqlite3'
            db = m.connect(path)
            cfg = m.Config(path, owner_id=42, source_ids=(-123,))
            t = dt.datetime(2026, 10, 30, 9, tzinfo=m.UTC)
            with patch('monitor.now', return_value=t):
                m.ingest(db, cfg, '-123', '1', text, t, t, link='https://t.me/example/1')
                row = m.due_rows(db, cfg, t)[0]
                card = m.render(row)
            self.assertLessEqual(len(card.encode('utf-16-le')) // 2, 4096)
            self.assertIn('Пришлите цену и срок.', card)
            self.assertIn('Черновик пока недоступен.', card)
            self.assertEqual(m.keyboard(cfg, row['candidate_id'], row['link'])['inline_keyboard'][-1][0]['url'],
                             'https://t.me/example/1')
            db.close()

    def test_project_negation_flows_through_ingest_without_touching_manual_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.sqlite3'
            db = m.connect(path)
            cfg = m.Config(path, owner_id=42, source_ids=(-123,))
            t = dt.datetime(2026, 10, 30, 9, tzinfo=m.UTC)
            with patch('monitor.now', return_value=t):
                cid = m.ingest(db, cfg, '-123', '1', 'Нужен сайт. Не в штат, проектная работа.', t, t)
                self.assertEqual(db.execute('SELECT kind FROM candidates WHERE id=?', (cid,)).fetchone()[0], 'demand')
                with db:
                    db.execute("UPDATE candidates SET owner_status='do_not_contact' WHERE id=?", (cid,))
                m.ingest(db, cfg, '-123', '1', 'Нужен сайт. Без трудоустройства.', t, t, edit_version='2')
                self.assertEqual(db.execute('SELECT owner_status FROM candidates WHERE id=?', (cid,)).fetchone()[0], 'do_not_contact')
                self.assertEqual(m.due_rows(db, cfg, t), [])
            db.close()


if __name__ == '__main__':
    unittest.main()
