"""Unexpected closure of only the monitor context; verify supervisor creates a fresh host."""
import json
import time
import automation
import local_chrome
import monitor
from live_source_trials import snapshot,ready,RECEIPT


def run():
    deadline=time.monotonic()+180
    before=snapshot()
    while before['sources'] and not all(ready(r) for r in before['sources']) and time.monotonic()<deadline:
        time.sleep(2);before=snapshot()
    names={r['username'] for r in before['sources']}
    if len(names)<2 or not all(ready(r) for r in before['sources']):raise ValueError('Live sources required')
    if automation.STOP.exists():raise ValueError('Operator stop must not be overridden')
    record={'case':'unexpected_context_close','started_at':monitor.iso(monitor.now()),'before':before}
    started=time.monotonic();local_chrome.command('runtime_trial_close_context')
    deadline=time.monotonic()+max(180,65*len(names)+30)
    while time.monotonic()<deadline:
        after=snapshot();rows=after['sources']
        if automation.STOP.exists():record['result']='operator_stop_detected';break
        if after['pid']!=before['pid'] and {r['username'] for r in rows}==names and all(ready(r) for r in rows):
            record.update(result='recovered',elapsed_s=round(time.monotonic()-started,2),after=after);break
        time.sleep(2)
    else:record.update(result='not_recovered_in_budget',after=snapshot())
    old=automation.read_json(RECEIPT);old.setdefault('trials',[]).append(record);automation.write_json(RECEIPT,old)
    print(json.dumps(record))


if __name__=='__main__':run()
