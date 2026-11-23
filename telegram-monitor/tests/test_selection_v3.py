"""Synthetic boundary tests, separate from the frozen real-message holdout."""
import unittest
import order_profile as p

class SelectionBoundaries(unittest.TestCase):
    def test_observed_task_pool_is_not_a_concrete_commission(self):
        # New live evidence #529, outside the original frozen93-message sample.
        text=('#ищу #техспец #разработчик #тильда #боты. Технические специалисты в подрядку. '
              'Разбираем поток задач от онлайн-школ, агентств и небольшого бизнеса. '
              'Вот типичные из последнего времени: сверстать 4 страницы на Тильде по готовому дизайн-коду; '
              'перенести сайт с конструктора на WordPress. Кого ищем: тех, кто доводит задачу до конца. '
              'Ставку называете вы. Анкета обязательна.')
        a=p.assess(text)
        self.assertEqual(a['kind'],'partnership')
        self.assertEqual(a['notification_route'],'partnership')
        self.assertEqual(a['draft'],'')
        mixed='В поиске дизайнеров помещения, создателей логотипов, так же нужно создать 2 сайта'
        self.assertEqual(p.assess(mixed)['notification_route'],'main')
    def test_negation_and_independent_tasks(self):
        for text,route in [
            ('Сайт не нужен', 'none'),
            ('Бюджет не указан. Нужен сайт', 'main'),
            ('Без бюджета пока нужен сайт', 'main'),
            ('Не нужен сайт. Нужна презентация', 'main'),
            ('Нужен сайт, не в штат, разовый проект', 'main'),
            ('Не ищу партнёров по продажам. Нужен сайт', 'main'),
            ('Ищу юриста, у компании есть сайт', 'none'),
            ('Нужен Telegram-бот. Отдельно нужен сайт', 'main'),
            ('Задача закрыта. Отдельно нужен лендинг', 'main'),
            ('Нужен сайт. Исполнитель найден', 'none'),
            ('Ищу менеджера по продажам сайтов', 'none'),
            ('Создаю сайты и ищу клиентов', 'none'),
            ('Нужен сайт? Создаю сайты под ключ, пишите', 'none'),
            ('Предлагаю услуги дизайна. Отдельно нужен сайт для своего проекта', 'main'),
            ('Ищем веб-дизайнера в штат или на проект', 'review'),
            ('Вакансия: разовый проект. Нужен лендинг', 'main'),
            ('Вакансия: нужен сайт на Tilda для кофейни', 'main'),
            ('Ищу партнёра: нужен сайт для моей компании', 'main'),
            ('Ищем подрядчика, будем передавать заказы на сайты', 'partnership'),
        ]:
            with self.subTest(text=text):self.assertEqual(p.assess(text)['notification_route'],route)

    def test_unknowns_do_not_block_selection(self):
        for text in ('Нужен сайт','Нужна презентация без указанного бюджета'):
            a=p.assess(text)
            self.assertEqual(a['notification_route'],'main')
            self.assertTrue(a['task_exists'])
        self.assertEqual(p.assess('Нужен сайт на Laravel')['notification_route'],'review')
        a=p.assess('Нужен сайт')
        self.assertEqual(a['draft'],'')
        self.assertTrue(any('маршрут' in item for item in a['response']['unknown']))

    def test_required_url_is_explicit_conflict(self):
        a=p.assess('Нужен лендинг. Пишите в лс. Пришлите ссылку на портфолио')
        self.assertEqual(a['notification_route'],'main')
        self.assertTrue(a['conflicts'])
        self.assertEqual(a['draft'],'')
