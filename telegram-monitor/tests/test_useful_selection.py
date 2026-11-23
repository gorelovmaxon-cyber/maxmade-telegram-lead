"""Declared requirements and synthetic boundaries, never production originals."""
import datetime as dt
import unittest
from unittest.mock import patch
import order_profile as p
import monitor as m
import filter_policy as f
import test_filter_policy as policy_tests
Sink=policy_tests.Sink

CASES=[
 ('Нужен лендинг для курса, желательно Tilda','main'),
 ('Посоветуйте, кто переделает сайт нашей компании','main'),
 ('Есть макет в Figma, нужно сверстать адаптивный лендинг','main'),
 ('Нужна презентация компании для встречи с инвесторами','main'),
 ('Нужен дизайнер интерфейса личного кабинета','main'),
 ('Нужен сайт на Laravel со сложной системой учёта','review'),
 ('Нужен бухгалтер. Сайт нашей компании — example.invalid','none'),
 ('Ищу клиентов на разработку сайтов','none'),
 ('Ищу человека, который будет продавать мои сайты','none'),
 ('Делаю сайты. Нужен лендинг? Пишите!','none'),
 ('Я сайтами и дизайном занимаюсь','none'),
 ('Кто занимается сайтами?','review'),
 ('Кто занимается сайтами? Нужно исправить форму заявки','main'),
 ('Сайт не нужен, нужен только логотип','none'),
 ('Не нужен новый сайт, нужно переделать мобильную версию существующего','main'),
 ('Ищу разработчика бота, сайт уже есть','none'),
 ('Нужен сайт и бот, ищем одного исполнителя на всё','review'),
 ('Нужен сайт и бот; можно взять только сайт','main'),
 ('Бюджет пока обсуждаем, нужен лендинг','main'),
 ('Нашли исполнителя, всем спасибо','none'),
 ('Ищем агентство, которое приведёт нам клиентов','none'),
 ('Мы агентство, передадим разработчику два сайта','main'),
 ('Нужен сайт. Присылайте цену, срок и ссылку на портфолио','main'),
 ('Мы агентство, ищем разработчика лендингов для передачи проектов','partnership'),
 ('Нужен дизайнер на постоянный подряд, задачи по интерфейсам в Figma','partnership'),
 ('Есть заказчик на сайт, сам не успеваю, ищу исполнителя','main'),
 ('Ищу партнёров, приводите клиентов, разработку беру на себя','none'),
 ('Плачу 35% за приведённого клиента на сайт','none'),
 ('Ищу партнёра в веб-студию','review'),
 ('Ищем технических специалистов в подрядку','review'),
 ('Ищем технических специалистов в подрядку: Tilda, адаптивы, формы','partnership'),
 ('Я дизайнер, сейчас нужен верстальщик для моего проекта: макет лендинга готов','main'),
 ('Разрабатываем сайты. Если вам нужен лендинг — обращайтесь','none'),
 ('Вакансия: собрать один лендинг на Tilda, проектная оплата','main'),
 ('Ищем веб-дизайнера в штат, полный день, оклад','employment'),
 ('Ищем дизайнера на проекты, не в штат','review'),
 ('Нужен менеджер по продажам сайтов','none'),
 ('Открыты вакансии: маркетолог, редактор, веб-дизайнер','review'),
 ('Ищем дизайнера, можно в штат или на проект','review'),
 ('На прошлый сайт исполнитель найден. Теперь нужен дизайнер новой презентации','main'),
 ('Кто сможет собрать страницу регистрации до пятницы?','main'),
 ('#ищу\nНужен лендиг для курса. Бюджета пока нет','main'),
 ('Коллеги, посоветуйте человека, который переделает наш сайт','main'),
 ('Цитата чужого объявления: «Делаю сайты». Никаких работ не заказываем','none'),
 ('Нужен сайт, WordPress не нужен, можно Tilda','main'),
 ('Нужен сайт. Отклики не принимаем','none'),
 ('Нужен лендинг. [Фрагмент сокращён; полный текст проверить в оригинале]','review'),
 ('Нужна разработка парсера, на сайте есть описание','none'),
 ('Я разрабатываю сайты, беру проекты. Стоимость от 30000 рублей','none'),
 ('Логотип делаем сами. Отдельно нужен лендинг','main'),
 ('Я дизайнер презентаций. Если вам нужна презентация продукта — пишите, с радостью помогу','none'),
 ('Нужно оформить пост в Figma','review'),
 ('Ищем дизайнера в Figma, задача пока не описана','review'),
 ('Интернет-маркетолог Бюджет: 30000 ₽ Ищу специалиста для комплексного продвижения сайта и соцсетей. Готовы к постоянному сотрудничеству.','none'),
 ('Ищем специалиста для продвижения строительного сайта. Нужна стратегия SEO-продвижения, заинтересованы в долгосрочном сотрудничестве.','none'),
 ('Требуется уникальный копирайтер. Для будущего сайта нужен текст об инвестициях.','none'),
 ('3D-модель одежды. Нужно переделать модель под новые лекала и подготовить её для сайта.','none'),
 ('Площадки для размещения помещений. Нужно собрать Excel: название, сайт и рейтинг.','none'),
 ('Доработка сайта на MODX. Нужно сверстать две страницы по Figma.','review'),
 ('Ищем маркетолога. Отдельно нужен лендинг на Tilda. Можно взять только сайт.','main'),
 ('Нужен лендинг под рекламу. Настройку рекламы возьмём на себя.','main'),
 ('Нужна презентация для продвижения сайта. Есть готовая структура слайдов.','main'),
 ('Создание сайта и приложения для кафе. Нужно сделать сайт для кафе.','review'),
 ('Развернуть почтовый сервер, проверить защиту сайта. Нужно развернуть постовый сервер, привязанный к домену сайта.', 'none'),
 ('Обработать фото товара. Нужно обработать 50 фото для размещения на сайте.', 'none'),
 ('Нужно зарегистрироваться на сайте магазина, плачу 2000 рублей.', 'none'),
 ('Визуальный дизайнер. Ищем визуального дизайнера по графике и веб-дизайну для пополнения команды.', 'employment'),
 ('Нужен сайт магазина. Фотографии и почтовый сервер уже готовы.', 'main'),
 ('Стартап ищет разработчиков для создания приложений, сайтов и ИИ-агентов. На первом этапе развитие внутренней системы без фиксированной оплаты. Клиентские задачи сдельно.', 'employment'),
 ('Агентство ищет разработчика для передачи проектов на Tilda. Сейчас нужен лендинг для курса, макеты готовы.', 'partnership'),
]

