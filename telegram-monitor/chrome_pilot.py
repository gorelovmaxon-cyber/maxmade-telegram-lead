"""Bounded Web K pilot: no credentials, history fetch, joins or startup task.

Both identity panels remain open; replacing/closing them disarms capture.
"""
from __future__ import annotations
import asyncio
import contextlib
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import monitor
from browser_capture import BrowserCapture, DomContract
from browser_ingest import BrowserAccessError, BrowserIdentity, BrowserIntake

ACCOUNT='maxmade_pro'
SOURCE='top_ads_freelance'
PEER=-1001513982592
DOM_PEER='-1513982592'
ROOT_SELECTOR='.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)'
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'state/local-chrome'
USERNAME_ROWS="""e=>[...e.querySelectorAll('.row')].filter(r=>/^(Username|Имя пользователя|Имя пользователя Telegram)$/i.test(r.querySelector('.row-subtitle')?.textContent?.trim()||'')).map(r=>r.querySelector('.row-title')?.textContent?.trim())"""
OWN_IDENTITY_SNAPSHOT="""()=>{const panels=[...document.querySelectorAll('.settings-container.active')];
  const own=[...document.querySelectorAll('.settings-container.active .profile-content.is-me')];
  return {settings_count:panels.length,own_count:own.length,visible_own_count:own.filter(e=>e.getClientRects().length).length,
    names:panels.flatMap(p=>[...p.querySelectorAll('.row')].filter(r=>/^(Username|Имя пользователя|Имя пользователя Telegram)$/i.test(r.querySelector('.row-subtitle')?.textContent?.trim()||''))
      .map(r=>r.querySelector('.row-title')?.textContent?.trim()||''))};}"""


def check_own_snapshot(proof,state):
    names=proof['names']
    contradictory=any(name and name!=ACCOUNT for name in names)
    state['identity_evidence']={k:v for k,v in proof.items() if k!='names'}
    state['identity_evidence'].update(username_rows=len(names),expected_username_rows=names.count(ACCOUNT),
                                      contradictory_username=contradictory)
    if contradictory:
        state['failure_class']='manual_attention';raise BrowserAccessError('Account identity contradicts configured account')
    if proof['settings_count']!=1 or proof['own_count']!=1 or proof['visible_own_count']!=1 or names!=[ACCOUNT]:
        state['failure_class']='identity_check_pending';raise BrowserAccessError('Own identity evidence incomplete')


def publish(**record):
    # Only metadata goes to the host status, never credentials or message bodies.
    import local_chrome
    local_chrome.status(**record)


def origin_ok(page):
    u=urlsplit(page.url)
    return u.scheme=='https' and u.hostname=='web.telegram.org' and u.port in (None,443) and u.path.startswith('/k/') and not u.username and not u.password


async def menu_activate(locator):
    # Web K uses mousedown on touch-capable Windows hardware. Only Settings
    # and the menu backdrop use this helper, never membership action buttons.
    await locator.evaluate("e=>{e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));e.click()}")


async def dismiss_menu(page):
    overlay=page.locator('.btn-menu-overlay:visible')
    if await overlay.count():
        await menu_activate(overlay)
        await overlay.wait_for(state='hidden',timeout=5000)


async def own_settings(page,state):
    state.pop('account',None)
    state.update(failure_class='identity_check_pending',guard_stage='own_account_snapshot',identity_evidence=None)
    if not origin_ok(page):
        state['failure_class']='manual_attention';raise BrowserAccessError('Account page origin differs')
    if await page.locator('input[type=tel]:visible,input[type=password]:visible,#auth-pages:not(.hide) .qr-canvas:visible').count():
        state['failure_class']='manual_attention';raise BrowserAccessError('Login required')
    own=page.locator('.settings-container.active .profile-content.is-me')
    if await own.count()>1:check_own_snapshot(await page.evaluate(OWN_IDENTITY_SNAPSHOT),state)
    if not await own.is_visible():
        await dismiss_menu(page)
        await page.locator('#column-left .sidebar-tools-button').click(timeout=5000)
        item=page.locator('.btn-menu-item:visible').filter(has_text=re.compile(r'Настройки|Settings'))
        await menu_activate(item)
        await page.locator('.settings-container.active .profile-content.is-me').wait_for(state='visible',timeout=10000)
    settings=page.locator('.settings-container.active')
    await page.wait_for_function('''()=>{
      const panel=document.querySelector('.settings-container.active');
      return panel && [...panel.querySelectorAll('.row')].some(row=>
        /^(Username|Имя пользователя|Имя пользователя Telegram)$/i.test(row.querySelector('.row-subtitle')?.textContent?.trim()||'') &&
        !!row.querySelector('.row-title')?.textContent?.trim());
    }''',timeout=10000)
    # Counts and username must describe one DOM instant, not two protocol reads
    # around a replacement of the Settings panel.
    check_own_snapshot(await page.evaluate(OWN_IDENTITY_SNAPSHOT),state)
    state.update(own=await own.element_handle(),settings=await settings.element_handle(),account=ACCOUNT)
    state.update(failure_class=None,guard_stage='own_account_verified')
    return {'account_username':'@'+ACCOUNT,'account_verified':True}


