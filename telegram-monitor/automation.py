"""Local Chrome operations. Metadata-only journal; no credentials in CLI or logs.

The Scheduler owns the supervisor; it owns only the dedicated browser host.
Retries are bounded per outage. A new baseline never claims gap recovery.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

import monitor
import local_chrome

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'state/local-chrome'
JOURNAL=STATE/'operations.sqlite3'
STOP=STATE/'operator-stop'
DELAYS=(5,15,30,60,120)
HEARTBEAT_MAX=45
HOST_HANG_MAX=150
TRANSIENT={'network_unavailable','page_reloaded','startup_transient'}


def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


def write_json(path,value):
    write_text(path,json.dumps(value,ensure_ascii=False,indent=2))


def write_text(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(value,encoding='utf-8')
    try:
        for attempt in range(5):
            try:tmp.replace(path);return
            except PermissionError:
                if attempt==4:raise
                time.sleep(.02)
    finally:tmp.unlink(missing_ok=True)


def age(value):
    try:return max(0,(monitor.now()-dt.datetime.fromisoformat(value)).total_seconds())
    except (TypeError,ValueError):return None


def process_running():
    try:
        with monitor.single_instance(STATE/'supervisor.lock'):return False
    except RuntimeError:return True


def config():
    monitor.load_env(ROOT/'.env');cfg=monitor.Config.from_env()
    if not cfg.db.is_absolute():cfg=__import__('dataclasses').replace(cfg,db=ROOT/cfg.db)
    return cfg


def journal(path=None):
    db=sqlite3.connect(path or JOURNAL,timeout=5);db.row_factory=sqlite3.Row
    db.execute('pragma journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY,at TEXT NOT NULL,kind TEXT NOT NULL,reason TEXT);
    CREATE TABLE IF NOT EXISTS outages(id INTEGER PRIMARY KEY,start_at TEXT NOT NULL,end_at TEXT,reason TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY,at TEXT NOT NULL,live_at TEXT,healthy INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,outage_id INTEGER NOT NULL,kind TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending',message_id INTEGER,error_type TEXT,UNIQUE(outage_id,kind));
    ''')
    # A crash during send is unknown, never automatically resent.
    with db:db.execute("update alerts set state='uncertain' where state='sending'")
    return db


def transition(db,kind,reason=None):
    with db:db.execute('insert into transitions(at,kind,reason) values(?,?,?)',(monitor.iso(monitor.now()),kind,reason))


def begin_outage(db,reason,start_at=None):
    old=db.execute('select id from outages where end_at is null order by id desc limit 1').fetchone()
    if old:return old[0]
    with db:
        cur=db.execute('insert into outages(start_at,reason) values(?,?)',(start_at or monitor.iso(monitor.now()),reason))
        ident=cur.lastrowid
        db.execute("insert into alerts(outage_id,kind) values(?,'failure')",(ident,))
    transition(db,'outage',reason);return ident


def end_outage(db):
    row=db.execute('select id from outages where end_at is null order by id desc limit 1').fetchone()
    if not row:return
    with db:
        db.execute('update outages set end_at=? where id=?',(monitor.iso(monitor.now()),row[0]))
        db.execute("update alerts set state='covered_by_recovery' where outage_id=? and kind='failure' and state='pending'",(row[0],))
        db.execute("insert or ignore into alerts(outage_id,kind) values(?,'recovery')",(row[0],))
    transition(db,'recovered','new_verified_baseline_no_backfill')


def flush_alerts(db,cfg,api=None):
    rows=db.execute("select a.*,o.start_at,o.end_at,o.reason from alerts a join outages o on o.id=a.outage_id where a.state='pending' order by a.id limit 2").fetchall()
    eligible=[]
    for row in rows:
        quiet=row['reason'] in ('operator_stopped','supervisor_start_or_previous_interruption')
        if row['kind']=='recovery':
            warned=db.execute("select 1 from alerts where outage_id=? and kind='failure' and state='sent'",(row['outage_id'],)).fetchone()
            quiet |= not bool(warned)
        elif row['end_at'] is not None:quiet=True
        elif not quiet and (age(row['start_at']) or 0)<300:continue
        if quiet:
            with db:db.execute("update alerts set state='quiet_log_only' where id=?",(row['id'],))
        else:eligible.append(row)
    rows=eligible
    if not rows:return
    api=api or monitor.Telegram(cfg.token)
    try:
        me=api.call('getMe',{},timeout=5)
        if me.get('username')!='uffweb_bot':return
    except Exception:return  # Network failure cannot invalidate the token.
    for row in rows:
        text=('Монитор: чтение прервано более 5 минут. Проверьте интернет и состояние монитора.'
              if row['kind']=='failure' else 'Монитор снова читает сообщения. Сообщения за время сбоя могли быть пропущены.')
        # Persist before a side effect. Ambiguous sends are never blindly retried.
        with db:db.execute("update alerts set state='sending' where id=?",(row['id'],))
        try:
            result=api.call('sendMessage',{'chat_id':cfg.owner_id,'text':text,'disable_web_page_preview':True},timeout=8)
        except Exception as error:
            with db:db.execute("update alerts set state='uncertain',error_type=? where id=?",(type(error).__name__,row['id']))
        else:
            with db:db.execute("update alerts set state='sent',message_id=? where id=?",(result.get('message_id'),row['id']))


def health(host,running,supervisor):
    check_age=age(host.get('last_live_check'))
    web_age=age(host.get('last_web_transport'))
    healthy=bool(running and host.get('capture_running') and host.get('phase')=='reading'
                 and host.get('account_verified') and host.get('source_verified')
                 and check_age is not None and check_age<=HEARTBEAT_MAX
                 and web_age is not None and web_age<=60)
    return {'healthy':healthy,'browser_available':bool(running and age(host.get('at')) is not None and age(host['at'])<=HOST_HANG_MAX),
            'last_verified_connection':host.get('last_live_check'),'check_age_seconds':round(check_age,1) if check_age is not None else None,
            'last_web_transport':host.get('last_web_transport'),'transport_scope':'public_web_origin_HEAD_plus_live_DOM_not_MTProto_ack',
            'account_verified':bool(healthy and host.get('account_verified')),'source_verified':bool(healthy and host.get('source_verified')),
            'manual_action_required':supervisor.get('phase') in ('manual_attention','retry_limit_reached') or host.get('phase')=='login_required'
                or any(r.get('phase') in ('manual_attention','retry_limit_reached') for r in host.get('source_states',[]))}


def status_record():
    host=read_json(STATE/'status.json');sup=read_json(STATE/'supervisor.json')
    result={'supervisor_running':process_running(),'host_running':local_chrome.host_running(),
            'mode':'interactive_windows_awake_only','account':'@maxmade_pro','sources':['@top_ads_freelance'],
            'operator_stopped':STOP.exists(),'supervisor':sup,'host_phase':host.get('phase'),
            'delivery_probe':host.get('delivery_check'),'uses_tinyfish':False,'backfill':False}
    result.update(health(host,result['host_running'],sup))
    if os.name=='nt':
        try:
            from chrome_startup import scheduled_task
            result['windows_task']=scheduled_task('status')
            result['windows_login_verified']=False
        except Exception as error:result['windows_task']={'state':'unconfirmed','error_type':type(error).__name__}
    if host.get('multi_source'):
        result.update(sources=['@'+r['username'] for r in host.get('source_states',[])],
            source_states=host.get('source_states',[]),all_sources_reading=host.get('all_sources_reading',False),
            registry_report='state/local-chrome/source-report.json')
    try:
        cfg=config()
        with contextlib.closing(sqlite3.connect(cfg.db.as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
            result['last_message_received']=db.execute("select max(received_at) from events where source_id=? and edit_version like 'browser:%'",('client:-1001513982592',)).fetchone()[0]
            result['pending_notifications']=db.execute("select count(*) from deliveries d join candidates c on c.id=d.candidate_id join events e on e.id=c.event_id where d.state='pending' and e.source_id=? and e.edit_version like 'browser:%'",('client:-1001513982592',)).fetchone()[0]
            result['legacy_pending_excluded']=db.execute("select count(*) from deliveries where state='pending'").fetchone()[0]-result['pending_notifications']
            if host.get('multi_source') and host.get('source_states'):
                peers=tuple('client:'+str(row['peer_id']) for row in host['source_states'])
                marks=','.join('?' for _ in peers)
                result['last_message_received']=db.execute("select max(received_at) from events where source_id in ("+marks+") and edit_version like 'browser:%'",peers).fetchone()[0]
                result['pending_notifications']=db.execute("select count(*) from deliveries d join candidates c on c.id=d.candidate_id join events e on e.id=c.event_id where d.state='pending' and e.source_id in ("+marks+") and e.edit_version like 'browser:%'",peers).fetchone()[0]
                result['legacy_pending_excluded']=db.execute("select count(*) from deliveries where state='pending'").fetchone()[0]-result['pending_notifications']
        if JOURNAL.exists():
            with contextlib.closing(sqlite3.connect(JOURNAL.as_uri()+'?mode=ro',uri=True)) as db:
                result['open_gap']=db.execute('select start_at,reason from outages where end_at is null order by id desc limit 1').fetchone()
                result['alert_states']=dict(db.execute('select state,count(*) from alerts group by state'))
    except Exception as error:result['diagnostic_error_type']=type(error).__name__
    result['delivery_available']=result['healthy'] and result['delivery_probe']=='ok'
    try:
        import operator_status
        result.update(operator_status.enrich(host,result,ROOT))
    except (OSError,ValueError,sqlite3.Error):result['operating_status']='состояние не подтверждено'
    return result


def launch_host():
    py=ROOT/'.venv/Scripts/pythonw.exe' if os.name=='nt' else Path(sys.executable)
    return subprocess.Popen([str(py),str(ROOT/'local_chrome.py'),'run'],cwd=ROOT,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)


def enqueue(action):
    target=STATE/'command.json'
    if target.exists():return False
    write_json(target,{'id':str(time.time_ns()),'action':action});return True


def terminate_owned_host(child,host):
    # Only a child launched by this supervisor; no name-based process killing.
    if child is not None and child.poll() is None:
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        else:child.terminate()


def supervise():
    local_chrome.private_directory(STATE)
    with monitor.single_instance(STATE/'supervisor.lock'):
        if STOP.exists():return
        cfg=config();db=journal();child=None;attempts=0;retry_at=0.;settle_until=0.;last_sample=0.;last_alert=0.;last_report=0.;was_healthy=False;previous_sample_at=None
        transition(db,'supervisor_start')
        # Starting after a previous healthy sample is a gap, not continuous uptime.
        last=db.execute('select at from samples order by id desc limit 1').fetchone()
        begin_outage(db,'supervisor_start_or_previous_interruption',last[0] if last else None)
        state={'pid':os.getpid(),'phase':'starting','attempts':0,'source':'top_ads_freelance'}
        try:
            while not STOP.exists():
                tick=time.monotonic();host=read_json(STATE/'status.json');running=local_chrome.host_running()
                resume=STATE/'resume-request'
                if resume.exists():
                    resume.unlink();attempts=0;retry_at=0;settle_until=tick+100
                    if running:enqueue('auto')
                    else:child=launch_host()
                    transition(db,'operator_resume','new_explicit_retry_budget')
                    state.update(phase='recovering',attempts=0,reason='operator_resume')
                good=health(host,running,state)['healthy']
                if was_healthy and not good:
                    begin_outage(db,host.get('failure_class') or 'connection_or_process_lost',state.get('last_healthy'))
                if tick-last_sample>=10:
                    if previous_sample_at and age(previous_sample_at)>25:
                        begin_outage(db,'sampling_gap_or_sleep',previous_sample_at)
                        if good:end_outage(db)
                    previous_sample_at=monitor.iso(monitor.now())
                    with db:
                        db.execute('insert into samples(at,live_at,healthy) values(?,?,?)',(monitor.iso(monitor.now()),host.get('last_live_check'),int(good)))
                        cutoff=monitor.iso(monitor.now()-dt.timedelta(days=30))
                        db.execute('delete from samples where at<?',(cutoff,))
                        db.execute('delete from transitions where at<?',(cutoff,))
                    last_sample=tick
                if good:
                    if not was_healthy:end_outage(db)
                    settle_until=0
                    if not was_healthy:retry_at=tick+DELAYS[min(max(attempts-1,0),len(DELAYS)-1)]
                    # Reset only after 5min stable, preventing infinite flap retries.
                    if not was_healthy:state['stable_since_monotonic']=tick
                    if tick-state.get('stable_since_monotonic',tick)>=300:attempts=0
                    state.update(phase='reading',attempts=attempts,last_healthy=host.get('last_live_check'))
                elif host.get('multi_source') and running and host.get('runtime_alive') and age(host.get('at')) is not None and age(host['at'])<=HEARTBEAT_MAX:
                    # Individual source retries/manual pauses belong to the reader.
                    # Never restart all healthy pages because one source failed.
                    state.update(phase='sources_waiting',reason='per_source_recovery_or_attention')
                elif tick>=settle_until:
                    phase=host.get('phase');failure=host.get('failure_class')
                    intentional=phase in ('operator_stopped','browser_closing','closing')
                    terminal=running and (phase=='login_required' or
                        phase in ('source_disarmed','startup_needs_local_attention') and failure not in TRANSIENT)
                    if intentional:
                        STOP.write_text(monitor.iso(monitor.now()),encoding='utf-8');break
                    if terminal:
                        state.update(phase='manual_attention',reason='login_required' if phase=='login_required' else 'identity_access_or_ambiguous_context')
                    elif attempts>=len(DELAYS):state.update(phase='retry_limit_reached',reason='five_attempts_exhausted')
                    elif tick>=retry_at:
                        attempts+=1
                        state.update(phase='recovering',attempts=attempts,reason='host_missing' if not running else failure or 'heartbeat_stale')
                        transition(db,'retry',state['reason'])
                        if not running:
                            terminate_owned_host(child,host)
                            # Remove only stale commands after confirmed host death.
                            (STATE/'command.json').unlink(missing_ok=True)
                            child=launch_host()
                        elif age(host.get('at')) is not None and age(host['at'])>HOST_HANG_MAX:
                            terminate_owned_host(child,host)
                        else:enqueue('auto_if_needed')
                        settle_until=tick+100;retry_at=settle_until+DELAYS[attempts-1]
                was_healthy=good
                state.update(at=monitor.iso(monitor.now()),automatic_recovery=True,backfill=False)
                write_json(STATE/'supervisor.json',state)
                if tick-last_alert>=60:
                    flush_alerts(db,cfg);last_alert=time.monotonic()
                if tick-last_report>=300:
                    with contextlib.suppress(Exception):report(STATE/'observation-report.json')
                    last_report=time.monotonic()
                time.sleep(2)
        finally:
            if STOP.exists():
                if local_chrome.host_running():enqueue('close')
                begin_outage(db,'operator_stopped',state.get('last_healthy'))
                transition(db,'operator_stopped')
                state.update(phase='operator_stopped',at=monitor.iso(monitor.now()))
                write_json(STATE/'supervisor.json',state)
            db.close()


def report(destination=None):
    cfg=config();result={'at':monitor.iso(monitor.now()),'source':'top_ads_freelance','required_observed_hours':24,'backfill':False}
    if JOURNAL.exists():
        with contextlib.closing(sqlite3.connect(JOURNAL.as_uri()+'?mode=ro',uri=True)) as db:
            rows=db.execute('select at,live_at,healthy from samples order by id').fetchall()
            # Credit only short adjacent samples with distinct recent live checks.
            seconds=0
            for a,b in zip(rows,rows[1:]):
                gap=(dt.datetime.fromisoformat(b[0])-dt.datetime.fromisoformat(a[0])).total_seconds()
                if a[2] and b[2] and a[1]!=b[1] and 0<gap<=25:seconds+=gap
            result.update(observed_seconds=seconds,observed_hours=round(seconds/3600,4),
                observation_complete=seconds>=86400,gaps=[dict(zip(('start','end','reason'),r)) for r in db.execute('select start_at,end_at,reason from outages')],
                alerts=dict(db.execute('select state,count(*) from alerts group by state')))
            start=rows[0][0] if rows else monitor.iso(monitor.now())
    else:start=monitor.iso(monitor.now())
    with contextlib.closing(sqlite3.connect(cfg.db.as_uri()+'?mode=ro',uri=True)) as db:
        result['classifications']=[dict(zip(('kind','priority','count'),r)) for r in db.execute('''select c.kind,c.priority,count(*) from events e join candidates c on c.event_id=e.id
          where e.received_at>=? and e.source_id=? and e.edit_version like 'browser:%' group by c.kind,c.priority''',(start,'client:-1001513982592'))]
        result['unique_messages']=sum(r['count'] for r in result['classifications'])
        delays=[(dt.datetime.fromisoformat(r)-dt.datetime.fromisoformat(p)).total_seconds() for p,r in db.execute("select published_at,received_at from events where received_at>=? and source_id=? and edit_version like 'browser:%'",(start,'client:-1001513982592'))]
        result['receive_delay_seconds']={'count':len(delays),'min':min(delays) if delays else None,'max':max(delays) if delays else None}
        result['notification_states']=dict(db.execute("select d.state,count(*) from deliveries d join candidates c on c.id=d.candidate_id join events e on e.id=c.event_id where e.received_at>=? and e.edit_version like 'browser:%' group by d.state",(start,)))
        notices=[(dt.datetime.fromisoformat(s)-dt.datetime.fromisoformat(r)).total_seconds() for r,s in db.execute("select e.received_at,d.sent_at from events e join candidates c on c.event_id=e.id join deliveries d on d.candidate_id=c.id where e.received_at>=? and d.state='sent' and d.sent_at is not null and e.edit_version like 'browser:%'",(start,))]
        result['notification_delay_seconds']={'count':len(notices),'min':min(notices) if notices else None,'max':max(notices) if notices else None}
        # Local conservative audit flag only; bodies never leave this process.
        sample=db.execute("select e.message_id,e.body from events e join candidates c on c.event_id=e.id where e.source_id=? and e.edit_version like 'browser:%' and c.priority='rejected' order by e.id desc limit 5",('client:-1001513982592',)).fetchall()
        suspect=[]
        for mid,body in sample:
            text=(body or '').lower()
            demand=bool(re.search(r'(нуж[её]н|нужна|ищ[уе]|требуется).{0,100}(сайт|лендинг|дизайн|презентац|исполнител)',text))
            if demand:suspect.append(mid)
        result['rejected_audit']={'sample_size':len(sample),'potential_missed_demand_ids':suspect,
            'method':'local_keyword_crosscheck_not_semantic_acceptance','scope':'last_five_browser_rejections_including_previous_run',
            'quality_accepted':False}
    if destination:write_json(destination,result)
    return result


def main():
    parser=argparse.ArgumentParser(description='Локальный монитор: запуск, состояние, остановка, отчёт')
    parser.add_argument('action',choices=['start','run','stop','status','report'])
    args=parser.parse_args()
    if args.action=='run':
        try:supervise()
        except RuntimeError:
            if not process_running():raise
        return
    if args.action=='status':print(json.dumps(status_record(),ensure_ascii=False,indent=2));return
    if args.action=='report':print(json.dumps(report(STATE/'observation-report.json'),ensure_ascii=False,indent=2));return
    if args.action=='stop':
        STATE.mkdir(parents=True,exist_ok=True);STOP.write_text(monitor.iso(monitor.now()),encoding='utf-8')
        if local_chrome.host_running():enqueue('close')
        print(json.dumps({'result':'intentional_stop_requested','automatic_restart':False}));return
    if args.action=='start':
        if process_running():
            if read_json(STATE/'supervisor.json').get('phase') in ('manual_attention','retry_limit_reached'):
                (STATE/'resume-request').write_text(monitor.iso(monitor.now()),encoding='utf-8')
                print(json.dumps({'result':'explicit_resume_requested'}))
            else:print(json.dumps({'result':'already_running'}))
            return
        STOP.unlink(missing_ok=True)
        # Scheduler gives independence from this terminal/chat.
        from chrome_startup import scheduled_task
        result=scheduled_task('start');print(json.dumps(result));return


if __name__=='__main__':main()