class UsefulSelectionTests(unittest.TestCase):
    def test_required_and_additional_examples(self):
        for text,route in CASES:
            with self.subTest(text=text):self.assertEqual(p.assess(text)['notification_route'],route)
    def test_evidence_offsets_and_unknowns(self):
        for text,_ in CASES:
            a=p.assess(text)
            for e in a['evidence'] or []:self.assertEqual(text[e['start']:e['end']],e['quote'])
            self.assertIn(a['work_direction'],('unknown','clients_to_author','work_to_us'))
        a=p.assess('Нужен сайт')
        self.assertEqual(a['author_role'],'unknown')
        self.assertIn('бюджет',a['response']['unknown'])
        self.assertEqual(a['draft'],'')
    def test_unknown_stack_and_fragment_do_not_notify(self):
        for text in ('Нужен сайт на Laravel','Нужен сайт и бот, один исполнитель','Нужен сайт. '+('Требования. '*250)):
            a=p.assess(text);self.assertEqual(a['notification_route'],'review')
            self.assertEqual(m.classify(text,m.now(),m.Config(__import__('pathlib').Path('unused')))[2],'rejected')
    def test_roles_and_scope(self):
        a=p.assess('Есть заказчик на сайт, сам не успеваю, ищу исполнителя')
        self.assertEqual(a['author_role'],'intermediary')
        b=p.assess('Нужен сайт и бот; можно взять только сайт')
        self.assertEqual(b['service_fit'],'partial')
        self.assertTrue(b['constraints'])
    def test_domains_and_newlines_do_not_split_the_task(self):
        a=p.assess('Нужен лендинг example.invalid для регистрации на вебинар. Пишите в лс')
        self.assertIn('вебинар',a['need'])
        self.assertEqual(p.assess('Создаю любых:\n1) Ботов\n2) Сайты\n3) Лендинги')['notification_route'],'none')

class UsefulDeliveryTests(unittest.TestCase):
    setUp=policy_tests.PolicyTests.setUp
    tearDown=policy_tests.PolicyTests.tearDown
    add=policy_tests.PolicyTests.add
    activate=policy_tests.PolicyTests.activate
    def test_only_project_and_subcontract_send_no_review_digest(self):
        self.activate()
        for i,(text,_) in enumerate(CASES):self.add(text,str(i))
        sink=Sink();m.deliver(self.db,self.cfg,sink,self.at)
        while m.deliver(self.db,self.cfg,sink,self.at):pass
        expected=sum(route in ('main','partnership') for _,route in CASES)
        self.assertEqual(len(sink.calls),expected)
        self.assertTrue(any('Субподряд / сотрудничество' in c[1]['text'] for c in sink.calls))
    def test_subcontract_cross_source_duplicate_and_revision(self):
        self.activate()
        text='Мы агентство, ищем разработчика лендингов для передачи проектов. Контакт @ExampleContact. '+('Отдельные задачи Tilda, адаптивы. '*8)
        cid=self.add(text);self.add(text,'2',source='-2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
        self.assertIsNotNone(f.latest(self.db,2)['duplicate_of'])
        sink=Sink();m.deliver(self.db,self.cfg,sink,self.at)
        self.add(text+' Бюджет 60000',version='2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM filter_updates').fetchone()[0],1)
        self.assertEqual(m.deliver_revisions(self.db,self.cfg,sink,self.at,('-1',)),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM publication_versions').fetchone()[0],4)
