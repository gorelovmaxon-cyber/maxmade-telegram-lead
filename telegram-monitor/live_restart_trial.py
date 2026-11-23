"""Operator Stop/Start and host restart, without touching Windows or other apps."""
import json
import subprocess
import sys
import time
import automation
import local_chrome
import monitor
from live_source_trials import snapshot,ready,RECEIPT


def run():
    before=snapshot()
    expected=len(before['sources'])
    if expected<2 or not all(ready(r) for r in before['sources']):raise ValueError('Live staged sources required')
    record={'case':'operator_stop_start_and_host_restart','started_at':monitor.iso(monitor.now()),'before':before}
    def command(name):
        result=subprocess.run([sys.executable,str(automation.ROOT/'automation.py'),name],cwd=automation.ROOT,capture_output=True,text=True,timeout=20)
        if result.returncode:raise RuntimeError('Operation failed')
    command('stop');deadline=time.monotonic()+30
    while time.monotonic()<deadline and (local_chrome.host_running() or automation.process_running()):time.sleep(.5)
    record['fully_stopped']=not local_chrome.host_running() and not automation.process_running()
    if not record['fully_stopped']:raise RuntimeError('Do not restart before complete stop')
    time.sleep(8)
    record['stayed_stopped']=not local_chrome.host_running() and not automation.process_running()
    start=time.monotonic();command('start');deadline=time.monotonic()+max(160,65*expected+30)
    while time.monotonic()<deadline:
        after=snapshot()
        if after['pid']!=before['pid'] and len(after['sources'])==expected and all(ready(r) for r in after['sources']):
            record.update(result='recovered',elapsed_s=round(time.monotonic()-start,2),after=after);break
        time.sleep(2)
    else:record.update(result='not_recovered_in_budget',after=snapshot())
    old=automation.read_json(RECEIPT);old.setdefault('trials',[]).append(record);automation.write_json(RECEIPT,old)
    print(json.dumps(record))


if __name__=='__main__':run()
