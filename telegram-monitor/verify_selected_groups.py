"""Read-only access check of the two chosen batches and the original source.

One existing Chrome page, no joins, no messages, no history/body export.
"""
import asyncio
import contextlib
import json
import importlib
import os
from pathlib import Path
import re
import sqlite3
import subprocess

import chrome_pilot
import monitor
import group_navigation

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'state/local-chrome/selected-group-verification.json'
SELECTED=('top_ads_freelance','bitrixinside24','biznes_chat','businessclass_rbc','onpeak_chat','odp2021',
          'expopeople','free_chat_for_freelance','Frilanser_100','tilda_official_chat','marketologiyuga')


def save(result):
    result['at']=monitor.iso(monitor.now())
    tmp=OUT.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2),encoding='utf-8');tmp.replace(OUT)


async def check(page,name,record):
    await group_navigation.open_group(page,name,record)
    record['stage']='group_header'
    await page.locator('.chat.active .topbar .chat-info-container').wait_for(timeout=6000)
    record['stage']='open_group_profile'
    await chrome_pilot.dismiss_menu(page)
    profile=page.locator('#column-right .profile-content')
    link=page.locator('#column-right .row-title').filter(has_text=re.compile('^t\\.me/'+re.escape(name)+'$',re.I))
    if not await profile.is_visible() or not await link.is_visible():await page.locator('.chat.active .topbar .chat-info-container').click(timeout=3000)
    await profile.wait_for(state='visible',timeout=4000)
    record['stage']='verify_public_link'
    await link.wait_for(state='visible',timeout=4000)
    record['stage']='verify_own_account'
    await chrome_pilot.own_settings(page,{})
    # Opening Settings can replace the right pane; verify both after reflow.
    record['stage']='verify_profile_after_settings'
    await profile.wait_for(state='visible',timeout=4000)
    await link.wait_for(state='visible',timeout=4000)
    with contextlib.suppress(Exception):
        await page.wait_for_function("()=>document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)').length===1",timeout=3000)
    proof=await page.evaluate(r'''()=>{
      const actions=[...document.querySelectorAll('#column-right .profile-action')].map(e=>e.textContent||'');
      const peers=[...new Set([...document.querySelectorAll('.chat.active .topbar .person-avatar[data-peer-id]')].map(e=>e.dataset.peerId))];
      const root=document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)');
      const metadata=[...document.querySelectorAll('.chat.active .bubble[data-mid][data-peer-id][data-timestamp]')].filter(e=>!e.classList.contains('is-date'));
      const ruleLinks=[...document.querySelectorAll('#column-right a[href]')].map(e=>e.href).filter(s=>/^https:\/\/(t\.me\/[^/]+\/[0-9]+|blog\.tilda\.cc\/community-chat-rules)/i.test(s)).slice(0,8);
      return {leave:actions.filter(t=>/leave|покинуть|выйти/i.test(t)).length,
        join:actions.filter(t=>/join|вступить|присоединиться|подать заявку|subscribe|подписаться/i.test(t)).length,
        pending:actions.some(t=>/cancel request|отменить заявку|request sent|заявка отправлена/i.test(t)),
        peers,roots:root.length,visible_metadata:metadata.length,
        at_end:document.querySelectorAll('.chat.active .bubbles.scrolled-down').length===1,
        forum:!!document.querySelector('.topics-container,.forum-tab'),rules_links:ruleLinks};
    }''')
    peers=proof.pop('peers')
    pin=int(peers[0])-10**12 if len(peers)==1 and re.fullmatch(r'-[1-9][0-9]*',peers[0]) else None
    record.update(account_verified=True,source_verified=True,peer_id=pin,dom=proof)
    record['membership']='pending' if proof['pending'] else 'joined' if proof['leave']>0 and proof['join']==0 else 'not_joined' if proof['join']>0 and proof['leave']==0 else 'unconfirmed'
    record['message_metadata_accessible']=bool(pin and proof['roots']==1 and proof['visible_metadata']>0)
    record['stage']='complete'


async def run(page,context,state):
    importlib.reload(group_navigation)
    state['manual_join_mode']=True
    main=page
    active=bool(state.get('task') and not state['task'].done())
    if active:
        await chrome_pilot.verify_live(main,state)
        page=await context.new_page()
        await page.goto('https://web.telegram.org/k/',wait_until='domcontentloaded',timeout=20000)
    result={'selected':list(SELECTED),'groups':[],'complete':False,'automatic_joins':0,'reader_sources_added':0}
    await page.locator('#column-left .sidebar-tools-button').wait_for(timeout=20000)
    await chrome_pilot.own_settings(page,{} if active else state)
    save(result)
    for name in SELECTED:
        record={'username':name,'membership':'unconfirmed','stage':'starting'}
        result['groups'].append(record);save(result)
        try:await asyncio.wait_for(check(page,name,record),timeout=30)
        except chrome_pilot.BrowserAccessError:
            record['error_type']='account_not_verified';save(result);break
        except Exception as error:
            record['error_type']=type(error).__name__
            detail=str(error).lower()
            record['failure_signals']={key:phrase in detail for key,phrase in {
                'pointer_intercepted':'intercepts pointer events',
                'not_visible':'element is not visible',
                'not_stable':'element is not stable',
                'detached':'element was detached',
            }.items()}
        save(result)
        # Repeated identical UI failure needs diagnosis, not 11 blind timeouts.
        if len(result['groups'])>=2:
            a,b=result['groups'][-2:]
            if a.get('error_type') and a.get('stage')==b.get('stage') and a.get('error_type')==b.get('error_type'):
                result['stopped_for']='repeated_ui_failure';break
    result['complete']=len(result['groups'])==len(SELECTED) and all(r.get('stage')=='complete' for r in result['groups'])
    save(result)
    if active:
        await page.close()
        await chrome_pilot.verify_live(main,state)
        return {'selected_groups_checked':len(result['groups']),
                'joined_groups':[r['username'] for r in result['groups'] if r['membership']=='joined'],
                'verification_complete':result['complete']}
    return {'phase':'selected_groups_checked','selected_groups_checked':len(result['groups']),
            'joined_groups':[r['username'] for r in result['groups'] if r['membership']=='joined'],
            'verification_complete':result['complete'],'capture_running':False,'error_type':None}


def launch():
    import local_chrome
    if local_chrome.host_running():local_chrome.command('runtime_verify_selected');return
    py=ROOT/'.venv/Scripts/pythonw.exe'
    child=subprocess.Popen([str(py),str(ROOT/'local_chrome.py'),'check-selected'],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    print(json.dumps({'read_only_verification_starting':True,'pid':child.pid,'browser_pages':1}))


if __name__=='__main__':launch()
