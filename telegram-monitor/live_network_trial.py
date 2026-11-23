"""Temporarily offline only this dedicated browser context; bounded restoration."""
import json
import time
import automation
import local_chrome
import monitor
from live_source_trials import snapshot,ready,RECEIPT


def run():
    before=snapshot()
    if len(before['sources'])<2 or not all(ready(r) and r.get('attempts')==0 for r in before['sources']):raise ValueError('Wait for live sources with reset retry budgets before a deliberate network trial')
    record={'case':'context_offline_online','started_at':monitor.iso(monitor.now()),'before':before}
    start=time.monotonic()
    try:
        local_chrome.command('offline');deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            rows=snapshot()['sources']
            if all(not ready(r) for r in rows):record['both_paused']=True;break
            time.sleep(.5)
        else:record['both_paused']=False
    finally:local_chrome.command('online')
    deadline=time.monotonic()+max(150,65*len(before['sources']))
    while time.monotonic()<deadline:
        after=snapshot()
        if all(ready(r) for r in after['sources']):
            record.update(result='recovered',elapsed_s=round(time.monotonic()-start,2),after=after);break
        time.sleep(2)
    else:record.update(result='not_recovered_in_budget',after=snapshot())
    old=automation.read_json(RECEIPT);old.setdefault('trials',[]).append(record);automation.write_json(RECEIPT,old)
    print(json.dumps(record))


if __name__=='__main__':run()
