"""Local role-and-deliverable selection. No networking, contacts or LLM calls."""
from __future__ import annotations
import re

SERVICES={
 'website':r'\bсайт(?:а|ы|ов|у|ом|е|ами|ах)?\b|лендинг\w*|посадочн\w*\s+страниц\w*|\btilda\b|тильд\w*',
 'interface':r'\bfigma\b|фигм\w*|ui.?kit|\bui.?ux\b|интерфейс\w*|веб.?дизайн\w*|дизайн\w*\s+сайт\w*',
 'presentation':r'презентаци\w*|\bслайд\w*',
}
REQUEST=r'\b(?:нуж(?:ен|на|но|ны)|ищ(?:у|ем|ется|ут)|требу(?:ется|ются)|закажу|заказать|набираем|кто\s+(?:сделает|возьмется)|помогите\s+(?:сделать|доработать|сверстать))\b'
PROFESSIONAL=r'веб.?дизайнер\w*|разработчик\w*|верстальщик\w*|дизайнер\w*'
OUTSIDE_TARGET=r'^\s*#?(?:юрист\w*|бухгалтер\w*|копирайтер\w*|сайтолог\w*|редактор\w*|(?:telegram|телеграм)[ -]?бот\w*|бот\b|парсер\w*|менеджер\w*\s+по\s+продаж\w*)'
OUTSIDE_RESULT=r'визуал\w*\s+товар\w*|карточ\w*\s+товар\w*|облож\w*|видеомонтаж\w*|монтаж\w*|сторис|\breels\b|\bshorts\b|интерьер\w*'


def negated(s,start,end):
    before=re.split(r'[.!?;,]|\bно\b|\bоднако\b',s[:start])[-1]
    return bool(re.search(r'\b(?:не(?:\s+(?:ищ\w*|нуж\w*|требу\w*|очень|на))?|без|никак\w*|нет)\s*$',before)
      or re.match(r'\s+(?:не\s+(?:нуж\w*|требу\w*|предусмотр\w*)|нет\b)',s[end:]))


def positives(pattern,s):
    return [m for m in re.finditer(pattern,s) if not negated(s,m.start(),m.end())]


def services_in(s):
    return [name for name,pattern in SERVICES.items() if positives(pattern,s)]


