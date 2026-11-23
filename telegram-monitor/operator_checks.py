"""Owner-only diagnostic card; uses the existing sole callback poller, no lead edits."""
import contextlib
import hmac
import json
import re
import sqlite3
import datetime as dt
import automation
import monitor


def connect():
    db=sqlite3.connect(automation.STATE/'operator-checks.sqlite3',timeout=10)
    db.execute('''create table if not exists cards(id integer primary key,label text unique,state text,
      message_id integer,created_at text,accepted_at text)''')
    return db


def prepare(cfg,api,label='T-20261106-01'):
    if not re.fullmatch(r'T-[A-Za-z0-9-]{1,40}',label):raise ValueError('Invalid technical-card label')
    with contextlib.closing(connect()) as db:
        with db:
            db.execute('insert or ignore into cards(label,state,created_at) values(?,?,?)',(label,'new',monitor.iso(monitor.now())))
            row=db.execute('select id,state,message_id,accepted_at from cards where label=?',(label,)).fetchone()
            if row[1]!='new':return {'test_card':label,'state':row[1],'message_id':row[2],'accepted_at':row[3]}
            db.execute("update cards set state='sending' where id=?",(row[0],))
        payload={'chat_id':cfg.owner_id,'text':f'ТЕХНИЧЕСКАЯ ПРОВЕРКА #{label}\nЭто не заказ. Нажмите «Проверить кнопку». Ожидается подтверждение теста; статусы заказов не изменятся.',
                 'reply_markup':{'inline_keyboard':[[{'text':'Проверить кнопку','callback_data':f't:{row[0]}:'+monitor.signature(cfg.token,row[0],'diagnostic')}]]}}
        try:
            sent=api.call('sendMessage',payload,timeout=10)
            with db:db.execute("update cards set state='sent',message_id=? where id=?",(sent['message_id'],row[0]))
        except Exception:
            with db:db.execute("update cards set state='uncertain' where id=?",(row[0],))
            raise
        return {'test_card':label,'state':'sent','message_id':sent['message_id']}


def handle_callback(cfg,update,api):
    cb=update.get('callback_query') or {};msg=cb.get('message') or {}
    match=re.fullmatch(r't:(\d+):([0-9a-f]{16})',cb.get('data',''))
    if not match or (cb.get('from') or {}).get('id')!=cfg.owner_id or (msg.get('chat') or {}).get('id')!=cfg.owner_id:return False
    ident=int(match[1])
    if not hmac.compare_digest(match[2],monitor.signature(cfg.token,ident,'diagnostic')):return False
    with contextlib.closing(connect()) as db:
        row=db.execute('select state,message_id from cards where id=?',(ident,)).fetchone()
        if not row or row[0] not in ('sent','accepted') or row[1]!=msg.get('message_id'):return False
        with db:db.execute("update cards set state='accepted',accepted_at=coalesce(accepted_at,?) where id=?",(monitor.iso(monitor.now()),ident))
    if cb.get('id'):
        with contextlib.suppress(monitor.ApiError,monitor.UnknownDelivery):
            api.call('answerCallbackQuery',{'callback_query_id':cb['id'],'text':'Тест подтверждён; статусы заказов не изменены'},timeout=5)
    return True


def record_resources(resources):
    """Metadata only. App absence plus fresh samples can support a user-run closed-app test."""
    host=automation.read_json(automation.STATE/'status.json')
    value={**resources,'enabled_sources':host.get('enabled_sources'),
           'sources':[{'username':r['username'],'phase':r['phase'],'last_live_check':r.get('last_live_check'),
                       'last_web_transport':r.get('last_web_transport')} for r in host.get('source_states',[])]}
    path=automation.STATE/'resource-history.jsonl'
    if path.exists() and path.stat().st_size>2*1024*1024:path.replace(path.with_name('resource-history.previous.jsonl'))
    with path.open('a',encoding='utf-8') as out:out.write(json.dumps(value)+'\n')
    if (automation.STATE/'closed-codex-check.json').exists():
        with (automation.STATE/'closed-codex-samples.jsonl').open('a',encoding='utf-8') as out:out.write(json.dumps(value)+'\n')


def arm_closed_check():
    file=automation.STATE/'closed-codex-check.json'
    host=automation.read_json(automation.STATE/'status.json')
    names=[r['username'] for r in host.get('source_states',[])]
    if not file.exists():
        automation.write_json(file,{'armed_at':monitor.iso(monitor.now()),'expected_sources':names,
                                   'accepted':False,'requires':'user closes/reopens app; inspect persisted samples and per-source events afterwards'})
    value=automation.read_json(file)
    if value.get('expected_sources')!=names and not value.get('accepted'):
        value.setdefault('roster_history',[]).append({'at':monitor.iso(monitor.now()),'sources':names})
        value['expected_sources']=names;automation.write_json(file,value)
    return value


