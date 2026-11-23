"""Staged local Web K reader. Independent pages, one DB/notification owner.

No rotation/history catch-up, joins, client messages, API keys or external AI.
"""
from __future__ import annotations
import asyncio
import contextlib
from dataclasses import replace
import json
import time

import automation
import chrome_pilot as pilot
import group_navigation
import monitor
import source_registry as registry
import source_config
from browser_capture import BrowserCapture, DomContract
from browser_ingest import BrowserAccessError, BrowserIdentity, BrowserIntake

TRANSIENT_RECHECKS=3
TRANSIENT_DELAY=2


class SourceSession:
    def __init__(self,definition,context,business,cfg,journal,stop,attach_gate=None):
        self.definition=definition;self.name=definition['username'];self.peer=definition['peer_id']
        self.dom=str(self.peer+10**12);self.context=context;self.db=business;self.cfg=cfg;self.journal=journal
        self.stop_event=stop;self.page=None;self.capture=None;self.state={};self.phase='starting'
        self.last_live=None;self.last_web=None;self.error=None;self.attempts=0;self.baseline=None;self.generation=0;self.stage='created'
        self.last_failure=None
        self.attach_gate=attach_gate
        self.context_lost=False
        self.cooldown_cycles=0

    def record(self):
        registry.record_runtime(self.journal,self.name,self.phase,self.error,self.attempts,self.baseline,self.last_live)

    async def profile(self):
        self.stage='open_profile'
        profile=self.page.locator('#column-right .profile-content')
        # Exact public link PLUS numeric peer; neither alone authorizes intake.
        link=self.page.locator('#column-right .row-title').filter(has_text=__import__('re').compile('^t\\.me/'+__import__('re').escape(self.name)+'$',__import__('re').I))
        if not await profile.is_visible() or not await link.is_visible():
            await pilot.dismiss_menu(self.page)
            await self.page.locator('.chat.active .topbar .chat-info-container').click(timeout=5000)
        await link.wait_for(state='visible',timeout=10000)
        self.stage='verify_own_settings';await pilot.own_settings(self.page,self.state)
        await profile.wait_for(state='visible',timeout=10000)
        await link.wait_for(state='visible',timeout=10000)
        # Settings/right-pane reflow can move the live end after attach()'s
        # first jump. Use the native control again before establishing baseline.
        down=self.page.locator('.chat.active .bubbles-go-down:not(.hide):visible')
        if await down.count()==1:await down.click(timeout=4000)
        self.stage='wait_message_root'
        await self.page.wait_for_function('''p=>document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)').length===1 &&
          document.querySelectorAll('.chat.active .bubbles.scrolled-down').length===1 &&
          [...document.querySelectorAll('.chat.active .bubble[data-mid][data-peer-id][data-timestamp]')].some(e=>e.dataset.peerId===p && e.getClientRects().length)''',arg=self.dom,timeout=12000)
        self.state.update(source_profile=await profile.element_handle(),source_link=await link.element_handle())
        self.stage='verify_profile';await self.verify()

    async def verify(self):
        # A timeout/cancellation is unconfirmed, never evidence of an identity
        # mismatch. Only an actual contradictory result needs manual attention.
        self.state['failure_class']='identity_check_pending'
        self.state['identity_evidence']=None
        self.state['guard']=None;self.state['guard_stage']='page_origin'
        if self.page is None or self.page.is_closed() or not pilot.origin_ok(self.page):raise BrowserAccessError('Page unavailable')
        handles={key:self.state.get(key) for key in ('own','settings','source_profile','source_link')}
        if not all(handles.values()):raise BrowserAccessError('Identity panel missing')
        self.state['guard_stage']='identity_snapshot'
        # One synchronous DOM snapshot reduces protocol contention across tabs;
        # old verified handles must still belong to this exact document.
        proof=await self.page.evaluate('''h=>({
          handlesConnected:Object.values(h).every(e=>e?.isConnected && e.ownerDocument===document),
          usernames:h.settings?[...h.settings.querySelectorAll('.row')]
            .filter(r=>/^(Username|Имя пользователя|Имя пользователя Telegram)$/i.test(r.querySelector('.row-subtitle')?.textContent?.trim()||''))
            .map(r=>r.querySelector('.row-title')?.textContent?.trim()):[],
          publicLink:h.source_link?.textContent?.trim().toLowerCase(),
          online:navigator.onLine,
          connecting:[...document.querySelectorAll('#chatlist-container > .sr-only[role="status"]')].some(e=>(e.textContent||'').trim()),
          own:document.querySelectorAll('.settings-container.active .profile-content.is-me').length,
          ownVisible:!!document.querySelector('.settings-container.active .profile-content.is-me')?.getClientRects().length,
          profileVisible:!!document.querySelector('#column-right .profile-content')?.getClientRects().length,
          peers:[...new Set([...document.querySelectorAll('.chat.active .topbar .person-avatar[data-peer-id]')].map(e=>e.dataset.peerId))],
          leave:[...document.querySelectorAll('#column-right .profile-action')].filter(e=>e.getClientRects().length && /^(leave|покинуть|выйти)$/i.test(e.textContent.replace(/[\uE000-\uF8FF]/g,'').trim())).length,
          join:[...document.querySelectorAll('#column-right .profile-action')].filter(e=>e.getClientRects().length && /join|вступить|подписаться|заявк/i.test(e.textContent)).length,
          roots:document.querySelectorAll('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover)').length,
          atEnd:document.querySelectorAll('.chat.active .bubbles.scrolled-down').length===1})''',handles)
        if not proof.pop('handlesConnected'):raise BrowserAccessError('Identity panel replaced')
        names=proof.pop('usernames');link=proof.pop('publicLink')
        self.state['identity_evidence']={'username_rows':len(names),'expected_username_rows':names.count(pilot.ACCOUNT),
          'contradictory_username':any(name and name!=pilot.ACCOUNT for name in names),
          'public_source_present':bool(link),'public_source_matches':link=='t.me/'+self.name.lower()}
        if any(name and name!=pilot.ACCOUNT for name in names):
            self.state['failure_class']='manual_attention';raise BrowserAccessError('Account changed')
        if names!=[pilot.ACCOUNT]:raise BrowserAccessError('Own username evidence incomplete')
        if not link:raise BrowserAccessError('Public source evidence incomplete')
        if link!='t.me/'+self.name.lower():
            self.state['failure_class']='manual_attention';raise BrowserAccessError('Public source changed')
        self.state['guard']=proof
        self.state['guard_stage']='layout_peer_membership'
        if not proof['online'] or proof['connecting']:
            self.state['failure_class']='network_unavailable'
            raise ConnectionError('Web connection not ready')
        if not (proof['own']==1 and proof['ownVisible'] and proof['profileVisible'] and proof['peers']==[self.dom] and proof['leave']==1 and proof['join']==0 and proof['roots']==1 and proof['atEnd']):
            self.state['failure_class']='manual_attention' if (proof['peers'] and proof['peers']!=[self.dom]) or proof['join']>0 else 'identity_check_pending'
            raise BrowserAccessError('Account/source/membership guard failed')
        self.state['failure_class']=None;self.state['guard_stage']='verified';self.last_live=monitor.iso(monitor.now())
        return BrowserIdentity(pilot.ACCOUNT,self.name,self.peer,True,self.page.url)

    async def attach(self):
        self.stage='new_page'
        self.last_web=None;self.last_live=None
        if self.page and not self.page.is_closed():await self.page.close()
        self.page=await self.context.new_page();self.state={};self.generation+=1
        if self.definition.get('adapter','telegram_web_k')!='telegram_web_k':raise ValueError('Unsupported source adapter')
        params=source_config.parameters(self.peer,json.loads(self.definition.get('adapter_parameters') or '{}'))
        self.stage='initial_navigation';await self.page.goto('https://web.telegram.org/k/',wait_until='domcontentloaded',timeout=params.get('navigation_timeout_ms',25000))
        await self.page.locator('#column-left .sidebar-tools-button').wait_for(state='visible',timeout=20000)
        self.stage='verify_initial_account';await pilot.own_settings(self.page,self.state)
        self.stage='native_group_navigation';await group_navigation.open_group(self.page,self.name,{})
        await self.page.locator('.chat.active .topbar .person-avatar[data-peer-id="'+self.dom+'"]').first.wait_for(state='attached',timeout=12000)
        # Native latest-message control, not a fabricated old-message mutation.
        down=self.page.locator('.chat.active .bubbles-go-down:not(.hide):visible')
        self.stage='latest_messages'
        if await down.count()==1:await down.click(timeout=4000)
        await self.profile()
        intake=BrowserIntake(self.db,self.cfg,pilot.ACCOUNT,self.name,self.peer)
        self.capture=BrowserCapture(self.page,intake,DomContract(pilot.ROOT_SELECTOR,self.dom,True),self.verify)
        self.stage='capture_arm';await self.capture.arm();self.baseline=intake.baseline
        self.stage='attached'
        with self.journal:self.journal.execute("update sources set access='joined',checked_at=? where username=?",(monitor.iso(monitor.now()),self.name))
        self.phase='reading';self.error=None;self.record()

    async def tick(self):
        """Keep the observer for a bounded pre-intake network pause only."""
        pause=None
        while True:
            try:
                ids=await self._tick_once()
                if pause is not None:
                    pause.update(result='resumed',elapsed_s=round(time.monotonic()-began,3),
                                 baseline_unchanged=self.baseline==baseline,generation_unchanged=self.generation==generation)
                    self.phase='reading';self.error=None;self.record()
                    from local_chrome import lifecycle
                    lifecycle('source_network_pause_resumed',source=self.name,**pause)
                return ids
            except ConnectionError:
                if not self.capture or not self.capture.active or self.state.get('failure_class')!='network_unavailable':raise
                if pause is None:
                    began=time.monotonic();baseline=self.baseline;generation=self.generation
                    pause={'started_at':monitor.iso(monitor.now()),'rechecks':0,'result':'waiting'}
                    self.network_pause=pause
                    registry.gap(self.journal,self.name,'network_pause_before_intake')
                    from local_chrome import lifecycle
                    lifecycle('source_network_pause_started',source=self.name,**pause)
                self.phase='recovering';self.error='ConnectionError';self.last_live=None;self.last_web=None;self.record()
                if pause['rechecks']>=TRANSIENT_RECHECKS:
                    pause.update(result='expired',elapsed_s=round(time.monotonic()-began,3))
                    from local_chrome import lifecycle
                    lifecycle('source_network_pause_expired',source=self.name,**pause)
                    raise
                pause['rechecks']+=1
                try:await asyncio.wait_for(self.stop_event.wait(),timeout=TRANSIENT_DELAY)
                except asyncio.TimeoutError:pass
                if self.stop_event.is_set() or automation.STOP.exists():return []

    async def _tick_once(self):
        if not self.capture:raise ConnectionError('Capture not attached')
        if self.page is None or self.page.is_closed() or not pilot.origin_ok(self.page):
            await self.capture.stop()
            raise BrowserAccessError('Page unavailable or origin changed')
        # A failed public-origin check must not consume the message queue.
        if self.last_web is None or automation.age(self.last_web)>=30:
            ok=await self.page.evaluate('''async()=>{const c=new AbortController(),t=setTimeout(()=>c.abort(),8000);
              try{return (await fetch('/k/?monitor_health='+Date.now(),{method:'HEAD',credentials:'omit',cache:'no-store',signal:c.signal})).ok}
              catch{return false}finally{clearTimeout(t)}}''')
            if not ok:
                self.state['failure_class']='network_unavailable'
                raise ConnectionError('Public Web origin unavailable')
            self.last_web=monitor.iso(monitor.now())
        ids=await self.capture.drain()
        registry.record_changes(self.journal,self.name,self.capture.last_changes)
        if ids:
            received=self.db.execute('select max(received_at) from events where source_id=?',('client:'+str(self.peer),)).fetchone()[0]
            with self.journal:self.journal.execute('update runtime set last_received=? where username=?',(received,self.name))
        self.record()
        return ids

    async def run(self):
        stable=None
        registry.gap(self.journal,self.name,'runtime_start_no_backfill')
        try:
            while not self.stop_event.is_set() and not automation.STOP.exists():
                try:
                    if not self.capture or not self.capture.active:
                        self.phase='recovering';self.record()
                        if self.attach_gate is None:await self.attach()
                        else:
                            async with self.attach_gate:await self.attach()
                        stable=time.monotonic()
                    await self.tick()
                    if stable is not None and time.monotonic()-stable>=300:self.attempts=0;self.cooldown_cycles=0
                    await asyncio.sleep(2)
                except asyncio.CancelledError:raise
                except Exception as error:
                    self.last_failure={'at':monitor.iso(monitor.now()),'error_type':type(error).__name__,
                                       'stage':self.stage,'failure_class':self.state.get('failure_class'),
                                       'guard_stage':self.state.get('guard_stage'),'guard':self.state.get('guard'),
                                       'identity_evidence':self.state.get('identity_evidence')}
                    from local_chrome import lifecycle
                    lifecycle('source_failure',source=self.name,**{k:v for k,v in self.last_failure.items() if k!='at'})
                    if self.capture:await self.capture.stop()
                    self.error=type(error).__name__;self.phase='recovering';self.last_live=None;self.record()
                    registry.gap(self.journal,self.name,self.error)
                    # Access ambiguity never triggers a blind re-navigation/account change.
                    if isinstance(error,BrowserAccessError) and self.state.get('failure_class')=='manual_attention':
                        self.phase='manual_attention';self.record();break
                    if isinstance(error,BrowserAccessError) and self.page and not self.page.is_closed():
                        # A reload/crash disconnects verified handles; it is safe to
                        # attach afresh, but a connected wrong identity requires attention.
                        connected=True
                        for key in ('own','source_link'):
                            try:connected=connected and bool(self.state.get(key)) and await self.state[key].evaluate('e=>e.isConnected')
                            except Exception:connected=False
                        if connected and self.state.get('failure_class')=='manual_attention':
                            self.phase='manual_attention';self.record();break
                    if self.attempts>=len(automation.DELAYS):
                        self.phase='retry_limit_reached';self.record()
                        if self.cooldown_cycles>=1:break
                        self.cooldown_cycles+=1
                        # One delayed extra budget for transient failures, never for identity ambiguity.
                        try:await asyncio.wait_for(self.stop_event.wait(),timeout=300)
                        except asyncio.TimeoutError:pass
                        if self.stop_event.is_set() or automation.STOP.exists():break
                        self.attempts=0;self.phase='recovering';self.record();continue
                    delay=automation.DELAYS[self.attempts];self.attempts+=1;self.record()
                    await asyncio.sleep(delay)
        finally:
            if self.capture:await self.capture.stop()
            if self.stop_event.is_set() or automation.STOP.exists():self.phase='context_closed' if self.context_lost else 'operator_stopped';self.record()

    def metadata(self):
        return {'username':self.name,'peer_id':self.peer,'phase':self.phase,'error_type':self.error,'attempts':self.attempts,
                'last_live_check':self.last_live,'last_web_transport':self.last_web,'baseline':self.baseline,'generation':self.generation,
                'capture_running':bool(self.capture and self.capture.active and self.phase=='reading'),'stage':self.stage,
                'last_failure':self.last_failure,'network_pause':getattr(self,'network_pause',None)}


