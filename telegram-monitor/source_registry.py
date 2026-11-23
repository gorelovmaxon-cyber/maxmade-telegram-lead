"""Local source registry and conservative, per-source observation accounting."""
from __future__ import annotations
import argparse
import contextlib
import datetime as dt
import json
import sqlite3
from pathlib import Path
import automation
import monitor

PATH=automation.STATE/'sources.sqlite3'


def connect(path=PATH):
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('pragma journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS sources(username TEXT PRIMARY KEY,peer_id INTEGER UNIQUE,type TEXT NOT NULL DEFAULT 'unknown',
      link TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,access TEXT NOT NULL DEFAULT 'unknown',checked_at TEXT,
      rules_state TEXT NOT NULL DEFAULT 'unknown',rules_url TEXT,rules_note TEXT,
      publish_permission TEXT NOT NULL DEFAULT 'unknown',reply_route TEXT NOT NULL DEFAULT 'unknown',
      read_permission TEXT NOT NULL DEFAULT 'unknown');
    CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY,username TEXT NOT NULL,at TEXT NOT NULL,live_at TEXT,
      healthy INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS readings_source ON readings(username,id);
    CREATE TABLE IF NOT EXISTS gaps(id INTEGER PRIMARY KEY,username TEXT NOT NULL,start_at TEXT NOT NULL,end_at TEXT,reason TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runtime(username TEXT PRIMARY KEY,phase TEXT NOT NULL,error_type TEXT,attempts INTEGER NOT NULL DEFAULT 0,
      baseline TEXT,last_check TEXT,last_received TEXT);
    CREATE TABLE IF NOT EXISTS stage_acceptance(stage INTEGER PRIMARY KEY,at TEXT NOT NULL,evidence TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS observation_runs(id INTEGER PRIMARY KEY,start_at TEXT NOT NULL,end_at TEXT,label TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS observation_sources(run_id INTEGER NOT NULL,username TEXT NOT NULL,start_at TEXT NOT NULL,
      PRIMARY KEY(run_id,username));
    CREATE TABLE IF NOT EXISTS browser_changes(id INTEGER PRIMARY KEY,username TEXT NOT NULL,message_id TEXT NOT NULL,
      version TEXT NOT NULL,change TEXT NOT NULL,observed_kind TEXT NOT NULL,observed_at TEXT NOT NULL,
      UNIQUE(username,message_id,version,observed_at));
    ''')
    columns={r[1] for r in db.execute('PRAGMA table_info(sources)')}
    for name,default in (('adapter',"'telegram_web_k'"),('adapter_parameters',"'{}'")):
        if name not in columns:db.execute('ALTER TABLE sources ADD COLUMN '+name+' TEXT NOT NULL DEFAULT '+default)
    db.execute('CREATE TABLE IF NOT EXISTS runtime_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS source_transitions(id INTEGER PRIMARY KEY,username TEXT NOT NULL,at TEXT NOT NULL,
      previous_phase TEXT,phase TEXT NOT NULL,error_type TEXT);
    CREATE TABLE IF NOT EXISTS source_incidents(id INTEGER PRIMARY KEY,username TEXT NOT NULL,opened_at TEXT NOT NULL,
      closed_at TEXT,terminal_phase TEXT NOT NULL,error_type TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS source_open_incident ON source_incidents(username) WHERE closed_at IS NULL;
    CREATE TABLE IF NOT EXISTS source_notices(id INTEGER PRIMARY KEY,incident_id INTEGER NOT NULL REFERENCES source_incidents(id),
      kind TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,next_at TEXT NOT NULL,
      sent_at TEXT,message_id INTEGER,error_code TEXT,UNIQUE(incident_id,kind));
    ''')
    db.commit()
    return db


def record_runtime(db,name,phase,error,attempts,baseline,last_check):
    """State transitions, not each heartbeat, create technical owner notices."""
    at=monitor.iso(monitor.now())
    with db:
        old=db.execute('SELECT phase FROM runtime WHERE username=?',(name,)).fetchone()
        db.execute('''INSERT INTO runtime(username,phase,error_type,attempts,baseline,last_check) VALUES(?,?,?,?,?,?)
          ON CONFLICT(username) DO UPDATE SET phase=excluded.phase,error_type=excluded.error_type,attempts=excluded.attempts,
          baseline=excluded.baseline,last_check=excluded.last_check''',(name,phase,error,attempts,str(baseline) if baseline is not None else None,last_check))
        if old and old[0]==phase:return
        db.execute('INSERT INTO source_transitions(username,at,previous_phase,phase,error_type) VALUES(?,?,?,?,?)',(name,at,old[0] if old else None,phase,error))
        incident=db.execute('SELECT id FROM source_incidents WHERE username=? AND closed_at IS NULL',(name,)).fetchone()
        if phase in ('manual_attention','retry_limit_reached') and not incident:
            incident_id=db.execute('INSERT INTO source_incidents(username,opened_at,terminal_phase,error_type) VALUES(?,?,?,?)',(name,at,phase,error)).lastrowid
            db.execute("INSERT INTO source_notices(incident_id,kind,next_at) VALUES(?,'failed',?)",(incident_id,at))
        elif phase=='reading' and incident:
            db.execute('UPDATE source_incidents SET closed_at=? WHERE id=?',(at,incident[0]))
            db.execute("INSERT OR IGNORE INTO source_notices(incident_id,kind,next_at) VALUES(?,'recovered',?)",(incident[0],at))


def record_changes(db,name,changes):
    with db:
        for row in changes:
            db.execute('insert or ignore into browser_changes(username,message_id,version,change,observed_kind,observed_at) values(?,?,?,?,?,?)',
                (name,row['message_id'],row['version'],row['change'],row['observed_kind'],row['observed_at']))


def enroll_active(db,name,at=None):
    run=db.execute('select id from observation_runs where end_at is null order by id desc limit 1').fetchone()
    if run:
        with db:db.execute('insert or ignore into observation_sources values(?,?,?)',
                          (run[0],name,at or monitor.iso(monitor.now())))


def start_observation(db,label):
    """Idempotent active run; each newly enabled source gets its own starting time."""
    run=db.execute('select id from observation_runs where end_at is null order by id desc limit 1').fetchone()
    if not run:
        with db:run=(db.execute('insert into observation_runs(start_at,label) values(?,?)',
                               (monitor.iso(monitor.now()),label)).lastrowid,)
    for source in enabled(db):enroll_active(db,source['username'])
    return run[0]


def observation_report(db):
    run=db.execute('select * from observation_runs where end_at is null order by id desc limit 1').fetchone()
    if not run:return None
    value=dict(run);value['sources']=[]
    for row in db.execute('select * from observation_sources where run_id=? order by start_at,username',(run['id'],)).fetchall():
        item=dict(row)
        readings=[dict(r) for r in db.execute('select * from readings where username=? and at>=? order by id',
                                             (row['username'],row['start_at']))]
        item['observed_seconds']=observed_seconds(readings)
        item['complete_24h']=item['observed_seconds']>=86400
        item['last_sample_at']=readings[-1]['at'] if readings else None
        value['sources'].append(item)
    return value


def initialize(db,audit=None):
    # Candidates are not active sources. No automatic joins or permissions inferred.
    names=('top_ads_freelance','freelead','bitrixinside24','biznes_chat','businessclass_rbc','onpeak_chat','odp2021',
           'expopeople','free_chat_for_freelance','frilanser_100','tilda_official_chat','marketologiyuga',
           'chat_frilans','smmruschats','imweek_ru','bit24dev','bitrixfordevelopers','spbeventchat','business_assistants','chat_kovalev')
    with db:
        for name in names:db.execute('insert or ignore into sources(username,link) values(?,?)',(name,'https://t.me/'+name))
        for row in (audit or {}).get('groups',[]):
            if row.get('stage')!='complete' or not row.get('source_verified'):continue
            db.execute('update sources set peer_id=?,type=?,access=?,checked_at=? where username=?',
                       (row['peer_id'],'group',row['membership'],audit['at'],row['username'].lower()))
        db.execute('''update sources set peer_id=-1001513982592,type='group',enabled=1,
          read_permission='local_client',rules_state='reviewed',reply_route='verify_each_request',
          rules_note='Existing accepted source; no unsolicited advertising or automated replies.' where username='top_ads_freelance' ''')


def enabled(db):
    rows=[dict(r) for r in db.execute("select * from sources where enabled=1 order by (username='top_ads_freelance') desc,username")]
    allowed=max([1]+[r[0] for r in db.execute('select stage from stage_acceptance')])
    # Stage 2 is a trial, later expansions require a measured previous-stage receipt.
    cap=2 if allowed==1 else {2:5,5:10,10:20,20:20}[allowed]
    limit=db.execute("SELECT value FROM runtime_settings WHERE key='max_active_sources'").fetchone()
    if limit:
        cap=int(limit[0])
        if not 1<=cap<=20:raise ValueError('Invalid configured active-source limit')
    if len(rows)>cap:raise ValueError('Previous source stage has not been accepted')
    for r in rows:
        if r['access']!='joined' or r['type']!='group' or r['rules_state']!='reviewed' or r['read_permission']!='local_client' or not r['peer_id'] or r['peer_id']>=-10**12:
            raise ValueError('Enabled source has unverified access, rules or type')
    return rows


def gap(db,name,reason,at=None):
    if not db.execute('select 1 from gaps where username=? and end_at is null',(name,)).fetchone():
        with db:db.execute('insert into gaps(username,start_at,reason) values(?,?,?)',(name,at or monitor.iso(monitor.now()),reason))


def sample(db,name,healthy,live_at=None,at=None):
    stamp=at or monitor.iso(monitor.now())
    prev=db.execute('select at from readings where username=? order by id desc limit 1',(name,)).fetchone()
    if prev and (dt.datetime.fromisoformat(stamp)-dt.datetime.fromisoformat(prev[0])).total_seconds()>25:
        gap(db,name,'sampling_gap_or_sleep',prev[0])
    if healthy:
        with db:db.execute('update gaps set end_at=? where username=? and end_at is null',(stamp,name))
    else:gap(db,name,'not_reading',stamp)
    with db:
        db.execute('insert into readings(username,at,live_at,healthy) values(?,?,?,?)',(name,stamp,live_at,int(healthy)))
        db.execute('delete from readings where at<?',(monitor.iso(monitor.now()-dt.timedelta(days=30)),))


def observed_seconds(rows):
    total=0
    for a,b in zip(rows,rows[1:]):
        delta=(dt.datetime.fromisoformat(b['at'])-dt.datetime.fromisoformat(a['at'])).total_seconds()
        # A stale repeated live_at, sleep or unhealthy sample never credits time.
        ages=[(dt.datetime.fromisoformat(r['at'])-dt.datetime.fromisoformat(r['live_at'])).total_seconds() if r['live_at'] else 9999 for r in (a,b)]
        if a['healthy'] and b['healthy'] and a['live_at']!=b['live_at'] and 0<delta<=25 and all(0<=age<=15 for age in ages):total+=delta
    return total


def report(db,business):
    result=[]
    for source in db.execute('select * from sources order by enabled desc,username').fetchall():
        item=dict(source);key='client:'+str(source['peer_id'])
        samples=[dict(r) for r in db.execute('select * from readings where username=? order by id',(source['username'],))]
        item['observed_seconds']=observed_seconds(samples)
        item['observation_start']=samples[0]['at'] if samples else None
        item['calendar_seconds']=(dt.datetime.fromisoformat(samples[-1]['at'])-dt.datetime.fromisoformat(samples[0]['at'])).total_seconds() if len(samples)>1 else 0
        item['observation_24h_complete']=item['observed_seconds']>=86400
        item['runtime']=dict(db.execute('select * from runtime where username=?',(source['username'],)).fetchone() or {})
        item['gaps']=[dict(r) for r in db.execute('select start_at,end_at,reason from gaps where username=? order by id',(source['username'],))]
        row=business.execute('''select count(*),max(e.received_at),sum(c.kind='demand'),sum(c.kind='demand' and c.priority!='rejected'),
          sum(c.owner_status='not_fit'),sum(c.owner_status='taken'),sum(c.owner_status='replied'),sum(c.owner_status='dialog')
          from events e join candidates c on c.event_id=e.id where e.source_id=? and e.edit_version like 'browser:%' ''',(key,)).fetchone()
        item.update(dict(zip(('events','last_received','direct_requests','profile_candidates','owner_rejected','taken','replied_status','dialog_status'),row)))
        for field in ('direct_requests','profile_candidates','owner_rejected','taken','replied_status','dialog_status'):
            item[field]=item[field] or 0
        item['outreach_confirmed']='unknown' if not item['replied_status'] and not item['dialog_status'] else item['replied_status']+item['dialog_status']
        item['dialog_confirmed']=item['dialog_status'] or 'unknown'
        sent=business.execute('''select count(*),max(d.sent_at) from deliveries d join candidates c on c.id=d.candidate_id
          join events e on e.id=c.event_id where e.source_id=? and e.edit_version like 'browser:%' and d.state='sent' ''',(key,)).fetchone()
        item.update(delivered=sent[0],last_delivery=sent[1])
        delays=[(dt.datetime.fromisoformat(r)-dt.datetime.fromisoformat(p)).total_seconds() for p,r in business.execute(
            "select published_at,received_at from events where source_id=? and edit_version like 'browser:%'",(key,))]
        item['receive_delay_s']={'count':len(delays),'max':max(delays) if delays else None,'min':min(delays) if delays else None}
        notices=[(dt.datetime.fromisoformat(sent)-dt.datetime.fromisoformat(received)).total_seconds() for received,sent in business.execute('''
          select e.received_at,d.sent_at from deliveries d join candidates c on c.id=d.candidate_id join events e on e.id=c.event_id
          where e.source_id=? and e.edit_version like 'browser:%' and d.state='sent' and d.sent_at is not null''',(key,))]
        item['notification_delay_s']={'count':len(notices),'max':max(notices) if notices else None,'min':min(notices) if notices else None}
        item['events_during_observation']=business.execute("select count(*) from events where source_id=? and edit_version like 'browser:%' and received_at>=?",(key,item['observation_start'] or monitor.iso(monitor.now()))).fetchone()[0]
        changes=dict(db.execute('select change,count(*) from browser_changes where username=? and observed_at>=? group by change',
                               (source['username'],item['observation_start'] or monitor.iso(monitor.now()))))
        item['new_messages_during_observation']=changes.get('first_ingest',0)
        item['observed_edits_during_observation']=changes.get('edited',0)
        item['mutation_journal_scope']='instrumented captures only; historical counts are not reconstructed'
        result.append(item)
    return {'at':monitor.iso(monitor.now()),'sources':result,'active_observation':observation_report(db),'backfill':False,'completeness_proven':False,
            'count_scope':'all_persisted_browser_events_per_source; events_during_observation_is_separate',
            'reading_time_scope':'only_short_adjacent_fresh_registry_samples; not_process_lifetime'}


def write_report(value):
    automation.write_json(automation.STATE/'source-report.json',value)
    lines=['# Отчёт по источникам', '', 'Срез UTC: '+value['at'], '',
      'События — все сохранённые browser-события конкретного источника; «за наблюдение» — отдельно с начала его текущего реестра. Время чтения зачтено только по соседним свежим проверкам, календарное время включает разрывы. Полнота потока не доказана.', '',
      '| Источник | Доступ / правила | Состояние | Чтение, мин / календарь, мин | Последнее получение UTC | События / за наблюдение | Прямые / профиль | Доставлено |',
      '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in value['sources']:
        run=r['runtime'];phase=run.get('phase','не подключён')
        lines.append(f"| @{r['username']} | {r['access']} / {r['rules_state']} | {phase} | {r['observed_seconds']/60:.1f} / {r['calendar_seconds']/60:.1f} | {r['last_received'] or 'неизвестно'} | {r['events']} / {r['events_during_observation']} | {r['direct_requests']} / {r['profile_candidates']} | {r['delivered']} |")
    lines+=['', 'Ручные исходы сохранённых browser-карточек; взятие в работу не доказывает отправку обращения.', '',
      '| Источник | Отклонено | Взято | Обращение подтверждено | Диалог подтверждён | Ошибка / разрывы |',
      '| --- | --- | --- | --- | --- | --- |']
    for r in value['sources']:
        error=r['runtime'].get('error_type') or 'нет текущей записи';open_gaps=sum(g['end_at'] is None for g in r['gaps'])
        lines.append(f"| @{r['username']} | {r['owner_rejected']} | {r['taken']} | {r['outreach_confirmed']} | {r['dialog_confirmed']} | {error}; всего {len(r['gaps'])}, открыто {open_gaps} |")
    lines+=['','Право чтения, публикации и маршрут ответа хранятся раздельно в JSON. Неизвестное не разрешает автоматический отклик. 24ч не считаются пройденными календарной выдержкой.']
    lines+=['','| Источник | Получение, с min / max | Доставка после приёма, с min / max |', '| --- | --- | --- |']
    for r in value['sources']:
        receive=r['receive_delay_s'];notice=r['notification_delay_s']
        lines.append(f"| @{r['username']} | {receive['min']} / {receive['max']} | {notice['min']} / {notice['max']} |")
    automation.write_text(automation.STATE/'source-report.md','\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['init','report']);args=parser.parse_args()
    with contextlib.closing(connect()) as db:
        if args.action=='init':
            initialize(db,automation.read_json(automation.STATE/'selected-group-verification.json'))
            print(json.dumps({'registered':db.execute('select count(*) from sources').fetchone()[0],'enabled':len(enabled(db))}))
        else:
            cfg=automation.config()
            with contextlib.closing(sqlite3.connect(cfg.db.as_uri()+'?mode=ro',uri=True)) as business:
                value=report(db,business)
            write_report(value)
            print(json.dumps(value,ensure_ascii=False,indent=2))
