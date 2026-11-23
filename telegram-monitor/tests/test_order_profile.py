import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor as m
import order_profile as p


class ProfileTests(unittest.TestCase):
    def test_advertisements_are_not_requests_or_partnerships(self):
        for text in (
            '#помогу #дизайн Логотипы, сайты под ключ, лендинги. Я графический и веб-дизайнер. Буду рада сотрудничеству. Пишите в лс',
            'Разработка ботов и сайтов. Могу помочь с доработкой проекта. Цены ниже рыночных. Пишите @ExampleContact',
            'Ищу работу дизайнером сайтов, мое портфолио',
            'Создание сайтов под ключ. Пишите в лс',
            'Я сайтами и дизайном занимаюсь',
        ):
            a = p.assess(text)
            self.assertEqual(a['kind'], 'offer')
            self.assertIsNone(a['case'])
            self.assertEqual(a['draft'], '')

    def test_service_mention_and_unconfirmed_technology(self):
        self.assertEqual(p.assess('Сайт есть. Возможно, пора подумать о дизайне')['kind'], 'ambiguous')
        self.assertEqual(p.assess('Нужен Telegram-бот для платежей')['kind'], 'other')
        self.assertEqual(p.assess('Нужен Telegram-бот. Сайт уже есть')['kind'], 'other')
        self.assertNotEqual(p.assess('Ищу юриста, у компании есть сайт')['kind'], 'demand')
        a = p.assess('Нужен сайт на Laravel, пишите в лс')
        self.assertTrue(a['fit'].startswith('частичное'))
        self.assertEqual(a['draft'], '')

    def test_solution_uses_actual_task_without_url_or_promises(self):
        a = p.assess('Нужен лендинг example.ru для регистрации на вебинар. Пишите в лс. Пример https://example.com')
        self.assertEqual(a['kind'], 'demand')
        self.assertIn('вебинар', a['need'])
        self.assertIn('форму регистрации', a['draft'])
        self.assertIn('На моём сайте есть кейс', a['draft'])
        self.assertIn('Готов показать', a['draft'])
        for forbidden in ('http', 't.me/', 'example.ru', 'руб', 'дней', 'гарантир'):
            self.assertNotIn(forbidden, a['draft'])
        self.assertEqual(a['case']['name'], 'Анна Фомичёва — вебинары / ВЭД')

    def test_form_and_requested_materials_preserve_author_route(self):
        text = 'Набираем дизайнеров на постоянное сотрудничество для лендингов. Заполните анкету. Пришлите цену, кейсы и срок.'
        a = p.assess(text)
        self.assertEqual(a['kind'], 'partnership')
        self.assertEqual(a['response']['route'], 'form')
        self.assertEqual(a['response']['instruction'], 'Заполните анкету')
        self.assertEqual(a['response']['requested'], ['цену / стоимость', 'кейсы / портфолио', 'срок', 'анкету'])
        self.assertEqual(a['draft'], '')
        self.assertIsNone(a['response']['contact'])

    def test_email_and_comments_do_not_become_private_outreach(self):
        for route, end in [('email', 'Отклик только на mail@example.org'), ('comment', 'Пишите в комментариях')]:
            a = p.assess('Нужен сайт. ' + end)
            self.assertEqual(a['response']['route'], route)
            self.assertEqual(a['draft'], '')

    def test_all_five_categories(self):
        for text, kind in (
            ('Нужен лендинг', 'demand'),
            ('Ищем веб-дизайнера на постоянное сотрудничество', 'partnership'),
            ('Ищем веб-дизайнера в штат, оклад', 'vacancy'),
            ('Предлагаю услуги: сайты', 'offer'),
            ('У нас есть сайт', 'ambiguous'),
        ):
            self.assertEqual(p.assess(text)['kind'], kind, text)

    def test_design_case_does_not_claim_developed_exchange(self):
        a = p.assess('Нужен дизайн интерфейса в Figma. Пишите в лс')
        self.assertIn('Bittly', a['draft'])
        self.assertIn('без разработки обменника', a['draft'])

    def test_reclassify_preserves_status_timestamps_and_delivery_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db.sqlite'
            db = m.connect(path)
            cfg = m.Config(path, owner_id=42, source_ids=(-123,))
            t = dt.datetime(2026, 10, 30, 9, tzinfo=m.UTC)
            with patch('monitor.now', return_value=t):
                cid = m.ingest(db, cfg, '-123', '1', 'Нужен лендинг', t, t)
                with db:
                    db.execute("UPDATE events SET body='#помогу Делаю сайты' WHERE id=(SELECT event_id FROM candidates WHERE id=?)", (cid,))
                    db.execute("UPDATE candidates SET owner_status='taken' WHERE id=?", (cid,))
                    db.execute("UPDATE deliveries SET state='sent',attempts=1,message_id=7,sent_at=? WHERE candidate_id=?", (m.iso(t), cid))
                before = tuple(db.execute('SELECT * FROM deliveries').fetchone())
                times = tuple(db.execute('SELECT published_at,received_at FROM events').fetchone())
                m.reclassify(db, cfg, (cid,))
                db.close()
                db = m.connect(path)
                self.assertEqual(tuple(db.execute('SELECT kind,priority,owner_status FROM candidates').fetchone()), ('offer','rejected','taken'))
                self.assertEqual(tuple(db.execute('SELECT * FROM deliveries').fetchone()), before)
                self.assertEqual(tuple(db.execute('SELECT published_at,received_at FROM events').fetchone()), times)
                self.assertEqual(m.due_rows(db, cfg, t), [])
                db.close()

    def test_card_fits_telegram_even_with_long_emoji_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = m.connect(Path(tmp) / 'db.sqlite')
            cfg = m.Config(Path(tmp) / 'db.sqlite', owner_id=42, source_ids=(-123,))
            t = dt.datetime(2026, 10, 30, 9, tzinfo=m.UTC)
            with patch('monitor.now', return_value=t):
                m.ingest(db, cfg, '-123', '1', 'Нужен сайт ' + '🎉' * 1900, t, t, link='https://t.me/source/1')
                row = m.due_rows(db, cfg, t)[0]
                text = m.render(row)
            self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
            self.assertEqual(m.keyboard(cfg, row['candidate_id'], row['link'])['inline_keyboard'][-1][0]['url'],
                             'https://t.me/source/1')
            self.assertIn('Наш отклик', text)
            db.close()


if __name__ == '__main__':
    unittest.main()