async def source_profile(page,state):
    await dismiss_menu(page)
    profile=page.locator('#column-right .profile-content')
    link=page.locator('#column-right .row-title').filter(has_text=re.compile(r'^t\.me/top_ads_freelance$'))
    if not await profile.is_visible() or not await link.is_visible():
        await page.locator('.chat.active .topbar .chat-info-container').click(timeout=5000)
    await profile.wait_for(state='visible',timeout=10000)
    await link.wait_for(state='visible',timeout=10000)
    # Settings can reflow/recreate the bubbles. Establish the DOM contract only
    # after both panels have finished opening, not before that last UI action.
    await own_settings(page,state)
    await profile.wait_for(state='visible',timeout=10000)
    await link.wait_for(state='visible',timeout=10000)
    await page.wait_for_function('''()=>document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)').length===1 &&
      document.querySelectorAll('.chat.active .bubbles.scrolled-down').length===1 &&
      [...document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover) .bubble[data-mid][data-peer-id][data-timestamp]')].some(e=>{const b=e.getBoundingClientRect();return e.dataset.peerId==='-1513982592' && !e.classList.contains('is-date') && b.width>0 && b.height>0})''',timeout=30000)
    evidence=await page.evaluate('''()=>({
      leave:[...document.querySelectorAll('#column-right .profile-action')].filter(e=>e.getClientRects().length && /Leave|Покинуть|Выйти/i.test(e.textContent)).length,
      join:[...document.querySelectorAll('#column-right .profile-action,.chat.active .chat-input button')].filter(e=>e.getClientRects().length && /Join|Вступить|Подать заявку/i.test(e.textContent)).length,
      peers:[...document.querySelectorAll('.chat.active .topbar .person-avatar[data-peer-id]')].map(e=>e.dataset.peerId),
      roots:document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)').length,
      bubbles:document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover) .bubble[data-mid][data-peer-id][data-timestamp]').length,
      at_end:document.querySelectorAll('.chat.active .bubbles.scrolled-down').length===1,
      metadata_valid:[...document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover) .bubble[data-mid][data-peer-id][data-timestamp]')].filter(e=>e.dataset.peerId==='-1513982592' && /^[1-9][0-9]*$/.test(e.dataset.mid) && Number(e.dataset.mid)<8589934592 && Number.isSafeInteger(Number(e.dataset.timestamp)) && Number(e.dataset.timestamp)>0).length
    })''')
    peers=evidence.pop('peers',[]);same_peer=bool(peers) and set(peers)=={DOM_PEER}
    state.update(source_profile=await profile.element_handle(),source_link=await link.element_handle(),
                 member=evidence['leave']==1 and evidence['join']==0 and same_peer,
                 contract_verified=evidence['roots']==1 and evidence['metadata_valid']>0 and evidence['at_end'] and same_peer)
    return {'account_username':'@'+ACCOUNT,'account_verified':True,'source_username':'@'+SOURCE,'source_verified':state['member'],'membership_required':evidence['join']>0,
            'source_evidence':{**evidence,'pinned_peer_matches':same_peer}}


