"""Owner-facing metadata status. Quiet sources are judged by guards, not message frequency."""
import contextlib
import datetime as dt
import json
import sqlite3
import monitor


def summarize(host,names,running,stopped,at):
    states={s['username']:s for s in host.get('source_states',[])}
    rows=[]
    for name in names:
        state=states.get(name,{})
        def fresh(field,limit):
            try:age=(at-dt.datetime.fromisoformat(state[field])).total_seconds()
            except (ValueError,KeyError,TypeError):return False
            return 0<=age<=limit
        live=bool(running and not stopped and state.get('phase')=='reading' and state.get('capture_running')
                  and fresh('last_live_check',15) and fresh('last_web_transport',60))
        rows.append({'username':name,'reading_fresh':live,'phase':state.get('phase','not_started'),
          'last_successful_read_check':state.get('last_live_check'),'last_transport_check':state.get('last_web_transport'),
          'error_type':state.get('error_type'),'attempts':state.get('attempts',0),
          'state':'читает' if live else 'остановлен' if stopped else 'проверка устарела' if state.get('phase')=='reading' else state.get('phase','не запущен')})
    count=sum(row['reading_fresh'] for row in rows)
    attention=any(row['phase'] in ('manual_attention','retry_limit_reached') for row in rows)
    status=('остановлен' if stopped else 'нужна помощь' if attention else 'работает' if rows and count==len(rows)
            else 'частично работает' if count else 'остановлен')
    return {'operating_status':status,'reading_sources':count,'enabled_source_count':len(names),'source_status':rows,
      'automatic_recovery_pending':bool(running and not stopped and not attention and count<len(names)),
      'scope':'interactive Windows, Chrome open, PC awake; no guaranteed backfill'}


def enrich(host,base,root,at=None):
    import source_registry
    at=at or monitor.now();state=root/'state/local-chrome'
    def open_ro(path):
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row;return db
    with contextlib.closing(open_ro(state/'sources.sqlite3')) as registry:
        definitions=[dict(r) for r in registry.execute('SELECT username,peer_id FROM sources WHERE enabled=1 ORDER BY username')]
        result=summarize(host,[r['username'] for r in definitions],base['host_running'],base['operator_stopped'],at)
        result['disabled_source_count']=registry.execute('SELECT count(*) FROM sources WHERE enabled=0').fetchone()[0]
        observation=source_registry.observation_report(registry)
        result['observation_run']=observation
        by_name={row['username']:row for row in result['source_status']}
        for definition in definitions:
            name=definition['username'];row=by_name[name]
            row['open_gaps']=[dict(r) for r in registry.execute('SELECT start_at,reason FROM gaps WHERE username=? AND end_at IS NULL',(name,))]
            row['observed_seconds_in_run']=next((s['observed_seconds'] for s in observation['sources'] if s['username']==name),0) if observation else 0
    import automation
    cfg=automation.config()
    with contextlib.closing(open_ro(cfg.db)) as db:
        result['active_filter_version']=monitor.meta(db,'filter_active_version','v2')
        result['active_rules_version']=monitor.meta(db,'filter_active_rules_version','legacy-selection-v3.1')
        result['active_profile_version']=monitor.meta(db,'active_business_profile_version',result['active_filter_version'])
        for definition in definitions:
            row=by_name[definition['username']];key='client:'+str(definition['peer_id'])
            row['last_new_event']=db.execute('SELECT max(received_at) FROM events WHERE source_id=?',(key,)).fetchone()[0]
            row['last_successful_delivery']=db.execute('''SELECT max(d.sent_at) FROM deliveries d JOIN candidates c ON c.id=d.candidate_id
              JOIN events e ON e.id=c.event_id WHERE e.source_id=? AND d.state='sent' ''',(key,)).fetchone()[0]
            row['delivery_states']=dict(db.execute('''SELECT d.state,count(*) FROM deliveries d JOIN candidates c ON c.id=d.candidate_id
              JOIN events e ON e.id=c.event_id WHERE e.source_id=? GROUP BY d.state''',(key,)))
    return result


if __name__=='__main__':
    import automation
    print(json.dumps(automation.status_record(),ensure_ascii=False,indent=2))
