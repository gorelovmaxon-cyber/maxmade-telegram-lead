"""Local MAXMADE fit and response guidance. No network or customer messaging."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

TRUNCATED_MARKER = '[Фрагмент сокращён; полный текст проверить в оригинале]'


@lru_cache(maxsize=1)
def profile() -> dict:
    return json.loads((Path(__file__).parent / 'config' / 'order-profile-v2.json').read_text(encoding='utf-8'))


def flat(text: str, limit: int = 350) -> str:
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def negated(s: str, start: int, end: int) -> bool:
    """Local phrase negation, bounded by punctuation and adversative clauses."""
    before = re.split(r'[.!?;,]|\bно\b|\bоднако\b', s[:start])[-1]
    after = s[end:]
    return bool(re.search(r'\b(?:не|без|никак\w*|нет)(?:\s+\w+){0,3}\s*$', before)
                or re.match(r'\s+(?:не\s+(?:нуж\w*|требу\w*|предусмотр\w*)|нет\b)', after))


def positive_matches(pattern: str, s: str) -> list[re.Match]:
    return [m for m in re.finditer(pattern, s) if not negated(s, m.start(), m.end())]


def employment(s: str) -> str:
    staff = positive_matches(r'в\s+штат\w*|штатн\w*\s+ваканси\w*|полный\s+день|оклад\w*|трудоустройств\w*|полная\s+занятость|зарплат\w*|оформлен\w*\s+по\s+тк', s)
    explicit_no_staff = bool(re.search(r'не\s+(?:в\s+штат|штатн\w*\s+ваканси)|без\s+трудоустройств|трудоустройств\w*\s+не\s+(?:нуж\w*|требу\w*)', s))
    alternative = bool(re.search(r'в\s+штат\w*.{0,25}(?:или|либо).{0,15}проект|проект\w*.{0,25}(?:или|либо).{0,15}в\s+штат', s))
    if staff and (explicit_no_staff or alternative):
        return 'ambiguous'
    return 'vacancy' if staff else 'project'


def without_links(value: str) -> str:
    return flat(re.sub(r'https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-zа-я]{2,}(?:/\S*)?', '', value, flags=re.I), 600)


def assess(body: str, *, confirmed: dict[str, str] | None = None) -> dict:
    # Only explicit caller-supplied owner confirmations may populate an offer.
    # The automatic pipeline never interprets a customer's budget as our price.
    confirmed = confirmed or {}
    input_truncated = len(flat(body, 2001)) > 2000 or TRUNCATED_MARKER in body
    text = flat(body, 2000)
    s = text.lower().replace('ё', 'е')
    services = [key for key, item in profile()['services'].items() if re.search(item['pattern'], s)]
    # A deliverable and a request are required. The presence of a service word
    # anywhere in a promotional catalogue is not hiring evidence.
    request = re.search(
        r'\b(?:нуж(?:ен|на|но|ны)|ищ(?:у|ем|ется|ут)|требуется|требуются|заказать|закажу|подскажите|кто\s+сделает)\b'
        r'.{0,100}?(?:сайт|лендинг|посадочн|веб.?дизайн|разработчик|дизайнер|интерфейс|редизайн|доработ|презентаци|верстальщик|tilda|тильд|figma|фигм)', s)
    if request is None:
        request = re.search(r'(?:сайт|лендинг|презентаци\w*).{0,30}(?:нужен|нужна|нужно\s+(?:сделать|создать|доработать))', s)
    unrelated_request = re.search(r'\b(?:нужен|нужна|ищу|ищем|требуется)\s+(?:telegram.бот|телеграм.бот|бот\b|парсер|юрист|бухгалтер|копирайтер)', s)
    if unrelated_request and request and unrelated_request.start() == request.start():
        request = None
        services = []  # A passing reference to an existing site is not its commission.
    offer = re.search(
        r'#помогу\b|ищу\s+работу|ищу\s+ваканс|рассмотрю\s+ваканс|делаю\s+сайты|создаю\s+сайты|беру\s+заказы|мое\s+портфолио|'
        r'я\s+.{0,40}(?:веб.дизайнер|графический|разработчик)|разработаем\s+почти|могу\s+помочь\s+с\s+доработкой|'
        r'предлагаю\s+(?:свои\s+)?услуги|готов(?:ые|ых)\s+бот|цены\s+ниже\s+рыночных', s)
    if not request and re.search(r'(?:разработка|создание|дизайн).{0,35}(?:сайт|лендинг|презентаци)', s) and re.search(r'пишите|обращайтесь|заказ(?:ы|ать)|мои\s+услуги', s):
        offer = True
    labels = profile()['labels']
    if re.search(r'ищу\s+работу|ищу\s+ваканс|рассмотрю\s+ваканс', s):
        kind, reason = 'offer', 'Автор ищет работу; заказа для нашего исполнения нет'
    elif offer and request:
        kind, reason = 'ambiguous', 'Смешаны предложение услуг и поиск исполнителя; роль автора не ясна'
    elif offer:
        kind, reason = 'offer', 'Автор предлагает свои услуги; задачи для нашего исполнения нет'
    elif request:
        if employment(s) == 'vacancy':
            kind, reason = 'vacancy', 'Штатная вакансия; проектный заказ не подтверждён'
        elif employment(s) == 'ambiguous':
            kind, reason = 'ambiguous', 'Противоречивые или альтернативные условия найма; проектный формат не подтверждён'
        elif re.search(r'постоянн\w*\s+сотруднич|долгосрочн\w*\s+сотруднич|партнер|субподряд|пул\s+(?:дизайнер|исполнител)|(?:набира|ищ\w*).{0,70}(?:в команду|для сотруднич)|сотрудничеств\w*.{0,30}(?:постоянн|долгосроч)', s):
            kind, reason = 'partnership', 'Набор подрядчиков / партнёрство; отдельный заказ не подтверждён'
        elif services:
            kind, reason = 'demand', 'Есть запрос на результат в пределах опубликованных услуг MAXMADE'
        else:
            kind, reason = 'ambiguous', 'Ищут исполнителя, но подходящий результат работ не указан'
    elif re.search(r'набира\w*.{0,35}дизайнер', s) and services:
        kind, reason = 'partnership', 'Набор дизайнеров; отдельный заказ не подтверждён'
    elif services or re.search(r'дизайнер|разработчик', s):
        kind, reason = 'ambiguous', 'Недостаточно данных: упоминание услуги не подтверждает заказ'
    else:
        kind, reason = 'other', 'Запроса на опубликованные услуги MAXMADE нет'

    fit = 'подходит по виду работ' if services and kind in ('demand', 'partnership') else 'недостаточно данных'
    if kind == 'offer':
        fit = 'не подходит: предложение услуг'
    elif kind == 'vacancy':
        fit = 'не подходит для проектного отбора: штатная вакансия'
    elif kind == 'other':
        fit = 'вне подтверждённого профиля'
    # Own code is not evidence of arbitrary stack/backend expertise.
    uncertain = positive_matches(r'\b(?:react|vue|laravel|django|wordpress|битрикс|webflow|php|node\.?js|python|shopify)\b|telegram.бот|телеграм.бот|парсер', s)
    if uncertain and kind in ('demand', 'partnership'):
        fit = 'частичное: обязательная технология / функция требует проверки компетенции'

    response = response_requirements(text)
    case = None
    approach = ''
    if kind in ('demand', 'partnership') and services:
        if 'presentation' in services:
            case_id = 'presentation-torfactiv'
        elif 'interface' in services and not re.search(r'под\s+ключ|сверст|разработ', s):
            case_id = 'exchanger-frame'
        elif re.search(r'b2b|многостранич|компани|стенд|event', s):
            case_id = 'forumtech'
        elif re.search(r'вебинар|обучен|курс|эксперт', s):
            case_id = 'fomicheva-brand'
        elif 'website' in services:
            case_id = 'anna-fomicheva-landing'
        else:
            case_id = 'exchanger-frame'
        case = profile()['cases'][case_id]
        approach = profile()['services'][services[0]]['approach']
    need = 'Задача клиента не указана'
    if request:
        # Preserve the surrounding task, not just the word "website".
        end = re.search(r'[.!?](?:\s|$)', text[request.end():])
        stop = request.end() + end.start() if end else min(len(text), request.start() + 280)
        need = text[request.start():stop]
    if re.search(r'вебинар|регистраци', s) and 'website' in services:
        approach = 'собрать страницу вокруг программы и пользы вебинара, добавить понятную форму регистрации и проверить её работу на телефоне'
    elif re.search(r'b2b|выставочн|стенд', s) and 'website' in services:
        approach = 'показать направления работ и подходящие проекты, затем вести посетителя к брифу или форме заявки; структуру согласовать под вашу аудиторию'
    elif re.search(r'редизайн|переделать', s) and 'website' in services:
        approach = 'сначала разобрать текущую страницу и описанную вами проблему, затем предложить изменения структуры и первого экрана; объём переделки согласовать после просмотра'
    # Do not ask the same material question regardless of the author's facts.
    materials = bool(positive_matches(r'(?:все\s+)?материал\w*\s+(?:уже\s+)?(?:есть|готов\w*)|(?:текст\w*|дизайн|макет\w*)[^.!?]{0,40}(?:готов\w*|есть)|(?:готов\w*)\s+(?:текст\w*|дизайн|макет\w*|материал\w*)', s))
    platform = bool(positive_matches(r'\btilda\b|тильд\w*|\bfigma\b|фигм\w*|платформ\w*[^.!?]{0,30}(?:выбирайте|на\s+ваш\s+выбор|люб\w*)', s))
    clarification = ''
    if kind == 'demand':
        if not materials:
            clarification = 'Какие исходные материалы уже есть?'
        elif 'website' in services and not platform:
            clarification = 'На какой платформе нужно реализовать страницу?'
    if materials and 'website' in services:
        approach = 'начать с проверки готовых материалов и требований, затем реализовать согласованный макет с адаптивами и проверить формы; дополнять тексты и дизайн только при выявленной необходимости'

    missing, conflicts, provided = [], [], []
    if input_truncated:
        missing.append('полный текст требований: сохранён только фрагмент, открыть оригинал')
    for key in response['required_keys']:
        label = REQUIREMENTS[key][0]
        if key == 'url':
            conflicts.append('Автор требует URL; первое обращение Максима должно быть без ссылки')
        elif key == 'form':
            missing.append('заполненная анкета по требованиям автора')
        elif not confirmed.get(key):
            missing.append(label)
        elif without_links(confirmed[key]) != flat(confirmed[key], 600):
            conflicts.append(f'Подтверждённое поле «{label}» содержит URL; первое обращение без ссылки')
        else:
            provided.append(f'{label}: {flat(confirmed[key], 250)}')
    if response['unparsed_requirements']:
        missing.append('уточнить и выполнить дополнительные требования автора: ' + '; '.join(response['unparsed_requirements']))
    if response['route'] == 'unknown':
        missing.append('способ отклика: открыть оригинал и уточнить маршрут')
    elif response['route'] == 'ambiguous':
        missing.append('однозначный способ отклика: в тексте противоречивые маршруты')
    elif response['route'] not in ('private', 'contact'):
        missing.append('подготовить отклик в указанном маршруте: ' + ROUTES[response['route']])
    if response['prohibited_private'] and response['route'] in ('private', 'contact', 'unknown'):
        conflicts.append('Автор запретил личные сообщения; личный отклик не готовится')
    if uncertain:
        missing.append('подтверждение обязательной технологии / функции Максимом')

    draft = ''
    draft_status = 'not_applicable'
    if kind in ('demand', 'partnership'):
        draft_status = 'not_ready'
        if not case:
            missing.append('подтверждённый релевантный кейс')
        if kind == 'partnership':
            missing.append('согласовать предмет партнёрства; не подменять его прямым заказом')
        if kind == 'demand' and case and not missing and not conflicts:
            draft_status = 'review'
            draft = (f'Здравствуйте! По вашей задаче «{without_links(flat(need, 140))}» предлагаю такой подход: {approach}. '
                     f'На моём сайте есть кейс {case["name"]} — {case["scope"]}. '
                     + (' '.join(provided) + '. ' if provided else '')
                     + clarification + ' Готов показать этот кейс и обсудить применение решений к вашей задаче.')
    return {'version': profile()['version'], 'kind': kind, 'label': labels.get(kind, 'Вне профиля'),
            'reason': reason, 'fit': fit, 'services': services, 'need': need,
            'case': case, 'response': response, 'approach': approach, 'draft': draft,
            'draft_status': draft_status, 'missing': list(dict.fromkeys(missing)),
            'conflicts': list(dict.fromkeys(conflicts)), 'clarification': clarification}


ROUTES = {'private': 'личный контакт', 'contact': 'указанный контакт', 'form': 'анкета / форма',
          'platform_bot': 'бот площадки', 'comment': 'комментарий / обсуждение',
          'email': 'электронная почта', 'exchange': 'биржа / площадка',
          'unknown': 'не указан', 'ambiguous': 'маршруты противоречат друг другу'}

# Required owner fields, never derived from a customer's budget or deadline.
REQUIREMENTS = {
    'price': ('цена предложения Максима', r'цен[уаы]|стоимост|расценк'),
    'case_material': ('материал кейса / портфолио для отклика', r'кейс|портфолио|выполненн\w*\s+(?:работ|сайт)'),
    'timeline': ('срок исполнения Максима', r'срок'),
    'form': ('анкету', r'анкет|форму'),
    'experience': ('подтверждённый опыт по запросу автора', r'опыт|стаж'),
    'resume': ('резюме', r'резюме'),
    'terms': ('условия сотрудничества', r'услови'),
    'phone': ('телефон для отклика', r'телефон'),
    'tax_id': ('реквизиты / ИНН', r'\bинн\b|реквизит'),
    'availability': ('подтверждённая доступность Максима', r'когда\s+(?:сможете|можете|готовы)\s+начать|дат\w*\s+начала'),
    'url': ('URL, запрошенный автором', r'\burl\b|ссылк'),
}
REQUEST_LABELS = {'price': 'цену / стоимость', 'case_material': 'кейсы / портфолио', 'timeline': 'срок', 'form': 'анкету'}


def response_requirements(text: str) -> dict:
    s = text.lower().replace('ё', 'е')
    sentences = re.split(r'(?<=[.!?;])\s+', text)
    destinations, evidence, requirement_quotes, required, unparsed = [], [], [], set(), []
    contact = None
    prohibited_private = False
    for sentence in sentences:
        low = sentence.lower().replace('ё', 'е')
        private_word = re.search(r'личк\w*|личн\w*\s+сообщ\w*|\bлс\b|\bл/с\b', low)
        if private_word and negated(low, private_word.start(), private_word.end()):
            prohibited_private = True
        action_pattern = r'пришл\w*|присыл\w*|отправ\w*|укаж\w*|напиш\w*|заполн\w*|прилож\w*|обязател\w*|в\s+отклике\s*[:—-]|(?:в\s+отклике\s+)?нужны\b'
        action = positive_matches(action_pattern, low)
        route_action = positive_matches(r'пишите|писать|отклик\w*|отправ\w*|пришл\w*|присыл\w*|заполн\w*|для\s+связи', low)
        local_routes = []
        if route_action:
            form = positive_matches(r'анкет\w*|форм[уае]\b', low)
            comments = positive_matches(r'комментар\w*|обсужден\w*', low)
            mail = re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', sentence)
            handle = re.search(r'(?<![\w.])@([a-zA-Z0-9_]{5,32})|(?:https?://)?t\.me/([a-zA-Z0-9_]{5,32})', sentence)
            if form:
                local_routes.append('form')
            if comments:
                local_routes.append('comment')
            if mail:
                local_routes.append('email')
                contact = mail.group()
            if positive_matches(r'бирж\w*|площадк\w*', low):
                local_routes.append('exchange')
            bot_words = positive_matches(r'(?:через|в)\s+бот\w*', low)
            if bot_words:
                local_routes = [r for r in local_routes if r != 'exchange']
                local_routes.append('platform_bot')
            if handle and not negated(low, handle.start(), handle.end()):
                username = handle.group(1) or handle.group(2)
                contact = '@' + username
                if username.lower().endswith('bot'):
                    local_routes.append('platform_bot')
                elif not local_routes:
                    local_routes.append('private' if private_word and not prohibited_private else 'contact')
            if private_word and not negated(low, private_word.start(), private_word.end()) and not local_routes:
                local_routes.append('private')
        if local_routes:
            destinations.extend(local_routes)
            evidence.append(sentence.rstrip('.!?;'))

        # A word like "price" in a budget statement is not an instruction.
        # Check each requested field in the same sentence as a positive action.
        found = False
        if action:
            for key, (_, pattern) in REQUIREMENTS.items():
                for match in re.finditer(pattern, low):
                    if not negated(low, match.start(), match.end()):
                        # Require a command before the field, or an explicit
                        # mandatory marker after it ("URL обязателен").
                        if any(a.start() <= match.start() or re.match(r'\s+обязател', low[match.end():]) for a in action):
                            required.add(key)
                            found = True
            if found:
                requirement_quotes.append(sentence.rstrip('.!?;'))
            # Do not silently drop an unknown field alongside a known price or
            # timeline. Strip understood objects from each command's remainder;
            # unrecognized instructions block a finished reply conservatively.
            for i, command in enumerate(action):
                tail = low[command.end():action[i+1].start() if i+1 < len(action) else len(low)]
                tail = re.sub(r'https?://\S+|@[a-z0-9_]+', '', tail)
                for _, pattern in REQUIREMENTS.values():
                    tail = re.sub(r'(?:' + pattern + r')\w*', '', tail)
                tail = re.sub(r'\b(?:в|на|и|или|ваш\w*|сво\w*|пожалуйста|обязательн\w*|для|отклик\w*|по|работ\w*|сотрудничеств\w*|лс|личк\w*|сообщени\w*)\b', '', tail)
                tail = re.sub(r'[\W\d_]+', '', tail)
                mandatory_suffix = low[command.start():].startswith('обязател') and not tail
                if tail and not (local_routes and not found):
                    unparsed.append(flat(sentence, 160))
                elif not found and not local_routes and not mandatory_suffix:
                    unparsed.append(flat(sentence, 160))

    unique = set(destinations)
    # "Пишите в лс. Контакт @..." is one route; form + bot are kept
    # conservative unless the text clearly names only one destination.
    if unique <= {'private', 'contact'} and unique:
        route = 'private' if 'private' in unique else 'contact'
    else:
        route = next(iter(unique)) if len(unique) == 1 else 'ambiguous' if unique else 'unknown'
    return {'route': route, 'instruction': ' | '.join(dict.fromkeys(evidence)) or 'Не указан',
            'contact': contact,
            'requested': [REQUEST_LABELS.get(key, REQUIREMENTS[key][0]) for key in REQUIREMENTS if key in required],
            'required_keys': [key for key in REQUIREMENTS if key in required],
            'requirement_quotes': list(dict.fromkeys(requirement_quotes)),
            'unparsed_requirements': list(dict.fromkeys(unparsed)), 'prohibited_private': prohibited_private,
            'paid_access': bool(positive_matches(r'платн\w*|подписк\w*', s)),
            'unknown': [label for label, pattern in [('бюджет', r'\d.{0,12}(?:₽|руб|тыс)|бюджет\s*[:—-]?\s*\d'),
                                                    ('срок', r'до\s+\d|срок\s*[:—-]?\s*\d|\d+\s*(?:дней|дня|недел)')]
                        if not re.search(pattern, s)]}


def card_guidance(body: str) -> str:
    a = assess(body or '')
    r = a['response']
    lines = [f'Соответствие MAXMADE: {a["fit"]}', f'Задача: {a["need"]}',
             f'Кейс: {a["case"]["name"] + " — " + a["case"]["scope"] if a["case"] else "не подбирается: подходящий заказ не подтверждён"}',
             f'Способ отклика: {ROUTES[r["route"]]}',
             f'Отклик — цитата из поста: {flat(r["instruction"], 380)}',
             f'Просят прислать: {", ".join(r["requested"]) or "не указано"}',
             f'Не указано: {", ".join(r["unknown"]) or "бюджет и срок найдены в тексте; сверить оригинал"}']
    if r['contact']:
        lines.append('Контакт из инструкции: ' + r['contact'])
    if r['requirement_quotes']:
        lines.append('Требования — цитата: ' + flat(' | '.join(r['requirement_quotes']), 450))
    if r['paid_access']:
        lines.append('В тексте указан платный доступ; условия проверить в оригинале.')
    if a['draft_status'] == 'not_ready':
        lines.append('Черновик: Не готов к отправке')
        if a['missing']:
            lines.append('Недостающее: ' + '; '.join(a['missing']))
        if a['conflicts']:
            lines.append('Конфликт: ' + '; '.join(a['conflicts']))
    if a['draft']:
        lines.append('Черновик: требует проверки Максимом, не отправлен. ' + a['draft'])
    text = '\n'.join(lines)
    if len(text.encode('utf-16-le')) // 2 > 2850:
        # Never cut off a readiness warning in a long post. Keep all recognized
        # fields, explicitly block the draft and direct the owner to the post.
        lines = [f'Соответствие MAXMADE: {a["fit"]}',
                 f'Способ отклика: {ROUTES[r["route"]]}',
                 f'Просят прислать: {", ".join(r["requested"]) or "не указано"}',
                 'Черновик: Не готов к отправке — длинные требования, сверить полный оригинал.']
        if a['conflicts']:
            lines.append('Конфликт: ' + '; '.join(a['conflicts']))
        # The original is always retained by monitor.render. Long free-form
        # quotes are not represented as a complete list of author requirements.
        lines.append('Недостающее: ' + '; '.join(x for x in a['missing'] if not x.startswith('уточнить и выполнить')))
        lines.append('Цитата (фрагмент): ' + flat(r['instruction'], 180))
        return '\n'.join(lines)
    return text