async def verify_live(page,state):
    state['failure_class']='manual_attention'
    if not origin_ok(page) or not state.get('account') or not state.get('member'):
        raise BrowserAccessError('Identity was not verified')
    # Handles refer to the verified document, not a new node with the same selector.
    for key in ('own','settings','source_profile','source_link'):
        if not state.get(key) or not await state[key].evaluate('(e)=>e.isConnected'):
            raise BrowserAccessError('Verified identity panel was replaced')
    if not await state['own'].evaluate("e=>e.matches('.profile-content.is-me') && !!e.getClientRects().length"):
        raise BrowserAccessError('Own identity panel closed')
    if await state['settings'].evaluate(USERNAME_ROWS)!=[ACCOUNT]:
        raise BrowserAccessError('Account changed')
    if await state['source_link'].evaluate('(e)=>e.textContent.trim()')!='t.me/'+SOURCE:
        raise BrowserAccessError('Source changed')
    guard=await page.evaluate('''p=>({
      online:navigator.onLine,
      connection_pending:[...document.querySelectorAll('#chatlist-container > .sr-only[role="status"]')].some(e=>(e.textContent||'').trim().length>0),
      own:document.querySelectorAll('.settings-container.active .profile-content.is-me').length,
      source:[...document.querySelectorAll('.chat.active .topbar .person-avatar[data-peer-id]')].map(e=>e.dataset.peerId),
      profileVisible:!!document.querySelector('#column-right .profile-content')?.getClientRects().length,
      leave:[...document.querySelectorAll('#column-right .profile-action')].filter(e=>/Leave|Покинуть|Выйти/i.test(e.textContent)).length,
      join:[...document.querySelectorAll('#column-right .profile-action')].filter(e=>/Join|Вступить|Подать заявку/i.test(e.textContent)).length,
      roots:document.querySelectorAll(p).length
      ,at_end:document.querySelectorAll('.chat.active .bubbles.scrolled-down').length===1
    })''',ROOT_SELECTOR)
    if not guard['online'] or guard['connection_pending']:
        state['failure_class']='network_unavailable'
    if not (guard['online'] and not guard['connection_pending'] and guard['own']==1 and guard['profileVisible'] and guard['source']
            and set(guard['source'])=={DOM_PEER} and guard['leave']==1 and guard['join']==0 and guard['roots']==1 and guard['at_end']):
        raise BrowserAccessError('Live account/source/access guard failed')
    state['failure_class']=None
    state['last_live_check']=monitor.iso(monitor.now())
    return BrowserIdentity(ACCOUNT,SOURCE,PEER,True,page.url)


def aggregate(db):
    return {'events':db.execute('select count(*) from events').fetchone()[0],
            'candidates':db.execute('select count(*) from candidates').fetchone()[0],
            'deliveries':dict(db.execute('select state,count(*) from deliveries group by state')),
            'owner_statuses':dict(db.execute('select owner_status,count(*) from candidates group by owner_status'))}


def delivery_candidates(db):
    # Persisted Chrome candidates survive quiet hours/restarts; the old pending
    # queue has no browser version marker and stays outside this reader's scope.
    return tuple(row[0] for row in db.execute('''SELECT DISTINCT c.id FROM candidates c
        JOIN events e ON e.id=c.event_id JOIN deliveries d ON d.candidate_id=c.id
        WHERE e.source_id=? AND e.edit_version LIKE 'browser:%' AND d.state='pending' ''',('client:'+str(PEER),)))


def poll_owner_callbacks(db,cfg,api):
    updates=api.call('getUpdates',{'offset':int(monitor.meta(db,'update_id','0'))+1,
        'timeout':0,'limit':50,'allowed_updates':['callback_query']},timeout=5)
    if not isinstance(updates,list):raise monitor.UnknownDelivery('Callback response invalid')
    for update in updates:
        if 'callback_query' in update:
            if (update['callback_query'].get('data') or '').startswith('t:'):
                import operator_checks
                operator_checks.handle_callback(cfg,update,api)
                with db:monitor.set_meta(db,'update_id',str(update['update_id']))
            else:monitor.process_update(db,cfg,update,api)
        else:
            # Telegram may include older buffered update types. No source ingest
            # or reply to incoming users occurs in this callback-only worker.
            with db:monitor.set_meta(db,'update_id',str(update['update_id']))


async def stop(state,reason='operator_stopped'):
    task=state.get('task')
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):await task
    capture=state.get('capture')
    if capture:await capture.stop()
    # A task cancelled before its first instruction never enters pilot.finally.
    db=state.pop('db',None)
    if db:db.close()
    if state.get('locks'):state['locks'].close()
    publish(phase=reason,capture_running=False,delivery_running=False)