def select(text):
    s=text.lower().replace('ё','е');services=services_in(s)
    sales=bool(positives(r'менеджер\w*\s+по\s+продаж\w*|партнер\w*\s+по\s+продаж\w*|приводи\w*\s+клиент\w*|ты\s+занимаешься\s+поиском\s+клиент\w*|разработк\w*\s+и\s+запуск\s+беру\s+на\s+себя',s))
    offer=bool(re.search(r'ищу\s+(?:работу|ваканс|заказы)|рассмотрю\s+ваканс|предлага\w*\s+(?:свои\s+)?услуги|наши\s+услуги|с\s+чем\s+можем\s+помочь|что\s+мы\s+предлагаем|реклама\s+вашего\s+оффера|продвигаем\s+любые\s+офферы|созда[ею]\w*.{0,65}(?:сайт|бот|лендинг)|дела[ею]\w*.{0,35}(?:сайт|лендинг)|занима(?:юсь|емся).{0,60}(?:сайт|дизайн)|#помогу\b|мое\s+портфолио|сайты\s+под\s+ключ.{0,100}(?:пишите|обращайтесь)|настрой\s+свой\s+мониторинг',s))
    offer |= bool(re.search(r'(?:разработка|создание)\s+(?:ботов\s+и\s+)?сайтов.{0,120}(?:пишите|цены|могу\s+помочь)',s))
    offer |= bool(re.search(r'\b(?:я|мы)\b.{0,50}(?:сайт|дизайн).{0,40}занима(?:юсь|емся)',s))
    offer |= bool(re.search(r'(?:рекомендую\s+вам\s+бота|есть\s+бесплатный\s+тариф|бесплатно\s+и\s+без\s+регистрации|запишитесь.{0,100}вебинар|на\s+бесплатном\s+вебинаре|сейчас\s+ищу.{0,70}фидбек)',s))
    staff=bool(positives(r'в\s+штат\w*|штатн\w*\s+ваканси\w*|полный\s+день|оклад\w*|трудоустройств\w*|полная\s+занятость|зарплат\w*|оформлен\w*\s+по\s+тк',s))
    staff_alternative=bool(re.search(r'в\s+штат\w*.{0,25}(?:или|либо).{0,15}проект|проект\w*.{0,25}(?:или|либо).{0,15}в\s+штат',s))
    staff_alternative |= staff and bool(re.search(r'не\s+(?:в\s+штат|штатн\w*\s+ваканси)|без\s+трудоустройств|трудоустройств\w*\s+не\s+(?:нуж\w*|требу\w*)',s))
    project=bool(re.search(r'есть.{0,30}(?:интересн\w*\s+)?проект|конкретн\w*\s+проект|разов\w*\s+(?:заказ|проект|задач)|проектн\w*\s+(?:заказ|работ)|(?:сделать|создать|сверстать|доработать|переделать)\s+(?:нам\s+)?(?:сайт|лендинг|интерфейс|презентаци)',s))
    project |= bool(positives(r'(?:нужен|нужна|нужно|закажу|заказать)\s+(?:нам\s+)?(?:сайт\b|лендинг\w*|интерфейс\w*|презентаци\w*)',s))
    incoming=bool(re.search(r'(?:постоянн\w*|долгосрочн\w*)\s+сотруднич|субподряд|пул\s+(?:дизайнер|исполнител)|передава\w*.{0,55}(?:заказ|сайт|лендинг)|(?:ищ\w*|набира\w*).{0,70}(?:подрядчик|в\s+команду|для\s+сотруднич)',s))
    task_pool=bool(re.search(r'\bв\s+подрядку\b|(?:разбира\w*|переда\w*|распределя\w*).{0,35}поток\s+задач|типичн\w*.{0,25}(?:задач|последнего\s+времени)',s))
    incoming |= task_pool
    hiring=bool(positives(r'рекрутер|открыт\w*\s+ваканси|с\s+вакансией|вакансия|этой\s+вакансией|бонус\w*\s+за\s+переработ|оклад\w*',s))
    hiring_bundle=bool(re.search(r'рекрутер|открыт\w*\s+ваканси|с\s+вакансией',s)) and not project
    closed=positives(r'исполнитель\s+найден|уже\s+нашли\s+исполнителя|задача\s+закрыта|вакансия\s+закрыта',s)
    requests=[];outside_request=False
    for match in re.finditer(REQUEST,s):
        if negated(s,match.start(),match.end()):continue
        end=next((m.end() for m in re.finditer(r'[.!?;](?:\s|$)',s[match.end():]) ),None)
        stop=min(len(s),match.end()+(end if end is not None else 220))
        tail=s[match.end():stop];local=services_in(tail)
        if re.match(r'\s+(?:работу|ваканс\w*|заказы|клиентов|продавц\w*)\b',tail):continue
        unrelated=bool(re.search(OUTSIDE_TARGET,tail))
        second_web_task=bool(re.search(r'(?:и|а\s+также|отдельно)\s+(?:еще\s+)?(?:разработчик\w*|веб.?дизайнер\w*|верстальщик\w*)',tail))
        if unrelated and not second_web_task:outside_request=True;continue
        role=bool(re.search(PROFESSIONAL,tail))
        if role and project:local=list(dict.fromkeys(local+services))
        if not local and role and (project or incoming):local=services
        if not local:
            outside_request |= bool(re.search(OUTSIDE_RESULT,tail));continue
        # Generic social/merchandise design does not turn into a web result.
        if re.search(OUTSIDE_RESULT,tail) and not re.search(SERVICES['website']+'|'+SERVICES['interface']+'|'+SERVICES['presentation'],tail):outside_request=True;continue
        requests.append((match.start(),stop,local))
    if not requests:
        reverse=re.search(r'(?:\bсайт\w*|лендинг\w*|презентаци\w*).{0,30}(?:нужен|нужна|нужно\s+(?:сделать|создать|доработать))',s)
        if reverse and not re.search(r'\bне\b',reverse.group()) and not negated(s,reverse.start(),reverse.end()):requests.append((reverse.start(),reverse.end(),services_in(reverse.group())))
    if closed:
        requests=[r for r in requests if r[0]>closed[-1].end()]
    independent_after_offer=bool(re.search(r'отдельно\s+(?:нуж|ищ)|для\s+(?:своего|моего|нашего)\s+(?:сайта|проекта)',s))
    queue='none';task=None;fit='outside';role='participant';kind='other';reason='Запроса на опубликованные услуги MAXMADE нет';need='Задача клиента не указана'
    selected_services=[]
    if sales and not (requests and independent_after_offer):
        kind='other';task=True;role='provider_seeking_sales';reason='Ищут продажи / клиентов для другого исполнителя, не исполнение работ MAXMADE';need='Приводить клиентов или продавать услуги другого исполнителя'
    elif (staff or hiring_bundle or (hiring and not project)) and (requests or services or re.search(r'сайтолог|дизайнер',s)):
        kind='ambiguous' if staff_alternative else 'vacancy';queue='review' if staff_alternative else 'employment';task=True;role='recruiter_or_employer';fit='unknown'
        reason='Найм / подбор вакансий; конкретный проектный результат не подтверждён' if not staff_alternative else 'Альтернативные условия штат/проект требуют проверки'
        need='Найм специалиста; проектный формат не подтверждён';selected_services=services
    elif offer and not independent_after_offer:
        kind='offer';task=False;role='service_provider';reason='Автор предлагает собственные услуги / продукт, задачи для нашего исполнения нет'
    elif requests:
        start,stop,selected_services=requests[-1];task=True;role='requester';fit='matched'
        kind='partnership' if task_pool or incoming and not (project and not re.search(r'на\s+постоянн|пул|субподряд|передава',s)) else 'demand'
        queue='partnership' if kind=='partnership' else 'main'
        reason='Ищут подрядчика для передачи подходящих работ; отдельная очередь партнёрства' if kind=='partnership' else 'Есть конкретный запрос на подходящий результат MAXMADE'
        need=text[start:stop].strip()
    elif closed:
        kind='closed_signal';task=False;reason='Опубликовано сообщение о закрытии задачи'
    elif offer:
        kind='offer';task=False;role='service_provider';reason='Автор предлагает собственные услуги / продукт, задачи для нашего исполнения нет'
    elif outside_request:
        task=True;role='requester';reason='Требуемая работа вне подтверждённого профиля MAXMADE'
    elif services or re.search(r'дизайнер|разработчик',s):
        kind='ambiguous';queue='review';role='unknown';fit='unknown';reason='Упоминание специальности / темы без подтверждённого подходящего задания';task=None
    if task and selected_services and positives(r'\b(?:react|vue|laravel|django|wordpress|битрикс|webflow|php|node\.?js|python|shopify)\b|telegram.бот|телеграм.бот|парсер',s):fit='needs_verification'
    if queue=='main' and not selected_services:queue='review';fit='unknown'
    return {'kind':kind,'reason':reason,'queue':queue,'task_exists':task,'service_fit':fit,
      'author_role':role,'services':selected_services,'need':need}
