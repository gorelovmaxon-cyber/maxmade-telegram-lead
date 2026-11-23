"""MAXMADE adapter: explicit requested result and work direction, not keywords."""
import re
from message_classification import positive
from selection_facts_v32 import facts,quote,REQUEST

def select(text,profile):
    s,signals,clauses=facts(text)
    patterns={k:v.get('match_pattern',v['pattern']) for k,v in profile['services'].items()}
    def services(t):return [k for k,p in patterns.items() if positive(p,t)]
    all_services=services(s)
    evidence=[quote(text,m.start(),m.end(),k,'role.'+k) for k,ms in signals.items() for m in ms]
    evidence.extend(quote(text,m.start(),m.end(),'topic.'+k,'profile.mention') for k,p in patterns.items() for m in positive(p,s))
    out={'kind':'other','queue':'none','task_exists':None,'service_fit':'unknown','author_role':'unknown',
      'services':[],'need':'Задача для исполнителя не подтверждена','reason':'Подходящий запрос не подтверждён',
      'work_direction':'unknown','cooperation_format':'unknown','constraints':[],'unknown_details':[],
      'evidence':evidence,'rule_ids':[],'requested_result':None}
    def done(kind,route,reason,rule,task=None,fit='unknown',role='unknown'):
        out.update(kind=kind,queue=route,reason=reason,task_exists=task,service_fit=fit,author_role=role)
        out['rule_ids'].append(rule);return out
    requests=[];outside=[]
    for a,b in clauses:
        part=s[a:b];found=services(part)
        for m in positive(REQUEST,part):
            tail=part[m.end():];local=services(tail)
            target=positive(profile['outside_target_pattern'],tail)
            if target and target[0].start()<8 and not re.search(r'(?:и|также|отдельно).{0,15}(?:разработчик|верстальщик|веб.?дизайнер)',tail):
                t=target[0];evidence.append(quote(text,a+m.end()+t.start(),a+m.end()+t.end(),'outside_result','profile.outside_target'))
                outside.append((a+m.start(),b));continue
            if not found or re.match(r'\s*(?:работу|заказы|клиентов|партнер\w*\s+по\s+продаж)',tail):continue
            if not local:
                if not re.search(r'сверст|передел|исправ|доработ|собра|созда|дизайнер|разработчик|верстальщик|передад|есть\s+заказчик',part):continue
                local=found
            requests.append((a+m.start(),b,local,a))
    pool=bool(signals['pool'])
    if pool and signals['request'] and all_services and not requests:
        requests=[(signals['request'][0].start(),len(text),all_services,0)]
    closed=signals['closed']
    if closed:requests=[r for r in requests if r[0]>closed[-1].end()]
    providers=positive(r'\b(?:'+profile['provider_pattern']+')',s)
    evidence.extend(quote(text,m.start(),m.end(),'offer','profile.self_promotion') for m in providers)
    advertisement=bool(signals['offer'] or providers)
    independent=bool(re.search(r'отдельно\s+(?:нуж|ищ)|для\s+(?:своего|моего|нашего)\s+(?:сайта|проекта)',s))
    own_request=re.search(r'(?:я|мы)[^.!?]{0,30}(?:дизайнер|разработчик)[^.!?]{0,35}(?:сейчас\s+)?(?:нуж|ищ)',s)
    independent |= bool(own_request and not re.search(r'если\s+(?:вам|вашему)',own_request.group()))
    if signals['sales'] and not (requests and independent):
        out['work_direction']='clients_to_author'
        return done('other','none','Ищут клиентов / продажи для другого исполнителя','direction.outbound_sales',True,'outside','performer')
    if advertisement and not (requests and independent):
        return done('offer','none','Автор предлагает свои услуги, а не передаёт нам работу','role.self_promotion',False,'outside','performer')
    alternative=bool(re.search(r'в\s+штат.{0,30}(?:или|либо).{0,15}проект|проект.{0,25}(?:или|либо).{0,15}в\s+штат',s))
    alternative |= bool(signals['staff'] and re.search(r'не\s+в\s+штат|без\s+трудоустройств|трудоустройств\w*\s+не\s+(?:нуж|треб)',s))
    hire_bundle=bool(re.search(r'рекрутер|открыты\s+вакансии|с\s+вакансией|бонус\w*\s+за\s+переработ',s))
    concrete=bool(requests and any(re.search(r'собра|сверст|созда|исправ|передел|доработ|разов\w*\s+проект|проектн\w*\s+оплат',s[r[0]:r[1]]) for r in requests))
    if alternative:
        out['cooperation_format']='alternatives'
        return done('ambiguous','review','Предлагаются штат или проект; отдельный объём не раскрыт','format.alternatives',True)
    if signals['staff'] or hire_bundle and not concrete:
        out['cooperation_format']='employment'
        return done('vacancy','employment','Найм / подбор сотрудников без подтверждённого отдельного проекта','format.employment',True,'outside','recruiter')
    if not requests:
        if closed:return done('closed_signal','none','Задача закрыта; нового запроса после закрытия нет','task.closed',False,'outside')
        if outside:return done('other','none','Запрошен результат вне услуг MAXMADE','fit.outside',True,'outside','unknown')
        if signals['request'] and positive(profile['outside_result_pattern'],s) and not all_services:
            return done('other','none','Запрошен непрофильный результат; веб-задача не установлена','fit.outside',True,'outside')
        if all_services or re.search(r'дизайнер|разработчик|партнер|техническ\w*\s+специалист',s) or any(re.search(r'\b'+re.escape(t)+r'\b',s,re.I) for t in profile.get('confirmed_tools',[])):
            return done('ambiguous','review','Нет достаточных данных о результате или направлении передачи работ','task.insufficient')
        return done('other','none','Запроса на услуги MAXMADE не установлено','fit.no_request',None,'outside')
    start,stop,chosen,clause_start=requests[-1]
    out.update(services=list(dict.fromkeys(k for r in requests for k in r[2])),need=text[clause_start:stop].strip(),
       requested_result=text[clause_start:stop].strip(),work_direction='work_to_us',cooperation_format='ongoing_subcontract' if pool else 'project')
    for k in out['services']:
        for m in positive(patterns[k],s):evidence.append(quote(text,m.start(),m.end(),'service.'+k,'profile.service.'+k))
    if re.search(r'не\s+(?:пишите|откликайтесь)|отклики\s+не\s+принима',s[clause_start:]):
        return done('closed_signal','none','Автор запретил отклики на текущую задачу','contact.prohibited',True,'outside')
    role='agency' if re.search(r'\b(?:мы|наше)\s+агентство|\bстудия\b',s) else 'intermediary' if re.search(r'есть\s+заказчик|сам\s+не\s+успеваю|передадим|передаем',s) else 'unknown'
    kind='partnership' if pool else 'demand';route='partnership' if pool else 'main'
    fit='matched';reason='Ищут исполнителя для подходящей задачи MAXMADE'
    scope_start=start if re.search(r'так\s*же|также|отдельно|а\s+еще',s[clause_start:start]) else clause_start
    task_text=s[scope_start:stop] if not pool else s
    unsupported=positive(profile['unconfirmed_competence_pattern'],task_text)
    outside_mix=positive(profile['outside_result_pattern'],task_text)
    separable=pool or bool(re.search(r'можно\s+(?:взять\s+)?только|только\s+сайт|отдельно|не\s+обязател',s[clause_start:]))
    if unsupported or outside_mix:
        fit='partial' if separable else 'needs_verification'
        for m in unsupported+outside_mix:
            offset=0 if pool else scope_start
            q=quote(text,offset+m.start(),offset+m.end(),'constraint','profile.unconfirmed_or_outside')
            out['constraints'].append(q);evidence.append(q)
        if not separable:route='review';reason='Полный объём / обязательная технология требует проверки компетенций'
        else:reason='Подходит выделенная часть работ; другие услуги и технологии не подтверждены'
    out['unknown_details']=['Отсутствующие бюджет, срок, платформа и маршрут остаются неизвестными']
    if '[Фрагмент сокращён;' in text or len(text)>2000:
        route='review';reason='Сохранён неполный текст: сначала проверить полный объём';out['rule_ids'].append('input.truncated')
    return done(kind,route,reason,'task.incoming_subcontract' if pool else 'task.concrete_request',True,fit,role)
