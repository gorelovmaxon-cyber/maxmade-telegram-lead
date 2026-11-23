import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import monitor
import source_registry as registry
from multi_source import MultiReader
import chrome_startup


class IncrementalTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_status_and_clock_continue_while_notification_io_is_blocked(self):
        import threading
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'sources.sqlite3')) as db:
            registry.initialize(db)
            reader=MultiReader(None);reader.journal=db
            stamp=monitor.iso(monitor.now())
            row={'username':'top_ads_freelance','phase':'reading','capture_running':True,'last_live_check':stamp,'last_web_transport':stamp}
            reader.sessions=[SimpleNamespace(name='top_ads_freelance',capture=SimpleNamespace(active=True),
                last_live=stamp,last_web=stamp,metadata=lambda:dict(row))]
            entered=threading.Event();release=threading.Event();published=[]
            def blocked_delivery():entered.set();release.wait(5)
            reader.delivery_cycle=blocked_delivery
            with patch('chrome_pilot.publish',side_effect=lambda **value:published.append(value)):
                delivery=asyncio.create_task(reader.background());heartbeat=None
                try:
                    deadline=asyncio.get_running_loop().time()+2
                    while not entered.is_set() and asyncio.get_running_loop().time()<deadline:await asyncio.sleep(.005)
                    self.assertTrue(entered.is_set())
                    heartbeat=asyncio.create_task(reader.heartbeat(interval=.01,sample_interval=.01))
                    await asyncio.sleep(.08)
                    self.assertFalse(release.is_set())
                    self.assertGreaterEqual(len(published),2)
                    self.assertTrue(all(value['all_sources_reading'] for value in published))
                    self.assertGreaterEqual(db.execute('select count(*) from readings').fetchone()[0],2)
                finally:
                    reader.stop_event.set();release.set()
                    await asyncio.gather(delivery,*([heartbeat] if heartbeat else []))
    async def test_cancelled_identity_probe_is_not_a_confirmed_account_mismatch(self):
        from multi_source import SourceSession
        class Page:
            def is_closed(self):return False
            async def evaluate(self,*args):await asyncio.Event().wait()
        session=SourceSession({'username':'fixture_source','peer_id':-1000000000123},None,None,None,None,asyncio.Event())
        session.page=Page();session.state={key:object() for key in ('own','settings','source_profile','source_link')}
        with patch('chrome_pilot.origin_ok',return_value=True):
            with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(session.verify(),.02)
        self.assertEqual(session.state['failure_class'],'identity_check_pending')
        self.assertEqual(session.state['guard_stage'],'identity_snapshot')
    async def test_closed_context_interrupts_pending_startup_instead_of_consuming_source_retries(self):
        class Context:
            def __init__(self):self.listeners={}
            def on(self,event,fn):self.listeners[event]=fn
            def remove_listener(self,event,fn):self.listeners.pop(event,None)
        class Api:
            def __init__(self,*args):pass
            def call(self,method,*args,**kwargs):return {'username':'uffweb_bot'} if method=='getMe' else {}
        async def waiting(session):await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);journal=registry.connect(root/'registry.sqlite3')
            cfg=monitor.Config(root/'business.sqlite3',owner_id=42,token='fixture')
            reader=MultiReader(Context());defs=[{'username':'fixture_source','peer_id':-1000000000123}]
            with patch('automation.STOP',root/'stop'),patch('automation.config',return_value=cfg),\
                 patch('source_registry.connect',return_value=journal),patch('source_registry.enabled',return_value=defs),\
                 patch.object(monitor.Config,'live_ready'),patch('monitor.Telegram',Api),\
                 patch('monitor.single_instance',side_effect=lambda *args:contextlib.nullcontext()),\
                 patch.dict('os.environ',{'MONITOR_CLIENT_ACCOUNT':'maxmade_pro'}),\
                 patch('chrome_pilot.publish'),patch('multi_source.SourceSession.run',waiting):
                task=asyncio.create_task(reader.start())
                while not reader.sessions:await asyncio.sleep(.01)
                reader.context.listeners['close']()
                with self.assertRaises(ConnectionError):await asyncio.wait_for(task,1)
                self.assertFalse(reader.metadata()['runtime_alive'])
                self.assertFalse(reader.metadata()['capture_running'])
                self.assertEqual(reader.sessions[0].attempts,0)
                self.assertEqual(journal.execute('select reason from gaps').fetchone()[0],'context_closed')
                await reader.close()

    async def test_operator_stop_blocks_startup_before_bot_handshake(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'stop';marker.write_text('stop')
            reader=MultiReader(None)
            with patch('automation.STOP',marker),patch('monitor.Telegram') as api:
                with self.assertRaises(InterruptedError):await reader.start()
            api.assert_not_called()

    async def test_add_one_preserves_existing_pages_tasks_and_background_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            db=registry.connect(Path(folder)/'registry.sqlite3')
            registry.initialize(db)
            with db:
                db.execute("update sources set access='joined' where username='top_ads_freelance'")
                db.execute("insert into stage_acceptance values(2,'fixture','{}')")
                db.execute("update sources set enabled=1,access='joined',type='group',peer_id=-1000000000123,rules_state='reviewed',read_permission='local_client' where username='freelead'")
            reader=MultiReader(None);reader.journal=db;reader.started=True
            reader.cfg=monitor.Config(Path(folder)/'business.sqlite3')
            first=SimpleNamespace(name='top_ads_freelance',page=object());background=object();old_task=object()
            reader.sessions=[first];reader.tasks=[old_task,background]
            reader.metadata=lambda:{'all_sources_reading':True}
            class Session:
                def __init__(self,definition,*args):self.name=definition['username']
                async def run(self):await asyncio.Event().wait()
            with patch('multi_source.SourceSession',Session),patch.object(monitor.Config,'live_ready'):
                result=await reader.add_pending()
            self.assertEqual(result['added_source'],'freelead')
            self.assertIs(reader.sessions[0],first);self.assertIs(reader.tasks[0],old_task)
            self.assertIs(reader.tasks[-1],background);self.assertEqual(len(reader.sessions),2)
            reader.tasks[1].cancel();await asyncio.gather(reader.tasks[1],return_exceptions=True)
            with self.assertRaises(ValueError):await reader.add_pending()
            db.close()

    async def test_failed_readonly_startup_keeps_browser_available_for_bounded_retry(self):
        page=object();context=object();state={'multi_source_requested':True}
        async def unavailable(*args):raise monitor.UnknownDelivery()
        with patch('multi_source.activate',unavailable):result=await chrome_startup.activate(page,context,state)
        self.assertEqual(result['failure_class'],'startup_transient')
        self.assertFalse(result['capture_running']);self.assertEqual(result['error_type'],'UnknownDelivery')


class ObservationTests(unittest.TestCase):
    def test_later_enrollment_does_not_inherit_old_reading_time_and_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'registry.sqlite3')) as db:
            registry.initialize(db)
            with db:db.execute("update sources set access='joined' where username='top_ads_freelance'")
            run=registry.start_observation(db,'fixture')
            self.assertEqual(registry.start_observation(db,'again'),run)
            registry.sample(db,'freelead',True,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')
            registry.enroll_active(db,'freelead')
            report=registry.observation_report(db)
            self.assertEqual(len(report['sources']),2)
            row=next(r for r in report['sources'] if r['username']=='freelead')
            self.assertEqual(row['observed_seconds'],0);self.assertFalse(row['complete_24h'])
