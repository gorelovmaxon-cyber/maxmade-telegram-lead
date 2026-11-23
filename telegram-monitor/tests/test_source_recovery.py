import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import monitor
import source_registry as registry
import source_notices as notices
import automation
from types import SimpleNamespace
from multi_source import SourceSession
from browser_ingest import BrowserAccessError

class FakeSink:
    def __init__(self,error=None):self.calls=[];self.error=error
    def call(self,method,payload,**kwargs):
        self.calls.append((method,payload))
        if self.error:raise self.error
        return {'message_id':777}

class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=registry.connect(self.root/'source.sqlite')
        registry.initialize(self.db);self.cfg=monitor.Config(self.root/'business.sqlite',owner_id=42)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def record(self,phase):registry.record_runtime(self.db,'top_ads_freelance',phase,None,5,None,None)
    def test_repeated_polls_do_not_send_repeated_state_notices(self):
        for _ in range(4):self.record('retry_limit_reached')
        with self.db:self.db.execute('UPDATE source_incidents SET opened_at=?',(monitor.iso(monitor.now()-__import__('datetime').timedelta(minutes=6)),))
        sink=FakeSink();self.assertEqual(notices.flush(self.db,self.cfg,sink,monitor.now()),1)
        self.assertEqual(notices.flush(self.db,self.cfg,sink,monitor.now()),0)
        self.record('recovering');self.record('reading');self.record('reading')
        self.assertEqual(notices.flush(self.db,self.cfg,sink,monitor.now()),1)
        self.assertEqual(len(sink.calls),2)
    def test_unknown_delivery_and_interrupted_send_never_blindly_repeat(self):
        self.record('manual_attention');sink=FakeSink(monitor.UnknownDelivery())
        notices.flush(self.db,self.cfg,sink,monitor.now());notices.flush(self.db,self.cfg,sink,monitor.now())
        self.assertEqual(len(sink.calls),1)
        self.assertEqual(self.db.execute('SELECT state FROM source_notices').fetchone()[0],'uncertain')
        with self.db:self.db.execute("UPDATE source_notices SET state='sending'")
        notices.recover_interrupted(self.db)
        self.assertEqual(self.db.execute('SELECT state FROM source_notices').fetchone()[0],'uncertain')
    def test_disabled_source_never_notifies_and_unannounced_recovery_is_quiet(self):
        self.record('retry_limit_reached');self.record('recovering');self.record('reading')
        sink=FakeSink();self.assertEqual(notices.flush(self.db,self.cfg,sink,monitor.now()),0)
        self.record('manual_attention')
        with self.db:self.db.execute('UPDATE sources SET enabled=0')
        self.assertEqual(notices.flush(self.db,self.cfg,sink,monitor.now()),0)

    def test_retry_exhaustion_waits_five_minutes_but_owner_action_is_immediate(self):
        self.record('retry_limit_reached');sink=FakeSink();at=monitor.now()
        self.assertEqual(notices.flush(self.db,self.cfg,sink,at),0)
        self.assertEqual(notices.flush(self.db,self.cfg,sink,at+__import__('datetime').timedelta(minutes=6)),1)
        self.assertNotIn('Ошибка:',sink.calls[0][1]['text'])
        self.record('reading');notices.flush(self.db,self.cfg,sink,at+__import__('datetime').timedelta(minutes=6))
        self.record('manual_attention');self.assertEqual(notices.flush(self.db,self.cfg,sink,monitor.now()),1)

class RecoveryBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self,root,db):
        return SourceSession({'username':'top_ads_freelance','peer_id':-1000000000123},None,None,None,db,asyncio.Event())
    async def test_transient_retry_has_one_cooldown_then_stops(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'source.sqlite')) as db:
            registry.initialize(db);s=await self.fixture(Path(folder),db);attempts=[];waits=[]
            async def fail():attempts.append(1);raise ConnectionError('synthetic network')
            async def cooldown(awaitable,timeout):
                waits.append(timeout);awaitable.close();raise asyncio.TimeoutError()
            s.attach=fail
            with patch('automation.DELAYS',(0,)),patch('automation.STOP',Path(folder)/'stop'),patch('local_chrome.lifecycle'),patch('multi_source.asyncio.wait_for',cooldown):
                await s.run()
            self.assertEqual(len(attempts),4);self.assertEqual(waits,[300]);self.assertEqual(s.phase,'retry_limit_reached')
    async def test_wrong_account_never_uses_transient_cooldown(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'source.sqlite')) as db:
            registry.initialize(db);s=await self.fixture(Path(folder),db);attempts=[]
            async def fail():
                attempts.append(1);s.state['failure_class']='manual_attention';raise BrowserAccessError('synthetic wrong account')
            s.attach=fail
            with patch('automation.STOP',Path(folder)/'stop'),patch('local_chrome.lifecycle'):await s.run()
            self.assertEqual(s.phase,'manual_attention');self.assertEqual(len(attempts),1)
    async def test_stop_during_cooldown_cannot_restart_source(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'source.sqlite')) as db:
            registry.initialize(db);s=await self.fixture(Path(folder),db);attempts=[]
            async def fail():attempts.append(1);raise ConnectionError('synthetic network')
            async def stopped(awaitable,timeout):awaitable.close();s.stop_event.set()
            s.attach=fail
            with patch('automation.DELAYS',()),patch('automation.STOP',Path(folder)/'stop'),patch('local_chrome.lifecycle'),patch('multi_source.asyncio.wait_for',stopped):await s.run()
            self.assertEqual(len(attempts),1);self.assertEqual(s.phase,'operator_stopped')

    async def test_stop_during_preserved_network_pause_does_not_read_or_reattach(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'source.sqlite')) as db:
            s=await self.fixture(Path(folder),db);calls=[];s.capture=SimpleNamespace(active=True)
            async def fail():calls.append(1);s.state['failure_class']='network_unavailable';raise ConnectionError('synthetic')
            async def stopped(awaitable,timeout):awaitable.close();s.stop_event.set()
            s._tick_once=fail
            with patch('automation.STOP',Path(folder)/'stop'),patch('local_chrome.lifecycle'),patch('multi_source.asyncio.wait_for',stopped):
                self.assertEqual(await s.tick(),[])
            self.assertEqual(len(calls),1);self.assertTrue(s.capture.active);self.assertEqual(s.phase,'recovering')

    async def test_failed_public_origin_check_does_not_drain_message_queue(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'source.sqlite')) as db:
            s=await self.fixture(Path(folder),db);calls=[]
            async def health(script):return False
            async def drain():calls.append(1);return []
            s.page=SimpleNamespace(evaluate=health,is_closed=lambda:False,url='https://web.telegram.org/k/')
            s.capture=SimpleNamespace(active=True,drain=drain)
            with patch('multi_source.TRANSIENT_DELAY',0),patch('automation.STOP',Path(folder)/'stop'),patch('local_chrome.lifecycle'):
                with self.assertRaises(ConnectionError):await s.tick()
            self.assertEqual(calls,[]);self.assertEqual(s.network_pause['rechecks'],3)

    async def test_exhausted_preserved_pause_falls_back_to_automatic_attach(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'source.sqlite')) as db:
            s=await self.fixture(Path(folder),db);calls=[];attachments=[]
            async def stop():s.capture.active=False
            s.capture=SimpleNamespace(active=True,stop=stop)
            async def check():
                calls.append(1)
                if len(calls)<=4:s.state['failure_class']='network_unavailable';raise ConnectionError('synthetic long network outage')
                s.stop_event.set();return []
            async def attach():
                attachments.append(1);s.capture.active=True;s.phase='reading';s.record()
            s._tick_once=check;s.attach=attach
            with patch('multi_source.TRANSIENT_DELAY',0),patch('automation.DELAYS',(0,)),patch('automation.STOP',Path(folder)/'stop'),patch('local_chrome.lifecycle'):
                await s.run()
            self.assertEqual(len(calls),5);self.assertEqual(len(attachments),1)
            self.assertEqual(s.network_pause['result'],'expired')
