"""Offline comparison on a private frozen sample. Never sends or touches live SQLite."""
import argparse
import collections
import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
import monitor
import order_profile
import order_profile_v2


def replay(folder,split):
    folder=Path(folder)
    labels=json.loads((folder/'labels.json').read_text(encoding='utf-8'))['labels']
    originals={r['id']:r for r in json.loads((folder/'originals.json').read_text(encoding='utf-8'))}
    spec=importlib.util.spec_from_file_location('_baseline_quality_monitor',folder/'baseline-code/monitor.py')
    old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
    old.order_profile=order_profile_v2
    clock=dt.datetime.fromisoformat(json.loads((folder/'baseline.json').read_text(encoding='utf-8'))['at'])
    old.now=lambda:clock
    original_now=monitor.now;monitor.now=lambda:clock
    rows=[]
    try:
        for label in labels:
            if label['split']!=split:continue
            r=originals[label['candidate_id']];published=dt.datetime.fromisoformat(r['published_at'])
            before=old.classify(r['body'],published,old.Config(Path('offline-only')))
            after=monitor.classify(r['body'],published,monitor.Config(Path('offline-only')))
            a=order_profile.assess(r['body'])
            rows.append({'candidate_id':r['id'],'family':label['family'],'split':split,'expected_main':label['main_notification'],
              'old_main':before[2]!='rejected','new_main':after[2]!='rejected','old_kind':before[0],
              'new_kind':a['kind'],'queue':a['notification_route'],'label_queue':label['queue'],
              'evidence_excerpt':label['evidence_excerpt'],'reason':label['reason']})
    finally:monitor.now=original_now
    stats={'rows':len(rows),'families':len({r['family'] for r in rows}),'positives':sum(r['expected_main'] for r in rows)}
    for prefix in ('old','new'):
        stats[prefix]={key:sum(predicate(r) for r in rows) for key,predicate in {
          'main':lambda r:r[prefix+'_main'], 'tp':lambda r:r[prefix+'_main'] and r['expected_main'],
          'fp':lambda r:r[prefix+'_main'] and not r['expected_main'],
          'fn':lambda r:not r[prefix+'_main'] and r['expected_main'],
          'tn':lambda r:not r[prefix+'_main'] and not r['expected_main']}.items()}
    return {'split':split,'stats':stats,'rows':rows,'labels_sha256':hashlib.sha256((folder/'labels.json').read_bytes()).hexdigest(),
      'snapshot_sha256':hashlib.sha256((folder/'snapshot.sqlite3').read_bytes()).hexdigest(),'version':order_profile.profile()['version'],
      'network_calls':0,'live_database_writes':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);parser.add_argument('--split',choices=['development','holdout'],required=True)
    args=parser.parse_args();result=replay(args.folder,args.split)
    (args.folder/('replay-'+args.split+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result['stats']))
    print(json.dumps([r for r in result['rows'] if r['expected_main']!=r['new_main']],ensure_ascii=False))