def status():
    with contextlib.closing(connect()) as db:
        cards=[dict(zip(('card','state','message_id','accepted_at'),r)) for r in db.execute('select label,state,message_id,accepted_at from cards')]
    check=automation.read_json(automation.STATE/'closed-codex-check.json')
    file=automation.STATE/'closed-codex-samples.jsonl';samples=[]
    if file.exists():
        for line in file.read_text(encoding='utf-8').splitlines():
            try:samples.append(json.loads(line))
            except ValueError:continue
    # Evidence only; absence of UI processes is not proof of incoming messages.
    absent=[s for s in samples if s.get('app_process_count')==0]
    return {'cards':cards,'closed_codex_check':check,'persisted_samples':len(samples),
            'samples_without_app_processes':len(absent),'first_absent_at':absent[0]['at'] if absent else None,
            'last_absent_at':absent[-1]['at'] if absent else None,'acceptance_requires':'inspect source checks/events within the user-confirmed closed interval'}


def analyze_closed_interval(start,end,expected,samples,changes):
    start=dt.datetime.fromisoformat(start);end=dt.datetime.fromisoformat(end)
    if start.tzinfo is None or end.tzinfo is None or end<=start:raise ValueError('Explicit ordered timezone-aware interval required')
    rows=[s for s in samples if start<=dt.datetime.fromisoformat(s['at'])<=end]
    bad=[];good=0
    for sample in rows:
        stamp=dt.datetime.fromisoformat(sample['at']);states={r['username']:r for r in sample.get('sources',[])}
        failed=[]
        for name in expected:
            state=states.get(name,{})
            live=(stamp-dt.datetime.fromisoformat(state['last_live_check'])).total_seconds() if state.get('last_live_check') else 9999
            web=(stamp-dt.datetime.fromisoformat(state['last_web_transport'])).total_seconds() if state.get('last_web_transport') else 9999
            if state.get('phase')!='reading' or not 0<=live<=15 or not 0<=web<=60:failed.append(name)
        if failed:bad.append({'at':sample['at'],'not_fresh_sources':failed})
        else:good+=1
    stamps=[start]+[dt.datetime.fromisoformat(r['at']) for r in rows]+[end]
    gaps=[(b-a).total_seconds() for a,b in zip(stamps,stamps[1:])]
    maximum_gap=max(gaps,default=(end-start).total_seconds())
    events={}
    for row in changes:
        if row['username'] in expected and start<=dt.datetime.fromisoformat(row['observed_at'])<=end:
            events[row['username']]=events.get(row['username'],0)+1
    complete=bool(expected) and (end-start).total_seconds()>=180 and good>=2 and not bad and maximum_gap<=90 and bool(events)
    return {'start_at':monitor.iso(start),'end_at':monitor.iso(end),'user_confirmed_app_closed':True,
            'process_count_is_not_ui_closure_proof':True,'expected_sources':expected,'samples':len(rows),
            'fresh_source_samples':good,'fresh_five_source_samples':good,'failed_samples':bad,'maximum_sampling_gap_s':maximum_gap,
            'new_originals_in_interval':events,'background_reading_observed':bool(good and events),
            'continuous_source_checks_accepted':complete,'full_stream_completeness_proven':False}


def record_closed_interval(start,end):
    import source_registry as registry
    first,last=dt.datetime.fromisoformat(start),dt.datetime.fromisoformat(end)
    if first.tzinfo is None or last.tzinfo is None or last<=first:raise ValueError('Explicit ordered timezone-aware interval required')
    # Registry timestamps are canonical UTC; SQLite text bounds must use it too.
    start,end=monitor.iso(first),monitor.iso(last)
    check=automation.read_json(automation.STATE/'closed-codex-check.json')
    samples=[];file=automation.STATE/'closed-codex-samples.jsonl'
    if file.exists():
        for line in file.read_text(encoding='utf-8').splitlines():
            with contextlib.suppress(ValueError):samples.append(json.loads(line))
    with contextlib.closing(registry.connect()) as journal:
        changes=[dict(r) for r in journal.execute("select username,observed_at from browser_changes where change='first_ingest' and observed_at>=? and observed_at<=?",(start,end))]
    result=analyze_closed_interval(start,end,check.get('expected_sources',[]),samples,changes)
    result['recorded_at']=monitor.iso(monitor.now())
    key=result['start_at']+'_'+result['end_at']
    intervals=check.setdefault('user_intervals',{})
    intervals[key]=result;check['accepted']=result['continuous_source_checks_accepted']
    automation.write_json(automation.STATE/'closed-codex-check.json',check)
    automation.write_json(automation.STATE/'closed-codex-result.json',result)
    return result