class MultiReader:
    def __init__(self,context):
        self.context=context;self.stop_event=asyncio.Event();self.sessions=[];self.tasks=[]
        self.locks=contextlib.ExitStack();self.db=None;self.journal=None;self.started=False
        self.delivery_check='not_checked';self.last_callback=0;self.callback_delay=10
        self.attach_gate=asyncio.Lock()
        self.heartbeat_task=None
        browser=getattr(context,'browser',None) if context is not None else None
        self.context_closed=bool(browser and not browser.is_connected())
        if context is not None:context.on('close',self.on_context_closed)

    def on_context_closed(self,*args):
        self.context_closed=True;self.stop_event.set()
        for session in self.sessions:
            session.context_lost=True
            if self.journal:registry.gap(self.journal,session.name,'context_closed',session.last_live)

    def require_context(self):
        if self.context_closed:raise ConnectionError('Browser context closed')
        if automation.STOP.exists():raise InterruptedError('Reader explicitly stopped')

    async def start(self):
        self.require_context()
        self.journal=registry.connect();definitions=registry.enabled(self.journal)
        if not definitions:raise ValueError('No enabled sources')
        cfg=automation.config();cfg=replace(cfg,client_sources=tuple(r['username'] for r in definitions),client_peer_ids=tuple(r['peer_id'] for r in definitions));cfg.live_ready()
        if __import__('os').getenv('MONITOR_CLIENT_ACCOUNT','').lower().lstrip('@')!=pilot.ACCOUNT:raise ValueError('Account binding differs')
        api=monitor.Telegram(cfg.token)
        me=await asyncio.to_thread(api.call,'getMe',{},timeout=15)
        if me.get('username')!='uffweb_bot':raise ValueError('Notification bot differs')
        if (await asyncio.to_thread(api.call,'getWebhookInfo',{},timeout=10)).get('url'):raise ValueError('Webhook already configured')
        self.locks.enter_context(monitor.single_instance(cfg.db.with_suffix('.reader.lock')))
        self.locks.enter_context(monitor.single_instance(cfg.db.with_suffix('.lock')))
        self.cfg=cfg;self.db=monitor.connect(cfg.db);monitor.recover_interrupted(self.db)
        self.journal_path=self.journal.execute('PRAGMA database_list').fetchone()[2]
        import source_notices
        source_notices.recover_interrupted(self.journal)
        for definition in definitions:
            self.require_context()
            session=SourceSession(definition,self.context,self.db,cfg,self.journal,self.stop_event,self.attach_gate)
            self.sessions.append(session);self.tasks.append(asyncio.create_task(session.run()))
            # Avoid simultaneous menu/navigation bursts during staged attachment.
            deadline=time.monotonic()+65
            while session.phase in ('starting','recovering') and not session.capture and time.monotonic()<deadline:
                self.require_context()
                pilot.publish(**self.metadata())
                await asyncio.sleep(.25)
        self.started=True
        self.tasks.append(asyncio.create_task(self.background()))
        self.heartbeat_task=asyncio.create_task(self.heartbeat())
        return self.metadata()

    async def add_pending(self):
        """Attach exactly one enabled definition without replacing existing sessions."""
        if not self.started or self.stop_event.is_set():raise ValueError('Reader is not running')
        if not self.metadata()['all_sources_reading']:raise ValueError('Existing source needs recovery first')
        definitions=registry.enabled(self.journal)
        present={s.name for s in self.sessions}
        pending=[d for d in definitions if d['username'] not in present]
        if len(pending)!=1:raise ValueError('Enable exactly one additional source')
        cfg=replace(self.cfg,client_sources=tuple(d['username'] for d in definitions),
                    client_peer_ids=tuple(d['peer_id'] for d in definitions))
        cfg.live_ready()
        session=SourceSession(pending[0],self.context,self.db,cfg,self.journal,self.stop_event,getattr(self,'attach_gate',None))
        self.cfg=cfg;self.sessions.append(session)
        # close() relies on the sole background/delivery task remaining last.
        self.tasks.insert(len(self.tasks)-1,asyncio.create_task(session.run()))
        registry.enroll_active(self.journal,session.name)
        return {'added_source':session.name,**self.metadata()}

    def metadata(self):
        rows=[s.metadata() for s in self.sessions]
        good=[r for r in rows if not self.context_closed and r['capture_running'] and automation.age(r['last_live_check']) is not None and automation.age(r['last_live_check'])<=45
              and automation.age(r['last_web_transport']) is not None and automation.age(r['last_web_transport'])<=60]
        return {'multi_source':True,'account_username':'@'+pilot.ACCOUNT,'source_states':rows,'phase':'reading' if good else 'sources_waiting',
                'capture_running':bool(good),'account_verified':bool(good),'source_verified':bool(good),
                'last_live_check':max((r['last_live_check'] for r in good),default=None),
                'last_web_transport':max((r['last_web_transport'] for r in rows if r['last_web_transport']),default=None),
                'delivery_check':self.delivery_check,'delivery_running':self.delivery_check=='ok',
                'all_sources_reading':bool(rows) and len(good)==len(rows),'enabled_sources':len(rows),
                'runtime_alive':not self.stop_event.is_set() and not self.context_closed,'context_closed':self.context_closed,
                'uses_tinyfish':False,'backfill':False,'error_type':None}

    def delivery_cycle(self):
        # A separate connection confined to this thread. Only this worker owns callbacks.
        with contextlib.closing(monitor.connect(self.cfg.db)) as db:
            marks=','.join('?' for _ in self.sessions)
            scope=tuple(r[0] for r in db.execute('''select distinct c.id from candidates c join events e on e.id=c.event_id
              join deliveries d on d.candidate_id=c.id where d.state='pending' and e.edit_version like 'browser:%'
              and e.source_id in ('''+marks+')',tuple('client:'+str(s.peer) for s in self.sessions)))
            if scope:monitor.deliver(db,self.cfg,monitor.Telegram(self.cfg.token),monitor.now(),scope)
            monitor.deliver_revisions(db,self.cfg,monitor.Telegram(self.cfg.token),monitor.now(),
                                     tuple('client:'+str(s.peer) for s in self.sessions))
            import source_notices
            with contextlib.closing(registry.connect(getattr(self,'journal_path',registry.PATH))) as notices:
                source_notices.flush(notices,self.cfg,monitor.Telegram(self.cfg.token),monitor.now())
            if time.monotonic()-self.last_callback>=self.callback_delay:
                self.last_callback=time.monotonic()
                try:
                    pilot.poll_owner_callbacks(db,self.cfg,monitor.Telegram(self.cfg.token))
                    self.delivery_check='ok';self.callback_delay=10
                except Exception as error:self.delivery_check=type(error).__name__;self.callback_delay=60

    async def heartbeat(self,interval=2,sample_interval=10):
        """Source status and observation clocks never wait for notification I/O."""
        last_sample=0
        while not self.stop_event.is_set():
            try:
                if time.monotonic()-last_sample>=sample_interval:
                    for session in self.sessions:
                        healthy=bool(session.capture and session.capture.active and session.last_live and automation.age(session.last_live)<=15
                                     and session.last_web and automation.age(session.last_web)<=60)
                        registry.sample(self.journal,session.name,healthy,session.last_live)
                    last_sample=time.monotonic()
                pilot.publish(**self.metadata())
            except asyncio.CancelledError:raise
            except Exception as error:pilot.publish(source_heartbeat_error=type(error).__name__,**self.metadata())
            await asyncio.sleep(interval)

    async def background(self):
        last_report=0;last_resources=0
        while not self.stop_event.is_set():
            try:
                await asyncio.to_thread(self.delivery_cycle)
                if self.stop_event.is_set():break
                if time.monotonic()-last_report>=60:
                    registry.write_report(registry.report(self.journal,self.db));last_report=time.monotonic()
                if time.monotonic()-last_resources>=60:
                    import resource_probe
                    resources=await asyncio.to_thread(resource_probe.measure,__import__('os').getpid())
                    resources['enabled_sources']=len(self.sessions)
                    automation.write_json(automation.STATE/'resource-sample.json',resources)
                    last_resources=time.monotonic()
                pilot.publish(**self.metadata())
            except asyncio.CancelledError:raise
            except Exception as error:pilot.publish(multi_source_error=type(error).__name__,**self.metadata())
            await asyncio.sleep(2)

    async def close(self):
        self.stop_event.set()
        heartbeat=getattr(self,'heartbeat_task',None)
        if heartbeat:
            heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True);self.heartbeat_task=None
        if self.context is not None:self.context.remove_listener('close',self.on_context_closed)
        # Let the sole delivery worker finish its bounded side effect before releasing locks.
        if self.tasks:
            for task in self.tasks[:-1] if self.started else self.tasks:task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)
        for session in self.sessions:
            if session.page and not session.page.is_closed():
                with contextlib.suppress(Exception):await session.page.close()
        if self.db:self.db.close();self.db=None
        if self.journal:self.journal.close();self.journal=None
        self.locks.close()


async def activate(page,context,state):
    if state.get('multi_reader'):await state['multi_reader'].close()
    if state.get('task') and not state['task'].done():await pilot.stop(state)
    reader=MultiReader(context);state['multi_reader']=reader
    try:return await reader.start()
    except Exception:
        await reader.close();state.pop('multi_reader',None);raise