async def pilot(page,state):
    capture=state['capture'];db=state['db'];cfg=state['cfg']
    started=time.monotonic();candidate_ids=set();observations=0;sent=0
    last_callback=-60.;callback_delay=10.;callback_status='not_enabled';last_transport=-60.
    reason='pilot_limit_reached'
    try:
        while state.get('run_limit_seconds',600) is None or time.monotonic()-started<state['run_limit_seconds']:
            ids=await capture.drain()
            if state.get('transport_probe_required') and time.monotonic()-last_transport>=30:
                # Public same-origin HEAD only: no content, auth export or API calls.
                transport_ok=await page.evaluate('''async()=>{const c=new AbortController();
                  const t=setTimeout(()=>c.abort(),8000);try{const r=await fetch('/k/?monitor_health='+Date.now(),
                    {method:'HEAD',cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer',signal:c.signal});return r.ok}
                  catch{return false}finally{clearTimeout(t)}}''')
                if not transport_ok:
                    state['failure_class']='network_unavailable'
                    raise BrowserAccessError('Public Web transport unavailable')
                last_transport=time.monotonic();state['last_web_transport']=monitor.iso(monitor.now())
            candidate_ids.update(ids);observations+=len(ids)
            # Only locally queued Chrome cards; Telegram is called when the
            # existing due/quiet/retry rules allow, never for rejected messages.
            scope=delivery_candidates(db)
            if scope:
                sent+=monitor.deliver(db,cfg,monitor.Telegram(cfg.token),monitor.now(),scope)
            if state.get('owner_callbacks') and time.monotonic()-last_callback>=callback_delay:
                last_callback=time.monotonic()
                try:
                    poll_owner_callbacks(db,cfg,monitor.Telegram(cfg.token))
                    callback_status='ok';callback_delay=10.
                except Exception as error:
                    callback_status=type(error).__name__;callback_delay=60.
            rows=[]
            if candidate_ids:
                rows=[dict(r) for r in db.execute(
                    'select c.id,c.kind,c.priority,c.owner_status,e.message_id,e.link from candidates c join events e on e.id=c.event_id where c.id in ('+','.join('?' for _ in candidate_ids)+')',tuple(candidate_ids))]
            publish(phase='reading' if state.get('run_limit_seconds') is None else 'bounded_pilot',capture_running=True,delivery_running=False,
                    pilot_observations=observations,pilot_sent=sent,pilot_elapsed_s=round(time.monotonic()-started),
                    pilot_candidates=rows,database=aggregate(db),owner_callbacks_status=callback_status,
                    last_live_check=state.get('last_live_check'),account_verified=True,source_verified=True,
                    last_web_transport=state.get('last_web_transport'),
                    failure_class=None,delivery_check=callback_status)
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        reason='operator_stopped';raise
    except Exception as error:
        failure=state.get('failure_class') or 'manual_attention'
        if state.get('navigation_revision',0)!=state.get('armed_revision',0) and page.url==state.get('armed_url'):
            failure='page_reloaded'
        reason='source_disarmed';publish(error_type=type(error).__name__,failure_class=failure,
            account_verified=False,source_verified=False)
    finally:
        await capture.stop()
        publish(phase=reason,capture_running=False,delivery_running=False,database=aggregate(db))
        db.close();state.pop('db',None);state['locks'].close()


