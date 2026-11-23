"""Operator settings for reviewed Telegram sources. No transport, joins or sends."""
from __future__ import annotations
import argparse
import contextlib
import json
import re
from pathlib import Path
import source_registry as registry
import monitor

ADAPTER='telegram_web_k'


def normalize(name):
    name=name.lower().lstrip('@')
    if not re.fullmatch(r'[a-z0-9_]{5,32}',name):raise ValueError('Invalid public source username')
    return name


def parameters(peer,params):
    params=dict(params)
    if set(params)-{'dom_peer_id','navigation_timeout_ms'}:raise ValueError('Unsupported adapter parameter')
    if 'dom_peer_id' in params and str(params['dom_peer_id'])!=str(peer+10**12):raise ValueError('DOM peer differs from source identity')
    if 'navigation_timeout_ms' in params and (type(params['navigation_timeout_ms']) is not int or not 5000<=params['navigation_timeout_ms']<=60000):
        raise ValueError('Navigation timeout must be5000..60000ms')
    return params


def add(db,name,peer,kind='group',params=None):
    name=normalize(name)
    if type(peer) is not int or peer>=-10**12:raise ValueError('Pinned Telegram supergroup peer required')
    if kind not in ('group','channel'):raise ValueError('Unsupported Telegram source type')
    params=parameters(peer,params or {})
    with db:
        db.execute('''INSERT INTO sources(username,link,peer_id,type,enabled,adapter,adapter_parameters)
          VALUES(?,?,?,?,0,?,?)''',(name,'https://t.me/'+name,peer,kind,ADAPTER,json.dumps(params)))
    return name


def review(db,name,audit,note,route,rules_url=None):
    name=normalize(name)
    row=db.execute('SELECT * FROM sources WHERE username=?',(name,)).fetchone()
    proof=next((r for r in audit.get('groups',[]) if r.get('username','').lower()==name),{})
    if not row:raise ValueError('Add the source before recording its review')
    if not note.strip() or not route.strip():raise ValueError('Rules and reply-route review are required')
    if not (proof.get('account_verified') and proof.get('source_verified') and proof.get('membership')=='joined'
      and proof.get('message_metadata_accessible') and proof.get('stage')=='complete' and not proof.get('error_type')
      and proof.get('peer_id')==row['peer_id']):raise ValueError('Access/account/source proof is incomplete')
    from automation import age
    checked=age(audit.get('at'))
    if checked is None or not 0<=checked<3600:raise ValueError('Access audit is not fresh')
    if row['type']!='group':raise ValueError('This reader currently supports verified groups')
    with db:db.execute('''UPDATE sources SET access='joined',checked_at=?,rules_state='reviewed',rules_note=?,rules_url=?,
      read_permission='local_client',reply_route=? WHERE username=?''',(audit['at'],note,rules_url,route,name))


def enable(db,name,value):
    name=normalize(name)
    with db:
        if db.execute('UPDATE sources SET enabled=? WHERE username=?',(int(value),name)).rowcount!=1:raise ValueError('Unknown source')
        if value:registry.enabled(db)


def set_limit(db,limit):
    if type(limit) is not int or not 1<=limit<=20:raise ValueError('Active source limit must be1..20')
    if db.execute('SELECT count(*) FROM sources WHERE enabled=1').fetchone()[0]>limit:raise ValueError('Disable extra sources first')
    with db:db.execute("INSERT INTO runtime_settings VALUES('max_active_sources',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(limit),))


def select_only(db,names):
    """Explicit operator action, preserving all other source records and business data."""
    names=tuple(normalize(name) for name in names)
    if not names or len(set(names))!=len(names):raise ValueError('Unique non-empty selection required')
    if db.execute('SELECT count(*) FROM sources WHERE username IN ('+','.join('?' for _ in names)+')',names).fetchone()[0]!=len(names):
        raise ValueError('Unknown source in selection')
    with db:
        db.execute('UPDATE sources SET enabled=0 WHERE username NOT IN ('+','.join('?' for _ in names)+')',names)
        db.execute('UPDATE sources SET enabled=1 WHERE username IN ('+','.join('?' for _ in names)+')',names)
        registry.enabled(db)
        for row in db.execute('SELECT username FROM sources WHERE enabled=0').fetchall():
            registry.gap(db,row[0],'operator_disabled_source')
            db.execute("UPDATE runtime SET phase='disabled',last_check=NULL WHERE username=?",(row[0],))


def main():
    import automation,local_chrome
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['list','add','review','enable','disable','limit','select'])
    parser.add_argument('username',nargs='?');parser.add_argument('--peer',type=int);parser.add_argument('--type',default='group',choices=['group','channel'])
    parser.add_argument('--adapter-parameters',default='{}');parser.add_argument('--audit',type=Path)
    parser.add_argument('--rules-note');parser.add_argument('--reply-route');parser.add_argument('--rules-url');parser.add_argument('--max-active',type=int)
    args=parser.parse_args()
    if args.action!='list' and (local_chrome.host_running() or automation.process_running()):
        raise SystemExit('Stop the monitor first; settings apply on the next Start')
    with contextlib.closing(registry.connect()) as db:
        if args.action=='add':add(db,args.username,args.peer,args.type,json.loads(args.adapter_parameters))
        elif args.action=='review':review(db,args.username,json.loads(args.audit.read_text(encoding='utf-8')),args.rules_note or '',args.reply_route or '',args.rules_url)
        elif args.action in ('enable','disable'):enable(db,args.username,args.action=='enable')
        elif args.action=='limit':set_limit(db,args.max_active)
        elif args.action=='select':select_only(db,args.username.split(','))
        rows=[dict(r) for r in db.execute('SELECT username,link,type,peer_id,enabled,access,checked_at,rules_state,reply_route,adapter,adapter_parameters FROM sources ORDER BY enabled DESC,username')]
        print(json.dumps({'sources':rows,'settings':dict(db.execute('SELECT key,value FROM runtime_settings')),'applies_on':'next_Start'},ensure_ascii=False))


if __name__=='__main__':main()
