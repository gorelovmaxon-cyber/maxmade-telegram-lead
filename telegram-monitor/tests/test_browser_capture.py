import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path

import monitor
from browser_capture import BrowserCapture, DomContract
from browser_ingest import BrowserAccessError, BrowserIdentity, BrowserIntake

PLAYWRIGHT = importlib.util.find_spec('playwright') is not None
FIXTURE = '''<!doctype html><html><head><style>.bubble,.message{display:block;min-height:20px}body{margin:0}</style></head><body>
<span id="account">expected_user</span><span id="source" data-username="approved_group" data-peer="-1000000000123" data-member="true"></span>
<section id="messages"><div class="bubble" data-mid="4294967396" data-peer-id="-123" data-timestamp="1000000000"><div class="message">Historical fixture: never ingest</div></div></section>
</body></html>'''


@unittest.skipUnless(PLAYWRIGHT, 'Install requirements-browser.txt to execute local browser fixture tests')
class BrowserCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.temp=tempfile.TemporaryDirectory();self.cfg=monitor.Config(Path(self.temp.name)/'fixture.sqlite3',owner_id=42,client_sources=('approved_group',),client_peer_ids=(-1000000000123,),quiet_start=0,quiet_end=0)
        self.db=monitor.connect(self.cfg.db)
        self.intake=BrowserIntake(self.db,self.cfg,'expected_user','approved_group',-1000000000123)
        self.playwright=await async_playwright().start()
        self.browser=await self.playwright.chromium.launch(channel='chrome',headless=True)
        self.context=await self.browser.new_context(service_workers='block')
        # Entire synthetic page is served in-process before any HTTP access.
        # No Telegram login, user browser context, cookies or real messages exist.
        self.requests=[]
        async def route(request):
            self.requests.append(request.request.url)
            if request.request.url=='https://web.telegram.org/k/':await request.fulfill(status=200,content_type='text/html',body=FIXTURE)
            else:await request.abort()
        await self.context.route('**/*',route)
        self.page=await self.context.new_page();await self.page.goto('https://web.telegram.org/k/#local-fixture')
        async def verify():
            account=await self.page.locator('#account').inner_text()
            source=await self.page.locator('#source').get_attribute('data-username')
            peer=await self.page.locator('#source').get_attribute('data-peer')
            member=await self.page.locator('#source').get_attribute('data-member')
            return BrowserIdentity(account,source,int(peer),member=='true',self.page.url)
        self.capture=BrowserCapture(self.page,self.intake,DomContract('#messages','-123',True),verify)

    async def asyncTearDown(self):
        await self.capture.stop();await self.context.close();await self.browser.close();await self.playwright.stop()
        self.db.close();self.temp.cleanup()

    async def add(self,mid=101,text='Нужен лендинг',timestamp=None,peer='-123'):
        await self.page.evaluate('''v=>{
          const e=document.createElement('div');e.className='bubble';e.dataset.mid=String(4294967296+v.mid);e.dataset.peerId=v.peer;
          e.dataset.timestamp=String(v.timestamp || Math.ceil(Date.now()/1000));
          const m=document.createElement('div');m.className='message';m.textContent=v.text;e.append(m);document.querySelector('#messages').append(e);
        }''',dict(mid=mid,text=text,timestamp=timestamp,peer=peer))

    async def settle(self):
        await self.page.wait_for_function('() => {const s=window.__maxmadeReadOnlyObserver.status();return s.queued>0 || s.failure;}')

    async def test_real_dom_mutation_to_filter_to_fake_notification(self):
        await self.capture.arm();self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)
        await self.add();await self.settle();ids=await self.capture.drain();self.assertEqual(len(ids),1)
        row=self.db.execute('select message_id,link,body from events').fetchone()
        self.assertEqual(row['message_id'],'101');self.assertEqual(row['link'],'https://t.me/approved_group/101')
        self.assertNotIn('Historical',row['body'])
        class FakeSink:
            def __init__(s):s.calls=[]
            def call(s,method,payload,timeout=20):s.calls.append((method,payload));return {'message_id':91,'date':int(monitor.now().timestamp())}
        sink=FakeSink();self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),1)
        self.assertEqual(sink.calls[0][1]['chat_id'],42)
        await self.page.evaluate("() => document.querySelector('[data-mid=\"4294967397\"] .message').textContent='Нужен лендинг, не в штат'")
        await self.settle();await self.capture.drain();self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),0)
        self.assertEqual(len(sink.calls),1);self.assertEqual(self.requests,['https://web.telegram.org/k/'])

    async def test_changed_identity_disarms_before_reading_queue(self):
        await self.capture.arm();await self.add();await self.settle()
        await self.page.locator('#account').evaluate("e=>e.textContent='different_user'")
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        self.assertFalse(self.capture.active);self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)

    async def test_pre_identity_network_pause_keeps_queue_and_exactly_one_delivery_after_verified_recovery(self):
        await self.capture.arm();initial=self.intake.started_at;baseline=self.intake.baseline
        await self.add();await self.settle();verify=self.capture.verify_live
        async def offline():raise ConnectionError('synthetic network before intake')
        self.capture.verify_live=offline
        with self.assertRaises(ConnectionError):await self.capture.drain()
        self.assertTrue(self.capture.active);self.assertEqual(self.intake.started_at,initial);self.assertEqual(self.intake.baseline,baseline)
        self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        self.capture.verify_live=verify;self.assertEqual(len(await self.capture.drain()),1)
        from test_filter_policy import Sink
        sink=Sink();self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),1)
        self.assertEqual(await self.capture.drain(),[]);self.assertEqual(monitor.deliver(self.db,self.cfg,sink,monitor.now()),0)

    async def test_post_drain_network_failure_disarms_and_does_not_accept_unconfirmed_batch(self):
        await self.capture.arm();await self.add();await self.settle();verify=self.capture.verify_live;calls=0
        async def interrupted():
            nonlocal calls
            calls+=1
            if calls==2:raise ConnectionError('synthetic post-drain loss')
            return await verify()
        self.capture.verify_live=interrupted
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        self.assertFalse(self.capture.active);self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    async def test_old_history_reinsert_and_foreign_peer_not_imported(self):
        await self.capture.arm();await self.add(mid=90,timestamp=1000000000);await self.add(mid=105,peer='-999');await self.add(mid=110)
        await self.settle();await self.capture.drain()
        self.assertEqual([r[0] for r in self.db.execute('select message_id from events')],['110'])

    async def test_root_replaced_disarms(self):
        await self.capture.arm();await self.page.locator('#messages').evaluate("e=>e.replaceWith(e.cloneNode(true))")
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        self.assertFalse(self.capture.active)

    async def test_queue_overflow_never_silently_loses_and_continues(self):
        await self.capture.arm()
        await self.page.evaluate('''()=>{for(let i=101;i<235;i++){
          const e=document.createElement('div');e.className='bubble';e.dataset.mid=String(4294967296+i);e.dataset.peerId='-123';e.dataset.timestamp=String(Math.ceil(Date.now()/1000));
          const m=document.createElement('div');m.className='message';m.textContent='Нужен лендинг';e.append(m);document.querySelector('#messages').append(e);
        }}''')
        await self.page.wait_for_function('()=>Boolean(window.__maxmadeReadOnlyObserver.status().failure)')
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        self.assertFalse(self.capture.active);self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)

    async def test_unverified_contract_never_installs_sensor(self):
        with self.assertRaises(BrowserAccessError):BrowserCapture(self.page,self.intake,DomContract('#messages','-123'),self.capture.verify_live)
        self.assertIsNone(await self.page.evaluate('()=>window.__maxmadeReadOnlyObserver || null'))

    async def test_temporary_and_ephemeral_ids_are_rejected(self):
        await self.capture.arm()
        await self.add(mid=101.1)
        await self.add(mid=4294967296+102)
        await self.add(mid=110)
        await self.settle();await self.capture.drain()
        self.assertEqual([r[0] for r in self.db.execute('select message_id from events')],['110'])

    async def test_identity_change_during_snapshot_discards_batch(self):
        await self.capture.arm();await self.add();await self.settle()
        original=self.capture.verify_live;calls=0
        async def racing_verifier():
            nonlocal calls
            calls+=1
            proof=await original()
            if calls==2:return BrowserIdentity('different_user',proof.source_username,proof.peer_id,True,proof.page_url)
            return proof
        self.capture.verify_live=racing_verifier
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)


if __name__=='__main__':unittest.main()
