"""Missing evidence can retry; positive contradictions can never arm capture."""
import asyncio
import contextlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import chrome_pilot as pilot
import chrome_startup
import monitor
import source_registry as registry
from browser_ingest import BrowserAccessError
from multi_source import SourceSession


class IdentityDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_loading_probe_rejects_partial_reader_before_touching_any_page(self):
        import local_chrome_runtime
        reader=SimpleNamespace(metadata=lambda:{'all_sources_reading':False},sessions=[None]*3)
        with self.assertRaises(ValueError):
            await local_chrome_runtime.dispatch(None,None,'trial_identity_loading',{'multi_reader':reader})

    async def test_loading_probe_only_touches_the_requested_source(self):
        import local_chrome_runtime
        from unittest.mock import AsyncMock
        pages=[SimpleNamespace(evaluate=AsyncMock(return_value={'injected':True})) for _ in range(3)]
        names=['top_ads_freelance','biznes_chat','freelead']
        reader=SimpleNamespace(metadata=lambda:{'all_sources_reading':True},sessions=[SimpleNamespace(name=n,page=p,generation=1) for n,p in zip(names,pages)])
        result=await local_chrome_runtime.dispatch(None,None,'trial_identity_loading',{'multi_reader':reader})
        self.assertTrue(result['identity_loading_trial']['injected'])
        pages[0].evaluate.assert_awaited_once();pages[1].evaluate.assert_not_awaited();pages[2].evaluate.assert_not_awaited()
    async def check(self,**overrides):
        proof=dict(handlesConnected=True,usernames=[pilot.ACCOUNT],publicLink='t.me/fixture_source',
          online=True,connecting=False,own=1,ownVisible=True,profileVisible=True,peers=['-123'],
          leave=1,join=0,roots=1,atEnd=True)
        proof.update(overrides)
        class Page:
            url='https://web.telegram.org/k/'
            def is_closed(self):return False
            async def evaluate(self,*args):return dict(proof)
        s=SourceSession({'username':'fixture_source','peer_id':-1000000000123},None,None,None,None,asyncio.Event())
        s.page=Page();s.state={k:object() for k in ('own','settings','source_profile','source_link')}
        with self.assertRaises(BrowserAccessError):await s.verify()
        return s

    async def test_absent_or_duplicate_expected_username_is_unconfirmed(self):
        for names in ([],[pilot.ACCOUNT,pilot.ACCOUNT]):
            with self.subTest(names_count=len(names)):
                s=await self.check(usernames=names)
                self.assertEqual(s.state['failure_class'],'identity_check_pending')
                self.assertIsNone(s.last_live)

    async def test_missing_source_evidence_is_not_positive_mismatch(self):
        for fields in ({'publicLink':None},{'peers':[]},{'leave':0,'join':0},{'handlesConnected':False}):
            with self.subTest(fields=fields):
                s=await self.check(**fields)
                self.assertEqual(s.state['failure_class'],'identity_check_pending')

    async def test_actual_wrong_account_source_or_join_requires_owner(self):
        for fields in ({'usernames':['different_user']},{'publicLink':'t.me/different_source'},
                       {'peers':['-999']},{'leave':0,'join':1}):
            with self.subTest(fields=fields):
                s=await self.check(**fields)
                self.assertEqual(s.state['failure_class'],'manual_attention')

    async def test_delayed_automatic_command_cannot_replace_live_reader_or_clear_owner_pause(self):
        meta={'runtime_alive':True,'all_sources_reading':False,'source_states':[{'phase':'manual_attention'}]}
        state={'multi_source_requested':True,'multi_reader':SimpleNamespace(metadata=lambda:dict(meta))}
        with patch('multi_source.activate') as start:
            result=await chrome_startup.activate(None,None,state,automatic=True)
        start.assert_not_called();self.assertTrue(result['runtime_alive'])
        self.assertEqual(result['source_states'][0]['phase'],'manual_attention')

    async def test_pending_identity_recovers_with_bounded_checks_and_no_operator_resume(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.closing(registry.connect(Path(folder)/'sources.sqlite3')) as db:
            registry.initialize(db);stop=asyncio.Event()
            s=SourceSession({'username':'top_ads_freelance','peer_id':-1000000000123},None,None,None,db,stop)
            attempts=[]
            async def attach():
                attempts.append(1)
                if len(attempts)==1:
                    s.state['failure_class']='identity_check_pending';raise BrowserAccessError('synthetic missing pane')
                s.capture=SimpleNamespace(active=True,stop=async_stop);s.phase='reading';s.last_live=monitor.iso(monitor.now())
            async def async_stop():pass
            async def tick():stop.set()
            s.attach=attach;s.tick=tick
            with patch('automation.STOP',Path(folder)/'stop'),patch('automation.DELAYS',(0,)),patch('local_chrome.lifecycle'):
                await s.run()
            self.assertEqual(len(attempts),2)
            self.assertNotEqual(s.state.get('failure_class'),'manual_attention')


@unittest.skipUnless(importlib.util.find_spec('playwright'),'Install requirements-browser.txt')
class OwnPaneRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        from test_chrome_identity_guard import HTML
        self.html=HTML;self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        self.ctx=await self.browser.new_context(service_workers='block')
        await self.ctx.route('**/*',lambda r:r.fulfill(content_type='text/html',body=HTML))
        self.page=await self.ctx.new_page();await self.page.goto('https://web.telegram.org/k/');self.state={}

    async def asyncTearDown(self):
        await self.ctx.close();await self.browser.close();await self.pw.stop()

    async def test_pane_removed_after_row_wait_cannot_be_labelled_wrong_account(self):
        original=self.page.wait_for_function
        async def disappear(*args,**kwargs):
            result=await original(*args,**kwargs)
            await self.page.locator('.profile-content.is-me').evaluate('e=>e.remove()')
            return result
        with patch.object(self.page,'wait_for_function',side_effect=disappear):
            with self.assertRaises(BrowserAccessError):await pilot.own_settings(self.page,self.state)
        self.assertEqual(self.state.get('failure_class'),'identity_check_pending')
        self.assertNotIn('account',self.state)
        await self.page.set_content(self.html)
        self.assertTrue((await pilot.own_settings(self.page,self.state))['account_verified'])

    async def test_visible_different_username_stops_and_safe_evidence_has_no_value(self):
        await self.page.locator('.settings-container .row-title').evaluate("e=>e.textContent='different_user'")
        with self.assertRaises(BrowserAccessError):await pilot.own_settings(self.page,self.state)
        self.assertEqual(self.state.get('failure_class'),'manual_attention')
        self.assertNotIn('different_user',str(self.state.get('identity_evidence',{})))