async def arm(page,state,max_seconds=600):
    if state.get('task') and not state['task'].done():raise BrowserAccessError('Pilot already active')
    await verify_live(page,state)
    monitor.load_env(ROOT/'.env');cfg=monitor.Config.from_env();cfg.live_ready()
    if os.getenv('MONITOR_CLIENT_ACCOUNT','').lower().lstrip('@')!=ACCOUNT:
        raise BrowserAccessError('Reader account binding differs from the chosen account')
    if not cfg.db.is_absolute():cfg=__import__('dataclasses').replace(cfg,db=ROOT/cfg.db)
    me=monitor.Telegram(cfg.token).call('getMe',{},timeout=15)
    if me.get('username')!='uffweb_bot':raise BrowserAccessError('Wrong notification bot')
    if max_seconds is None and monitor.Telegram(cfg.token).call('getWebhookInfo',{},timeout=10).get('url'):
        raise BrowserAccessError('Existing webhook must not be modified automatically')
    locks=contextlib.ExitStack();db=None
    try:
        locks.enter_context(monitor.single_instance(cfg.db.with_suffix('.reader.lock')))
        locks.enter_context(monitor.single_instance(cfg.db.with_suffix('.lock')))
        db=monitor.connect(cfg.db)
        backup=STATE/('before-pilot-'+monitor.now().strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
        with contextlib.closing(sqlite3.connect(backup)) as copy:db.backup(copy)
        before=aggregate(db)
        # Reuse the existing crash rule: an interrupted send becomes uncertain,
        # never a blind retry after Windows/process termination.
        monitor.recover_interrupted(db)
        intake=BrowserIntake(db,cfg,ACCOUNT,SOURCE,PEER)
        capture=BrowserCapture(page,intake,DomContract(ROOT_SELECTOR,DOM_PEER,state.get('contract_verified',False)),lambda:verify_live(page,state))
        for attempt in range(3 if max_seconds is None else 1):
            if max_seconds is None:
                # Bot API validation may outlast Web K's final pane reflow.
                # Reverify after those calls, immediately before the baseline.
                checked=await source_profile(page,state)
                if not checked['source_verified']:raise BrowserAccessError('Membership no longer verified')
            try:
                await capture.arm()
                break
            except BrowserAccessError:
                if max_seconds is not None or attempt==2:raise
                await page.wait_for_timeout(350)
        state.update(capture=capture,db=db,cfg=cfg,locks=locks,run_limit_seconds=max_seconds,owner_callbacks=max_seconds is None)
        state['armed_revision']=state.get('navigation_revision',0)
        state['armed_url']=page.url
        state['task']=asyncio.create_task(pilot(page,state))
        return {'phase':'reading' if max_seconds is None else 'bounded_pilot','capture_running':True,'pilot_limit_seconds':max_seconds,'pilot_before':before,
                'account_verified':True,'source_verified':True,'delivery_running':False,'token_check':'accepted_uffweb_bot',
                'autostart_enabled':False,'uses_tinyfish':False,'error_type':None}
    except Exception:
        if db:db.close()
        locks.close();raise


async def dispatch(page,context,action,state):
    try:
        if not origin_ok(page):raise BrowserAccessError('Wrong browser origin')
        if action in ('settings','verify'):
            return {'phase':'own_account_verified',**await own_settings(page,state),'error_type':None}
        if action=='profile':
            return {'phase':'source_checked',**await source_profile(page,state),'error_type':None}
        if action=='source':
            if state.get('task') and not state['task'].done():await stop(state)
            state.pop('account',None);state['member']=False
            state['source_navigation']={'stage':'dismiss_menu'}
            await dismiss_menu(page)
            own=page.locator('.settings-container.active .profile-content.is-me')
            back=page.locator('#column-left .sidebar-header .sidebar-back-button:visible, #column-left .sidebar-header .sidebar-close-button:visible')
            state['source_navigation'].update(stage='close_settings',back_count=await back.count(),own_visible=await own.is_visible())
            if await own.is_visible() and await back.count()==1:
                await back.click(timeout=3000)
                await own.wait_for(state='hidden',timeout=4000)
            row=page.locator('.chatlist-chat[data-peer-id="'+DOM_PEER+'"]')
            state['source_navigation'].update(stage='native_chat_link',row_count=await row.count(),row_visible=await row.is_visible())
            if await row.count()==1 and await row.is_visible():
                # Native chat links run Web K's navigation handler. Changing the
                # hash/reloading can leave a previous chat in a shared worker.
                await row.click(timeout=5000)
            else:
                await page.goto('https://web.telegram.org/k/#@'+SOURCE,wait_until='domcontentloaded',timeout=30000)
                await page.reload(wait_until='domcontentloaded',timeout=30000)
            state['source_navigation']['stage']='verify_pinned_header'
            await page.locator('.chat.active .topbar .person-avatar[data-peer-id="'+DOM_PEER+'"]').first.wait_for(timeout=15000)
            state['source_navigation']['stage']='complete'
            return {'phase':'source_open_unverified','account_verified':False,'source_verified':False}
        if action=='arm':return await arm(page,state)
        if action=='stop':await stop(state);return {'phase':'operator_stopped','capture_running':False}
        if action=='drain':
            await verify_live(page,state)
            return {'phase':'live_identity_guard_passed','account_verified':True,'source_verified':True,'error_type':None}
        if action=='diagnose':
            return {'diagnostic':{'origin_verified':origin_ok(page),
                    'own_handle_connected':bool(state.get('own')) and await state['own'].evaluate('(e)=>e.isConnected'),
                    'root_count':await page.locator(ROOT_SELECTOR).count(),
                    'login_required':await page.locator('input[type="tel"],input[type="password"]').count()>0}}
        raise ValueError('Unsupported scoped command')
    except Exception as error:
        if state.get('task') and not state['task'].done():await stop(state,'source_disarmed')
        account_ok=False
        if state.get('settings') and state.get('own'):
            with contextlib.suppress(Exception):
                account_ok=await state['settings'].evaluate('(e)=>e.isConnected') and await state['settings'].evaluate(USERNAME_ROWS)==[ACCOUNT]
        return {'phase':'source_not_armed','action':action,'error_type':type(error).__name__,
                'capture_running':False,'delivery_running':False,'account_verified':account_ok,'source_verified':False,
                'source_navigation':state.get('source_navigation')}
