"""Manual group batches of five; never joins, sends messages or advances itself."""
from pathlib import Path
import json
import os
import subprocess

import chrome_pilot
import monitor
from manual_group_tabs import GROUPS

ROOT=Path(__file__).resolve().parent
FILE=ROOT/'state/local-chrome/group-batch.json'
FIRST=('bitrixinside24','biznes_chat','businessclass_rbc','onpeak_chat','odp2021','expopeople')


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


async def open_batch(page,context,state,next_batch=False):
    # No bulk join job is started in this mode.
    state['manual_join_mode']=True
    await page.locator('#column-left .sidebar-tools-button').wait_for(timeout=20000)
    await chrome_pilot.own_settings(page,state)
    previous=read(FILE)
    joined=read(FILE.with_name('authorized-joins-20261105.json'))
    excluded={r['username'] for r in joined.get('groups',[]) if r.get('state') in ('joined','already_joined','request_pending')}
    queue=[n for n in (*FIRST,*(n for n in GROUPS if n not in FIRST)) if n not in excluded]
    offset=int(previous.get('offset',0))+(5 if next_batch else 0)
    names=queue[offset:offset+5]
    record={'at':monitor.iso(monitor.now()),'offset':offset,'requested':names,'opened':[],
            'automatic_join':False,'automatic_outreach':False,'automatic_next_batch':False}
    FILE.write_text(json.dumps(record,indent=2),encoding='utf-8')
    targets=[]
    reusable=[p for p in context.pages if p is not page and not p.is_closed()]
    for index,name in enumerate(names):
        target=page if index==0 else reusable[index-1] if index<=len(reusable) else await context.new_page()
        await target.goto('https://web.telegram.org/k/#@'+name,wait_until='domcontentloaded',timeout=25000)
        if index==0 or index<=len(reusable):await target.reload(wait_until='domcontentloaded',timeout=25000)
        targets.append(target);record['opened'].append(name)
        FILE.write_text(json.dumps(record,indent=2),encoding='utf-8')
    if targets:
        await targets[0].bring_to_front()
        cdp=await context.new_cdp_session(targets[0])
        try:
            wid=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'normal'}})
        finally:await cdp.detach()
    record['complete']=True
    FILE.write_text(json.dumps(record,indent=2),encoding='utf-8')
    return {'phase':'manual_group_batch','group_batch_opened':len(record['opened']),
            'group_batch':record['opened'],'automatic_next_batch':False,'error_type':None}


def launch(next_batch=False):
    import local_chrome
    if local_chrome.host_running():
        local_chrome.command('runtime_next_five_groups' if next_batch else 'runtime_open_five_groups');return
    python=ROOT/'.venv/Scripts/pythonw.exe'
    child=subprocess.Popen([str(python),str(ROOT/'local_chrome.py'),'batch5-next' if next_batch else 'batch5'],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    print(json.dumps({'batch_browser_starting':True,'pid':child.pid,'maximum_groups':5}))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Open the current or next manual batch of five')
    parser.add_argument('action',nargs='?',choices=['current','next'],default='current')
    launch(next_batch=parser.parse_args().action=='next')
