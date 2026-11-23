"""Read-only frozen-sample replay. Writes reports only beside the private sample."""
import argparse,collections,hashlib,json
from pathlib import Path
import order_profile

AUTO={'main','partnership'}
def evaluate(folder,split):
    folder=Path(folder)
    labels={r['id']:r for r in json.loads((folder/(split+'-labels.json')).read_text(encoding='utf-8'))}
    sample=[r for r in json.loads((folder/'sample.json').read_text(encoding='utf-8')) if r['split']==split]
    if set(labels)!={r['id'] for r in sample}:raise ValueError('Complete frozen labels are required')
    rows=[]
    for r in sample:
        a=order_profile.assess(r['body']);label=labels[r['id']]
        rows.append({'id':r['id'],'expected':label['route'],'label_reason':label['reason'],
          'old_route':r['old_assessment']['notification_route'],'old_auto':r['old_assessment']['notification_route']=='main' or (r['old_assessment'].get('version') in ('maxmadepro-2026-11-09-v3.2','maxmadepro-2026-11-09-v3.3') and r['old_assessment']['notification_route']=='partnership'),
          'new_route':a['notification_route'],'new_auto':a['notification_route'] in AUTO,'assessment':a})
    stats={'rows':len(rows),'sources':len({r['source_id'] for r in sample}),
      'expected_routes':dict(collections.Counter(r['expected'] for r in rows)),
      'new_routes':dict(collections.Counter(r['new_route'] for r in rows)),
      'review':sum(r['new_route']=='review' for r in rows),
      'owner_confirmed_positives':sum(labels[r['id']].get('owner_confirmed',False) for r in rows)}
    for prefix in ('old','new'):
        stats[prefix]={
          'main':sum(r[prefix+'_route']=='main' for r in rows),
          'extra_main':sum(r[prefix+'_route']=='main' and r['expected']!='main' for r in rows),
          'missed_main':sum(r[prefix+'_route']!='main' and r['expected']=='main' for r in rows),
          'extra_auto':sum(r[prefix+'_auto'] and r['expected'] not in AUTO for r in rows),
          'missed_auto':sum(not r[prefix+'_auto'] and r['expected'] in AUTO for r in rows),
          'correct_subcontract':sum(r[prefix+'_auto'] and r[prefix+'_route']=='partnership' and r['expected']=='partnership' for r in rows)}
    return {'split':split,'profile_version':order_profile.profile()['version'],'stats':stats,'rows':rows,
      'sample_sha256':hashlib.sha256((folder/'sample.json').read_bytes()).hexdigest(),
      'labels_sha256':hashlib.sha256((folder/(split+'-labels.json')).read_bytes()).hexdigest(),
      'network_calls':0,'live_database_writes':0,'limits':'Agent labels; small positive count; no population accuracy claim'}
def main():
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--split',choices=['development','holdout'],required=True)
    args=p.parse_args();v=evaluate(args.folder,args.split)
    (args.folder/('evaluation-'+args.split+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(v['stats'],ensure_ascii=False))
if __name__=='__main__':main()
