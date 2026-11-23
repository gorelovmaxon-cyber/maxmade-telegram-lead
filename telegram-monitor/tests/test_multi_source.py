"""Isolated real Chrome: two sources, failure isolation, binding and replay safety."""
import asyncio
import contextlib
import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor
import source_registry as registry
from multi_source import SourceSession
from browser_capture import BrowserCapture, DomContract
from browser_ingest import BrowserAccessError, BrowserIntake


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=registry.connect(self.root/'sources.sqlite3');self.business=monitor.connect(self.root/'business.sqlite3')
        registry.initialize(self.db)
    def tearDown(self):self.db.close();self.business.close();self.tmp.cleanup()

    def test_unknown_rules_and_access_cannot_enable_source_or_advance_stage(self):
        with self.assertRaises(ValueError):registry.enabled(self.db)
        with self.db:
            self.db.execute("update sources set access='joined' where username='top_ads_freelance'")
        self.assertEqual(len(registry.enabled(self.db)),1)
        with self.db:
            self.db.execute("update sources set enabled=1,access='joined',peer_id=-1000000000123,type='group' where username='freelead'")
        with self.assertRaises(ValueError):registry.enabled(self.db)
        with self.db:
            self.db.execute("update sources set rules_state='reviewed',read_permission='local_client' where username='freelead'")
        self.assertEqual(len(registry.enabled(self.db)),2)
        with self.db:
            self.db.execute("update sources set enabled=1,access='joined',peer_id=-1000000000456,type='group',rules_state='reviewed',read_permission='local_client' where username='biznes_chat'")
        with self.assertRaises(ValueError):registry.enabled(self.db)

    def test_report_credits_only_distinct_recent_checks_and_never_infers_outreach_from_taken(self):
        start=monitor.now()-dt.timedelta(seconds=300)
        stamps=[monitor.iso(start+dt.timedelta(seconds=n)) for n in (0,10,20,150,160,170)]
        for i,stamp in enumerate(stamps):registry.sample(self.db,'top_ads_freelance',True,stamps[i] if i!=2 else stamps[1],stamp)
        cfg=monitor.Config(self.root/'business.sqlite3')
        monitor.ingest(self.business,cfg,'client:-1001513982592','1','Нужен сайт',start,start,edit_version='browser:test')
        with self.business:self.business.execute("update candidates set owner_status='taken'")
        row=next(r for r in registry.report(self.db,self.business)['sources'] if r['username']=='top_ads_freelance')
        self.assertEqual(row['observed_seconds'],30)
        self.assertEqual(row['taken'],1);self.assertEqual(row['outreach_confirmed'],'unknown')
        self.assertFalse(row['observation_24h_complete'])

    def test_delivery_delay_is_source_scoped_and_missing_notice_stays_unknown(self):
        start=monitor.now()-dt.timedelta(seconds=60)
        cfg=monitor.Config(self.root/'business.sqlite3',owner_id=42,token='fixture',quiet_start=0,quiet_end=0)
        ident=monitor.ingest(self.business,cfg,'client:-1001513982592','9','Нужен лендинг для компании, ищу исполнителя.',start,start,edit_version='browser:test')
        with self.business:
            changed=self.business.execute("update deliveries set state='sent',sent_at=?,message_id=77 where candidate_id=?",
                (monitor.iso(start+dt.timedelta(seconds=12)),ident)).rowcount
        self.assertEqual(changed,1)
        rows={r['username']:r for r in registry.report(self.db,self.business)['sources']}
        self.assertEqual(rows['top_ads_freelance']['notification_delay_s'],{'count':1,'min':12.0,'max':12.0})
        self.assertEqual(rows['freelead']['notification_delay_s'],{'count':0,'min':None,'max':None})


