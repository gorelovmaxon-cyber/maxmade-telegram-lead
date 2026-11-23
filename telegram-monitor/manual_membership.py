"""Read membership controls for the 20 manual candidates; never join or read bodies."""
import asyncio
import json
from pathlib import Path
import re

import chrome_pilot
import monitor
from manual_group_tabs import GROUPS

FILE=Path(__file__).resolve().parent/'state/local-chrome/manual-membership.json'


def save(rows,complete=False):
    temp=FILE.with_suffix('.tmp')
    temp.write_text(json.dumps({'at':monitor.iso(monitor.now()),'complete':complete,'groups':rows},indent=2),encoding='utf-8')
    temp.replace(FILE)


async def check_one(page,name):
    result={'username':name,'state':'unconfirmed','join_clicked':False}
    await page.goto('https://web.telegram.org/k/#@'+name,wait_until='domcontentloaded',timeout=20000)
    # Web K does not always react to external hash navigation.
    await page.reload(wait_until='domcontentloaded',timeout=20000)
    await page.locator('.chat.active .topbar .chat-info-container').wait_for(timeout=12000)
    await chrome_pilot.own_settings(page,{})
    panel=page.locator('#column-right .profile-content')
    if not await panel.is_visible():await page.locator('.chat.active .topbar .chat-info-container').click(timeout=5000)
    await panel.wait_for(state='visible',timeout=8000)
    link=page.locator('#column-right .row-title').filter(has_text=re.compile('^t\\.me/'+re.escape(name)+'$',re.I))
    await link.wait_for(state='visible',timeout=8000)
    controls=await page.evaluate('''()=>{
      const nodes=[...document.querySelectorAll('#column-right .profile-action,.chat.active .chat-input button')].filter(e=>e.getClientRects().length);
      return {
        leave:nodes.filter(e=>/leave|покинуть|выйти/i.test(e.textContent)).length,
        join:nodes.filter(e=>/join|вступить|присоединиться|подать заявку/i.test(e.textContent)).length,
        pending:nodes.filter(e=>/cancel request|отменить заявку|request sent|заявка отправлена/i.test(e.textContent)).length,
        peer_count:new Set([...document.querySelectorAll('.chat.active .topbar [data-peer-id]')].map(e=>e.dataset.peerId)).size
      }
    }''')
    result.update(account_verified=True,source_verified=True,controls=controls)
    if controls['peer_count']!=1:return result
    if controls['pending']:result['state']='request_pending'
    elif controls['leave']==1 and controls['join']==0:result['state']='joined'
    elif controls['leave']==0 and controls['join']>0:result['state']='not_joined'
    return result


async def reopen_remaining(page,context,state):
    state['manual_join_mode']=True
    await chrome_pilot.own_settings(page,state)
    rows=[];probe=await context.new_page()
    try:
        for name in GROUPS:
            try:row=await check_one(probe,name)
            except chrome_pilot.BrowserAccessError:
                rows.append({'username':name,'state':'account_not_verified','join_clicked':False})
                save(rows)
                raise
            except Exception as error:row={'username':name,'state':'unconfirmed','error_type':type(error).__name__,'join_clicked':False}
            rows.append(row);save(rows)
    finally:await probe.close()
    # Reuse existing tabs; no duplicate copy for a joined source.
    reopened=[];first=None
    for row in rows:
        if row['state'] in ('joined','request_pending'):continue
        name=row['username'];url='https://web.telegram.org/k/#@'+name
        tab=next((p for p in context.pages if p is not page and p.url==url),None)
        tab=tab or await context.new_page()
        try:
            await tab.goto(url,wait_until='commit',timeout=15000)
            row['reopened']=True;reopened.append(name)
            if first is None:first=tab
        except Exception as error:row.update(reopened=False,reopen_error_type=type(error).__name__)
        save(rows)
    save(rows,True)
    if first:
        await first.bring_to_front()
        cdp=await context.new_cdp_session(first)
        try:
            wid=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'normal'}})
        finally:await cdp.detach()
    return {'membership_checked':len(rows),'already_joined':sum(r['state']=='joined' for r in rows),
            'request_pending':sum(r['state']=='request_pending' for r in rows),'reopened_remaining':len(reopened)}


