"""Bounded local lifecycle trials, metadata only. Never posts test messages."""
import argparse
import json
import time
import automation
import local_chrome
import monitor

RECEIPT=automation.STATE/'two-source-trials.json'


def snapshot():
    host=automation.read_json(automation.STATE/'status.json')
    return {'at':host.get('at'),'pid':host.get('pid'),'sources':host.get('source_states',[])}


def ready(row):
    age=automation.age(row.get('last_live_check'))
    web_age=automation.age(row.get('last_web_transport'))
    return row.get('capture_running') and row.get('phase')=='reading' and age is not None and age<15 and web_age is not None and web_age<=60


def trial(action):
    before=snapshot()
    if len(before['sources'])<2 or not all(ready(r) for r in before['sources']):raise ValueError('Live staged sources required before trial')
    record={'case':action,'started_at':monitor.iso(monitor.now()),'before':before,'all_other_samples_live':True}
    start=time.monotonic()
    local_chrome.command('runtime_trial_'+action+'_one')
    deadline=time.monotonic()+150;interrupted=False
    while time.monotonic()<deadline:
        after=snapshot();rows=after['sources']
        others=[next((r for r in rows if r['username']==s['username']),{}) for s in before['sources'][:-1]]
        target=next((r for r in rows if r['username']==before['sources'][-1]['username']),{})
        record['all_other_samples_live'] &= all(ready(r) for r in others)
        interrupted |= not ready(target)
        generation=target.get('generation',0)>before['sources'][-1].get('generation',0)
        if interrupted and generation and ready(target):
            record.update(result='recovered',elapsed_s=round(time.monotonic()-start,2),after=after);break
        time.sleep(2)
    else:record.update(result='not_recovered_in_budget',after=snapshot())
    old=automation.read_json(RECEIPT);old.setdefault('trials',[]).append(record);automation.write_json(RECEIPT,old)
    print(json.dumps(record))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['close','reload']);trial(p.parse_args().action)