@unittest.skipUnless(importlib.util.find_spec('playwright'),'Install requirements-browser.txt')
class MultiSourceChromeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        # The real state folder may hold an operator-stop marker from a running
        # monitor. tick() honours it and returns no ids, so an unpatched STOP
        # turns these tests into a check of the host machine, not of the code.
        self._stop_marker=patch('automation.STOP',root/'operator-stop')
        self._stop_marker.start()
        self.db=monitor.connect(root/'fixture.sqlite3');self.journal=registry.connect(root/'sources.sqlite3')
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        self.ctx=await self.browser.new_context(service_workers='block')
        self.requests=[]
        async def route(req):
            self.requests.append(req.request.url);await req.fulfill(status=200,content_type='text/html',body='<html></html>')
        await self.ctx.route('**/*',route)
        self.cfg=monitor.Config(root/'fixture.sqlite3',client_sources=('source_one','source_two'),client_peer_ids=(-1000000000123,-1000000000456),quiet_start=0,quiet_end=0)
        self.stop=asyncio.Event();self.sessions=[]
        for name,peer in zip(self.cfg.client_sources,self.cfg.client_peer_ids):
            s=SourceSession({'username':name,'peer_id':peer},self.ctx,self.db,self.cfg,self.journal,self.stop)
            self.sessions.append(s);await self.attach(s)

    async def attach(self,s):
        import chrome_pilot
        s.page=await self.ctx.new_page();await s.page.goto('https://web.telegram.org/k/')
        await s.page.set_content('''<style>.bubble,.message{display:block;min-height:20px}</style>
          <div class="settings-container active"><div class="profile-content is-me">Account</div>
          <div class="row"><div class="row-title">maxmade_pro</div><div class="row-subtitle">Username</div></div></div>
          <div id="column-right"><div class="profile-content"><button class="profile-action">\ue9c6leave</button></div>
          <div class="row-title">t.me/'''+s.name+'''</div></div>
          <div class="chat active"><div class="topbar"><span class="person-avatar" data-peer-id="'''+s.dom+'''"></span></div>
          <div class="bubbles scrolled-down"><div class="bubbles-inner"><div class="bubble" data-mid="100" data-peer-id="'''+s.dom+'''" data-timestamp="1000000000"><div class="message">Old synthetic row</div></div></div></div></div>''')
        s.state={};await chrome_pilot.own_settings(s.page,s.state);await s.profile()
        s.capture=BrowserCapture(s.page,BrowserIntake(self.db,s.cfg,'maxmade_pro',s.name,s.peer),DomContract(chrome_pilot.ROOT_SELECTOR,s.dom,True),s.verify)
        await s.capture.arm();s.phase='reading';s.record()

    async def add(self,s,mid='101'):
        # Future within30s is permitted by intake; avoid timestamp rounding before baseline.
        await s.page.evaluate('''({peer,mid})=>{const b=document.createElement('div');b.className='bubble';
          b.dataset.mid=mid;b.dataset.peerId=peer;b.dataset.timestamp=String(Math.ceil(Date.now()/1000));
          b.innerHTML='<div class="message">Нужен сайт для компании, ищу исполнителя.</div>';
          document.querySelector('.bubbles-inner').append(b)}''',{'peer':s.dom,'mid':mid})
        await s.page.wait_for_function('()=>window.__maxmadeReadOnlyObserver.status().queued>0')

    async def asyncTearDown(self):
        self.stop.set()
        for s in self.sessions:
            if s.capture:await s.capture.stop()
        await self.ctx.close();await self.browser.close();await self.pw.stop()
        self.db.close();self.journal.close();self.tmp.cleanup()
        self._stop_marker.stop()

    async def test_same_message_number_in_two_sources_does_not_cross_bind_or_duplicate_on_replay(self):
        for s in self.sessions:await self.add(s);self.assertEqual(len(await s.tick()),1)
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],2)
        for s in self.sessions:self.assertEqual(await s.tick(),[])
        self.assertEqual({r[0] for r in self.db.execute('select link from events')},{'https://t.me/source_one/101','https://t.me/source_two/101'})

    async def test_closing_one_source_does_not_stop_other_and_restart_baselines_history(self):
        first,second=self.sessions
        await first.page.close()
        with self.assertRaises(BrowserAccessError):await first.tick()
        await self.add(second);self.assertEqual(len(await second.tick()),1)
        await self.attach(first);self.assertEqual(await first.tick(),[])
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],1)

    async def test_wrong_numeric_peer_or_account_blocks_intake_without_stopping_other_page(self):
        first,second=self.sessions
        await first.page.locator('.topbar [data-peer-id]').evaluate("e=>e.dataset.peerId='-999'")
        with self.assertRaises(BrowserAccessError):await first.tick()
        await self.add(second);self.assertEqual(len(await second.tick()),1)
        await second.page.locator('.settings-container .row-title').evaluate("e=>e.textContent='different_account'")
        with self.assertRaises(BrowserAccessError):await second.tick()

    async def test_typing_avatar_does_not_change_verified_conversation(self):
        first=self.sessions[0]
        await first.page.locator('.topbar').evaluate('''e=>{
            const typing=document.createElement('span');typing.dataset.peerId='12345';e.append(typing)}''')
        self.assertEqual((await first.verify()).peer_id,first.peer)
        await self.add(first);self.assertEqual(len(await first.tick()),1)

    async def test_identity_snapshot_rejects_connected_wrong_link_and_identical_replacement(self):
        first,second=self.sessions
        await first.page.locator('#column-right .row-title').evaluate("e=>e.textContent='t.me/source_two'")
        with self.assertRaises(BrowserAccessError):await first.verify()
        self.assertEqual(first.state['failure_class'],'manual_attention')
        await second.page.locator('#column-right').evaluate('e=>e.replaceWith(e.cloneNode(true))')
        with self.assertRaises(BrowserAccessError):await second.verify()
        self.assertEqual(second.state['failure_class'],'identity_check_pending')

    async def test_identity_proof_uses_one_browser_snapshot_without_exposing_handle_metadata(self):
        from unittest.mock import patch
        first=self.sessions[0]
        with patch.object(first.page,'evaluate',wraps=first.page.evaluate) as evaluate:
            proof=await first.verify()
        self.assertEqual(evaluate.call_count,1)
        self.assertEqual(proof.source_username,first.name)
        self.assertNotIn('publicLink',first.state['guard'])
        self.assertNotIn('usernames',first.state['guard'])

    async def test_profile_reflow_uses_native_live_end_control_before_rearming(self):
        from unittest.mock import patch
        import chrome_pilot
        first=self.sessions[0]
        await first.capture.stop()
        original=chrome_pilot.own_settings
        async def reflow(page,state):
            result=await original(page,state)
            await page.evaluate('''()=>{
              document.querySelector('.bubbles').classList.remove('scrolled-down');
              const button=document.createElement('button');button.className='bubbles-go-down';
              button.textContent='Latest';button.onclick=()=>{
                document.querySelector('.bubbles').classList.add('scrolled-down');button.remove()};
              document.querySelector('.chat.active').append(button)}''')
            return result
        with patch('chrome_pilot.own_settings',reflow):await first.profile()
        self.assertTrue(await first.page.locator('.bubbles.scrolled-down').count())
        await first.capture.arm()
        self.assertEqual(await first.tick(),[])
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)

    async def test_persistent_network_pause_is_bounded_then_full_recovery_baselines_history(self):
        await self.ctx.set_offline(True)
        with patch('multi_source.TRANSIENT_DELAY',0),patch('local_chrome.lifecycle'):
            for s in self.sessions:
                with self.assertRaises(ConnectionError):await s.tick()
                self.assertEqual(s.network_pause['rechecks'],3)
                self.assertEqual(s.network_pause['result'],'expired')
                await s.capture.stop() # run() performs this before full reconnect.
        await self.ctx.set_offline(False)
        for s in self.sessions:await self.attach(s);self.assertEqual(await s.tick(),[])
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)

    async def test_brief_network_pause_keeps_observer_while_other_source_reads(self):
        first,second=self.sessions;await self.add(first);await self.add(second)
        initial=first.capture.intake.started_at;baseline=first.capture.intake.baseline;capture=first.capture
        verify=first.capture.verify_live;calls=0;other_ids=[]
        async def transient():
            nonlocal calls
            calls+=1
            if calls==1:
                first.state['failure_class']='network_unavailable'
                other_ids.extend(await second.tick())
                raise ConnectionError('synthetic brief network')
            return await verify()
        first.capture.verify_live=transient
        with patch('multi_source.TRANSIENT_DELAY',0),patch('local_chrome.lifecycle'):
            self.assertEqual(len(await first.tick()),1)
        self.assertEqual(len(other_ids),1);self.assertIs(first.capture,capture)
        self.assertEqual(first.capture.intake.started_at,initial);self.assertEqual(first.capture.intake.baseline,baseline)
        self.assertEqual(first.network_pause['result'],'resumed');self.assertEqual(first.network_pause['rechecks'],1)
        self.assertEqual(await first.tick(),[]);self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],2)

    async def test_account_mismatch_during_pause_stops_before_any_intake(self):
        first,second=self.sessions;await self.add(first);await self.add(second)
        verify=first.capture.verify_live;calls=0
        async def changed():
            nonlocal calls
            calls+=1
            if calls==1:
                first.state['failure_class']='network_unavailable'
                await first.page.locator('.settings-container .row-title').evaluate("e=>e.textContent='foreign_account'")
                raise ConnectionError('synthetic brief network')
            return await verify()
        first.capture.verify_live=changed
        with patch('multi_source.TRANSIENT_DELAY',0),patch('local_chrome.lifecycle'):
            with self.assertRaises(BrowserAccessError):await first.tick()
        self.assertFalse(first.capture.active);self.assertEqual(first.state['failure_class'],'manual_attention')
        self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        self.assertEqual(len(await second.tick()),1)

    async def test_five_sources_keep_identity_and_dedup_when_one_page_closes(self):
        from dataclasses import replace
        names=self.cfg.client_sources+('source_three','source_four','source_five')
        peers=self.cfg.client_peer_ids+(-1000000000789,-1000000000999,-1000000001111)
        cfg=replace(self.cfg,client_sources=names,client_peer_ids=peers)
        for name,peer in zip(names[2:],peers[2:]):
            session=SourceSession({'username':name,'peer_id':peer},self.ctx,self.db,cfg,self.journal,self.stop)
            self.sessions.append(session);await self.attach(session)
        pages=[s.page for s in self.sessions]
        for s in self.sessions:
            await self.add(s);self.assertEqual(len(await s.tick()),1)
            self.assertEqual([r['change'] for r in s.capture.last_changes],['first_ingest'])
        for s in self.sessions:
            self.assertEqual(await s.tick(),[]);self.assertEqual(s.capture.last_changes,[])
        self.assertEqual(self.db.execute('select count(distinct source_id) from events').fetchone()[0],5)
        await self.sessions[-1].page.close()
        for s in self.sessions[:-1]:
            await self.add(s,'102');self.assertEqual(len(await s.tick()),1)
        await self.attach(self.sessions[-1])
        for index,s in enumerate(self.sessions[:-1]):self.assertIs(s.page,pages[index])
        self.assertEqual(await self.sessions[-1].tick(),[])
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],9)