async def recheck_existing(page,context,state):
    """Refresh membership controls in the already opened tabs, without 20 reloads."""
    state['manual_join_mode']=True
    await chrome_pilot.own_settings(page,state)
    rows=[];remaining=[];candidate_pages=[p for p in context.pages if p is not page]
    targets={}
    for index,name in enumerate(GROUPS):
        url='https://web.telegram.org/k/#@'+name
        tab=next((p for p in context.pages if p is not page and p.url.lower()==url.lower()),None)
        # Web K may canonicalize a hash to the numeric peer. The previous command
        # created these 20 tabs in catalogue order; still verify the public link.
        if tab is None and len(candidate_pages)==len(GROUPS):tab=candidate_pages[index]
        targets[name]=tab
        row={'username':name,'state':'unconfirmed','join_clicked':False}
        if tab is None:
            rows.append(row);save(rows);continue
        try:
            await tab.locator('.chat.active .topbar .chat-info-container').wait_for(timeout=3000)
            panel=tab.locator('#column-right .profile-content')
            if not await panel.is_visible():await tab.locator('.chat.active .topbar .chat-info-container').click(timeout=3000)
            await panel.wait_for(state='visible',timeout=3000)
            link=tab.locator('#column-right .row-title').filter(has_text=re.compile('^t\\.me/'+re.escape(name)+'$',re.I))
            await link.wait_for(state='visible',timeout=3000)
            proof=await tab.evaluate('''()=>{
              const texts=[...document.querySelectorAll('#column-right .profile-action,.chat.active .chat-input button')].map(e=>e.textContent||'');
              return {leave:texts.filter(t=>/leave|покинуть|выйти/i.test(t)).length,
                join:texts.filter(t=>/join|вступить|присоединиться|подать заявку/i.test(t)).length,
                pending:texts.filter(t=>/cancel request|отменить заявку|request sent|заявка отправлена/i.test(t)).length}
            }''')
            row['controls']=proof
            if proof['pending']:row['state']='request_pending'
            elif proof['leave']==1 and proof['join']==0:row['state']='joined'
            elif proof['join']>0 and proof['leave']==0:row['state']='not_joined'
        except Exception as error:row['error_type']=type(error).__name__
        rows.append(row);save(rows)
        if row['state'] not in ('joined','request_pending'):remaining.append(tab)
    await chrome_pilot.own_settings(page,state)
    # Close only confirmed joined candidate tabs opened by our previous command.
    # Do not close a tab with active input or a focused element.
    for row in rows:
        if row['state']!='joined':continue
        url='https://web.telegram.org/k/#@'+row['username']
        tab=targets.get(row['username'])
        if tab:
            editing=await tab.evaluate('''()=>document.hasFocus() || [...document.querySelectorAll('input,textarea,[contenteditable=true]')].some(e=>e.getClientRects().length && (e.value||e.textContent||'').trim())''')
            if not editing:await tab.close();row['joined_tab_closed']=True
    if remaining:
        await remaining[0].bring_to_front()
        cdp=await context.new_cdp_session(remaining[0])
        try:
            wid=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'normal'}})
        finally:await cdp.detach()
    save(rows,True)
    return {'membership_checked':len(rows),'already_joined':sum(r['state']=='joined' for r in rows),
            'request_pending':sum(r['state']=='request_pending' for r in rows),'remaining_tabs':len(remaining)}


async def show_confirmed_unjoined(page,context,state):
    data=json.loads(FILE.read_text(encoding='utf-8'))
    names=[r['username'] for r in data['groups'] if r['state']=='not_joined' and r['username'] in GROUPS]
    await chrome_pilot.own_settings(page,state)
    shown=[];first=None
    for name in names:
        url='https://web.telegram.org/k/#@'+name
        tab=next((p for p in context.pages if p is not page and not p.is_closed() and p.url.lower()==url.lower()),None)
        if tab is None:tab=await context.new_page()
        try:
            await tab.goto(url,wait_until='commit',timeout=15000)
            shown.append(name)
            if first is None:first=tab
        except Exception:pass
    if first and not first.is_closed():
        await first.bring_to_front()
        cdp=await context.new_cdp_session(first)
        try:
            wid=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'normal'}})
        finally:await cdp.detach()
    result={'at':monitor.iso(monitor.now()),'shown':shown,'count':len(shown),'automatic_joins':0,
            'unconfirmed':sum(r['state']=='unconfirmed' for r in data['groups'])}
    receipt=FILE.with_name('manual-membership-reopened.json')
    receipt.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return {'manual_unjoined_reopened':len(shown),'unjoined_names':shown}


async def retry_failed_tabs(page,context,state):
    data=json.loads(FILE.read_text(encoding='utf-8'))
    receipt=FILE.with_name('manual-membership-reopened.json')
    previous=json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else {'shown':[]}
    shown=list(previous.get('shown',[]));first=None
    missing=[r['username'] for r in data['groups'] if r['state']=='not_joined' and r['username'] in GROUPS and r['username'] not in shown]
    verifier=None
    if missing:
        verifier=await context.new_page()
        await verifier.goto('https://web.telegram.org/k/#@'+missing[0],wait_until='domcontentloaded',timeout=20000)
        await verifier.locator('#column-left .sidebar-tools-button').wait_for(timeout=15000)
        await chrome_pilot.own_settings(verifier,{})
    for index,name in enumerate(missing):
        # A discarded/crashed renderer can retain an unusable old Page handle.
        tab=verifier if index==0 else await context.new_page()
        try:
            await tab.goto('https://web.telegram.org/k/#@'+name,wait_until='commit',timeout=15000)
            shown.append(name)
            if first is None:first=tab
        except Exception:pass
    if first and not first.is_closed():
        await first.bring_to_front()
        cdp=await context.new_cdp_session(first)
        try:
            wid=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'normal'}})
        finally:await cdp.detach()
    receipt.write_text(json.dumps({**previous,'at':monitor.iso(monitor.now()),'shown':shown,'count':len(shown)},indent=2),encoding='utf-8')
    return {'manual_unjoined_reopened':len(shown),'unjoined_names':shown}
