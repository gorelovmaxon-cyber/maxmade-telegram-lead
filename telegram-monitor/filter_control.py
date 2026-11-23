"""Local version switch and metadata queues; never opens or exports browser sessions."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path
import filter_policy

ROOT=Path(__file__).resolve().parent
INPUTS=('monitor.py','order_profile.py','order_profile_v2.py','order_selection.py','filter_policy.py',
        'filter_control.py','quality_replay.py','quality_evaluation.py','multi_source.py','local_chrome_runtime.py','maxmade_selection.py','selection_facts.py','intent_frames.py','maxmade_selection_v32.py','selection_facts_v32.py',
        'browser_capture.py','browser_ingest.py','browser_observer.js','browser_context.py','browser_message_context.js','context_surface_probe.py','tests/test_browser_context.py',
        'message_classification.py','selection_engine.py','source_config.py','source_registry.py',
        'operator_status.py','source_notices.py','operator_checks.py','technical_browser_test.py','automation.py',
        'config/order-profile.json','config/order-profile-v2.json',
        'tests/test_filter_policy.py','tests/test_filter_control.py','tests/test_selection_v3.py','tests/test_monitor.py','tests/test_order_profile.py',
        'tests/test_profile_architecture.py','tests/test_source_config.py','tests/test_source_recovery.py','tests/test_operator_status.py','tests/test_useful_selection.py','tests/test_coverage_context.py','tests/test_acceptance_v33.py','tests/fixtures/coverage-requirements.json')


def code_hashes():return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in INPUTS}


def load_isolated(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def validate_receipt(path):
    receipt=json.loads(Path(path).read_text(encoding='utf-8'))
    if receipt.get('code_hashes')!=code_hashes():raise ValueError('Offline receipt does not match current code')
    profile_version=json.loads((ROOT/'config/order-profile.json').read_text(encoding='utf-8'))['version']
    if profile_version in ('maxmadepro-2026-11-09-v3.2','maxmadepro-2026-11-09-v3.3'):
        schema='useful-selection-v33' if profile_version.endswith('3.3') else 'useful-selection-v32'
        if not (receipt.get('schema')==schema and receipt.get('tests_passed') and receipt.get('required_examples_passed')
                and receipt.get('holdout_evaluated') and receipt.get('development_fn')==0 and receipt.get('holdout_fn')==0
                and receipt.get('development_extra_auto')==0 and receipt.get('holdout_extra_auto')==0
                and all(len(receipt.get(key,''))==64 for key in ('development_labels_sha256','holdout_labels_sha256','sample_sha256'))):
            raise ValueError('Useful-work quality/test gate has not passed')
        if schema=='useful-selection-v33' and not all(receipt.get(k) for k in ('contrasts_passed','format_invariance_passed','positive_holdout_checked','known_positive_limit_disclosed')):
            raise ValueError('Coverage contrast / positive-evidence gate has not passed')
        return receipt
    if not receipt.get('tests_passed') or receipt.get('holdout_fn')!=0 or receipt.get('development_fn')!=0:
        raise ValueError('Offline quality/test gate has not passed')
    if receipt.get('labels_sha256')!='a145cecee4f359884d22b4a9be0e7b7d99fbb218356ecfdf03d06b41eacff284':
        raise ValueError('Frozen labels changed')
    return receipt


def hot_apply(reader,version):
    import automation,local_chrome,monitor
    importlib.reload(filter_policy)
    if version not in (filter_policy.V2,filter_policy.V3):raise ValueError('Unsupported release; no switch applied')
    if version==filter_policy.V3:
        validate_receipt(automation.STATE/'filter-validation.json')
        if not reader.metadata()['all_sources_reading']:raise ValueError('Keep all existing sources healthy before activation')
    # Load first. No browser/callback tasks, locks or sessions are recreated.
    profile=load_isolated('_selection_v3_profile',ROOT/'order_profile.py')
    # Reload pure helpers on the event loop, without creating readers or yielding.
    import order_selection
    importlib.reload(order_selection)
    import message_classification,selection_facts,intent_frames,maxmade_selection,selection_engine
    for module in (message_classification,selection_facts,intent_frames,maxmade_selection,selection_engine):importlib.reload(module)
    importlib.reload(filter_policy)
    replacement=load_isolated('_selection_monitor_release',ROOT/'monitor.py')
    replacement.order_profile=profile
    for name in ('ApiError','UnknownDelivery','Telegram','Config'):
        setattr(replacement,name,getattr(monitor,name))
    target=automation.STATE/('filter-switch-'+monitor.now().strftime('%Y%m%dT%H%M%SZ'))
    local_chrome.private_directory(target)
    before={'at':monitor.iso(monitor.now()),'pid':__import__('os').getpid(),
      'manual_statuses':{str(r[0]):r[1] for r in reader.db.execute('SELECT id,owner_status FROM candidates')},
      'sent_history':{str(r[0]):list(r[1:]) for r in reader.db.execute("SELECT id,state,message_id,sent_at FROM deliveries WHERE state='sent'")},
      'env_sha256':hashlib.sha256((ROOT/'.env').read_bytes()).hexdigest(),
      'source_names':[s.name for s in reader.sessions],
      'generations':{s.name:s.generation for s in reader.sessions},'code_hashes':code_hashes()}
    with contextlib.closing(sqlite3.connect(target/'business-before.sqlite3')) as copy:reader.db.backup(copy)
    (target/'before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
    filter_policy.ensure_schema(reader.db)
    if version==filter_policy.V3 and not filter_policy.get_meta(reader.db,'filter_initial_cutoff'):
        since=monitor.iso(monitor.now()-__import__('datetime').timedelta(hours=48))
        with reader.db:
            for row in reader.db.execute('''SELECT c.id,c.kind,e.* FROM candidates c JOIN events e ON e.id=c.event_id
              JOIN deliveries d ON d.candidate_id=c.id WHERE d.state='sent' AND e.received_at>=? AND e.body IS NOT NULL''',(since,)).fetchall():
                filter_policy.remember(reader.db,row[0],row['body'],row['author'],row['link'],row['source_id'],
                                       row['edit_version'],row['received_at'],row['kind']=='demand')
    for name in ('connect','classify','ingest','due_rows','render','deliver','deliver_revisions','recover_interrupted','prune'):
        setattr(monitor,name,getattr(replacement,name))
    monitor.order_profile=profile
    import multi_source
    importlib.reload(multi_source)
    session_methods_updated=0
    for session in reader.sessions:
        if hasattr(session,'definition'):
            session.__class__=multi_source.SourceSession;session_methods_updated+=1
    reader.delivery_cycle=types.MethodType(multi_source.MultiReader.delivery_cycle,reader)
    import browser_context
    importlib.reload(browser_context)
    context_update=browser_context.install_live_capture(reader,ROOT)
    result=filter_policy.activate(reader.db,version,monitor.iso(monitor.now()))
    receipt=result|{'pid':before['pid'],'sources':before['source_names'],'before_path':str(target),
      'sources_restarted':0,'callbacks_workers_added':0,'code_hashes':code_hashes(),'browser_context':context_update,
      'source_methods_updated':session_methods_updated,'stability_version':'bounded-network-pause-20261109-v1'}
    automation.write_json(automation.STATE/'filter-active.json',receipt)
    return {'filter_policy':{key:receipt[key] for key in ('version','initial_cutoff','at','pid','sources_restarted','callbacks_workers_added')}}


def main():
    import automation
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['activate','rollback','status','queue'])
    parser.add_argument('--route',choices=['partnership','employment','review'],default='review')
    args=parser.parse_args()
    if args.action in ('activate','rollback'):
        import local_chrome
        local_chrome.command('runtime_filter_v3' if args.action=='activate' else 'runtime_filter_v2')
    else:
        with contextlib.closing(sqlite3.connect(automation.config().db.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            if args.action=='status':
                result={'version':filter_policy.get_meta(db,'filter_active_version',filter_policy.V2),
                  'cutoff':filter_policy.get_meta(db,'filter_initial_cutoff'),
                  'decisions':[list(r) for r in db.execute('SELECT route,count(*) FROM filter_decisions GROUP BY route')]}
            else:result=[dict(r) for r in filter_policy.queue_rows(db,args.route)]
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
