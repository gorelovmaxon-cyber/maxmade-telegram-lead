"""Transport- and business-independent message roles. No service vocabulary."""
import re

RULES_VERSION='local-role-rules-20261109-v3-r1'
REQUEST=r'\b(?:нуж(?:ен|на|но|ны)|ищ(?:у|ем|ется|ут)|требу(?:ется|ются)|закажу|заказать|набираем)\b'


def negated(text,start,end):
    before=re.split(r'[.!?;,]|\bно\b|\bоднако\b',text[:start])[-1]
    return bool(re.search(r'\b(?:не(?:\s+(?:ищ\w*|нуж\w*|требу\w*|очень|на))?|без|нет)\s*$',before)
                or re.match(r'\s+(?:не\s+(?:нуж\w*|требу\w*|предусмотр\w*)|нет\b)',text[end:]))


def positive(pattern,text):
    return [m for m in re.finditer(pattern,text) if not negated(text,m.start(),m.end())]


def classify(text):
    s=text.lower().replace('ё','е')
    request=[m for m in positive(REQUEST,s) if re.match(r'\s*#?\w',s[m.end():])]
    closed=positive(r'исполнитель\s+найден|задача\s+закрыта|вакансия\s+закрыта|уже\s+нашли\s+исполнителя',s)
    request=[m for m in request if not closed or m.start()>closed[-1].end()]
    staff=positive(r'в\s+штат|оклад\w*|полный\s+день|трудоустройств\w*|полная\s+занятость',s)
    pool=positive(r'субподряд|в\s+подрядку|поток\s+задач|пул\s+исполнител\w*|передава\w*\s+заказ\w*|постоянн\w*\s+сотруднич',s)
    sales=positive(r'приводи\w*\s+клиент\w*|партнер\w*\s+по\s+продаж\w*|менеджер\w*\s+по\s+продаж\w*',s)
    offers=positive(r'предлага\w*\s+(?:свои\s+)?услуги|наши\s+услуги|оказыва\w*\s+услуги|ищу\s+работу|ищу\s+заказы|мое\s+портфолио',s)
    if closed and not request:category='closed_signal'
    elif sales:category='partnership'
    elif staff:category='vacancy'
    elif pool:category='partnership'
    elif offers:category='offer'
    elif request:category='demand'
    else:category='ambiguous'
    return {'category':category,'rules_version':RULES_VERSION,
      'task_exists':True if request or staff or pool or sales else False if closed or offers else None,
      'signals':{'request':bool(request),'employment':bool(staff),'subcontract_pool':bool(pool),
                 'sales_direction':bool(sales),'offer':bool(offers),'closed':bool(closed)}}
