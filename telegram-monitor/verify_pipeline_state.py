"""Safe snapshot/getMe check. Never prints credentials or destination IDs."""
import contextlib
import hashlib
import json
import sqlite3
from pathlib import Path

import automation
import chrome_pilot
import monitor

if __name__=='__main__':
    cfg=automation.config();out=automation.STATE
    result={'at':monitor.iso(monitor.now()),'account_binding_matches':__import__('os').getenv('MONITOR_CLIENT_ACCOUNT','').lstrip('@').lower()==chrome_pilot.ACCOUNT}
    with contextlib.closing(sqlite3.connect(cfg.db.as_uri()+'?mode=ro',uri=True)) as db:
        result['integrity']=db.execute('pragma integrity_check').fetchone()[0]
        result['counts']={t:db.execute('select count(*) from '+t).fetchone()[0] for t in ('events','candidates','deliveries')}
        result['owner_statuses']=dict(db.execute('select owner_status,count(*) from candidates group by owner_status'))
        result['delivery_states']=dict(db.execute('select state,count(*) from deliveries group by state'))
        backup=out/('before-verification-'+monitor.now().strftime('%Y%m%dT%H%M%SZ')+'.sqlite3')
        with contextlib.closing(sqlite3.connect(backup)) as copy:db.backup(copy)
        result['backup_name']=backup.name
        before=automation.ROOT/'state/automation-20261105/before.sqlite3'
        if before.exists():
            with contextlib.closing(sqlite3.connect(before.as_uri()+'?mode=ro',uri=True)) as original:
                statuses=dict(original.execute('select id,owner_status from candidates'))
                current=dict(db.execute('select id,owner_status from candidates'))
                result['old_manual_status_mismatches']=sum(current.get(k)!=v for k,v in statuses.items())
                old=list(original.execute('select id,state,message_id from deliveries order by id'))
                current_deliveries={r[0]:r[1:] for r in db.execute('select id,state,message_id from deliveries')}
                result['old_delivery_mismatches']=sum(current_deliveries.get(r[0])!=r[1:] for r in old)
    before_env=automation.read_json(automation.ROOT/'state/automation-20261105/before.json').get('env_sha256')
    result['env_unchanged']=before_env==hashlib.sha256((automation.ROOT/'.env').read_bytes()).hexdigest() if before_env else None
    try:
        me=monitor.Telegram(cfg.token).call('getMe',{},timeout=10)
        result['bot_check']='accepted_uffweb_bot' if me.get('username')=='uffweb_bot' else 'wrong_bot'
    except monitor.ApiError as error:result['bot_check']='api_'+str(error.code)
    except Exception as error:result.update(bot_check='network_unconfirmed',bot_error_type=type(error).__name__)
    automation.write_json(out/'pipeline-verification.json',result)
    print(json.dumps(result))
