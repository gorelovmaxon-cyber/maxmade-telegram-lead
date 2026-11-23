"""First replies assembled from the order and the verified MAXMADE profile."""

import re

import order_profile


INTRO = 'Здравствуйте! Меня зовут Максим, я разработчик сайтов и систем автоматизации.'
DESIGN_NOTE = 'После обсуждения задачи бесплатно подготовлю макет рабочего экрана, чтобы вы могли оценить подход до предоплаты.'


def draft(body: str) -> str:
    order = order_profile.assess(body)
    route = order['notification_route']
    if route not in ('main', 'partnership'):
        return f'Отклик не подготовлен. {order["reason"]}.'

    response = order['response']
    if response['route'] in ('form', 'platform_bot', 'comment', 'ambiguous') or response['prohibited_private']:
        return 'Перед отправкой: откройте оригинал и проверьте указанный способ отклика и обязательные поля.'

    topic = order_profile.project_topic(body, 'demand')
    text = body.lower().replace('ё', 'е')
    screen = topic != 'Презентация'
    if topic == 'Автоматизация заявок':
        subject = 'автоматизации заявок'
        plan = 'Предлагаю описать путь заявки от формы до получателя, согласовать поля и проверить передачу на тестовой заявке.'
        question = 'Куда сейчас должна попадать новая заявка?'
        screen = False
    elif topic == 'Презентация':
        count = re.search(r'\b(\d{1,2})\s+слайд(?:а|ов)?\b', text)
        subject = 'оформлению презентации' + (f' из {count[0]}' if count else '')
        plan = 'Предлагаю собрать единый стиль слайдов: выделить главную мысль на каждом и настроить читаемость текста и графики.'
        question = 'Кто будет смотреть презентацию и в каком формате её будут показывать?'
        if re.search(r'референс\w*|готов\w*\s+материал\w*', text):
            plan += ' В работе буду опираться на ваши материалы и референсы.'
            question = 'Можете прислать материалы, чтобы я предложил оформление одного слайда?'
        if re.search(r'подготов\w*\s+(?:несколько\s+)?баннер\w*', text):
            subject += ' и баннеров'
            plan += ' Баннеры оформлю в том же стиле, чтобы материалы запуска воспринимались вместе.'
    elif topic in ('Дизайн сайта', 'Дизайн интерфейса'):
        subject = 'дизайну сайта' if topic == 'Дизайн сайта' else 'дизайну интерфейса'
        plan = 'Предлагаю сначала согласовать ключевой пользовательский сценарий, затем подготовить адаптивные макеты и компоненты в Figma для передачи в разработку.'
        question = 'Какой сценарий нужно проработать в первую очередь?'
    elif topic == 'Оформление кейсов для сайта':
        subject = 'оформлению кейсов для сайта'
        plan = 'Предлагаю взять один готовый проект и собрать из его материалов страницу: задача, принятые решения и результат. От неё можно выстроить общую подачу остальных кейсов.'
        question = 'Какой проект хотите оформить первым?'
    elif topic == 'Доработка сайта':
        subject = 'доработке сайта'
        if re.search(r'\bформ\w*.{0,25}\b(?:заявк\w*|отправ\w*)', text):
            plan = 'Предлагаю проверить, как сейчас работает форма заявки на телефоне и компьютере, затем согласовать и внести нужные правки.'
            question = 'На какой странице проверить форму?'
            screen = False
        else:
            plan = 'Предлагаю начать с текущей страницы и согласовать список правок. После изменений проверю затронутые блоки на телефоне и компьютере.'
            question = 'Можете прислать адрес страницы и список правок?'
    else:
        redesign = topic == 'Редизайн сайта'
        subject = 'редизайну сайта' if redesign else 'разработке сайта'
        site = 'Обновлю структуру существующей страницы' if redesign else 'Предлагаю собрать структуру сайта'
        if re.search(r'\btilda\b|\bтильд\w*', text):
            subject += ' на Tilda'
            site = 'Предлагаю выполнить работу на Tilda'
        plan = f'{site}. Согласуем, что посетитель должен увидеть первым и как он оставит заявку. Затем подготовлю дизайн и проверю мобильную версию.'
        question = 'Какое действие посетителя для вас главное?'

    if route == 'partnership':
        subject = 'проектному сотрудничеству по ' + subject
        question = 'Какую первую задачу вы готовы передать на подряд?'

    parts = [INTRO, f'Откликаюсь на ваш запрос по {subject}. {plan}']
    keyword = re.search(r'(?i)\bнапиши\w*\b[^.!?]{0,55}\bслово\s+[«“"]([А-ЯЁA-Zа-яёa-z0-9_-]{1,30})[»”"]', body)
    if keyword:
        parts.insert(0, keyword[1])
    case = order['case']
    direct = response['route'] in ('private', 'contact')
    if case:
        example = f'Похожий пример из моего портфолио — {case["name"]} ({case["scope"].split(";")[0]}).'
        if direct:
            example += f' {case["url"]}'
        parts.append(example)
    if screen:
        parts.append(DESIGN_NOTE)
    parts.append(question)

    notes = ['Шлюз ИИ пока не подготовил текст. Проверьте черновик по объявлению']
    if not direct:
        notes.append('Проверьте актуальность объявления и способ отклика в оригинале')
    if order['service_fit'] == 'partial':
        notes.append('Согласуйте выделенную часть работ')
    if response['required_keys'] or response['unparsed_requirements']:
        notes.append('Сверьте обязательные требования и приложите запрошенные материалы')
    parts.append('Перед отправкой: ' + '. '.join(notes) + '.')
    return '\n\n'.join(parts)
