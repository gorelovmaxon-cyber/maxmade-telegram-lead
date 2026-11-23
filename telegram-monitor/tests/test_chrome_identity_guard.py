"""Real local Chrome, entirely synthetic page/network and isolated SQLite."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor
import chrome_pilot as pilot
from browser_capture import BrowserCapture, DomContract
from browser_ingest import BrowserAccessError, BrowserIntake

HTML='''<!doctype html><style>.bubble,.message{display:block;min-height:20px}</style>
<div class="settings-container active"><div class="profile-content is-me">Account</div>
<div class="row"><div class="row-title">maxmade_pro</div><div class="row-subtitle">Username</div></div></div>
<div id="column-right"><div class="profile-content"><button class="profile-action">Leave</button></div>
<div class="row-title">t.me/top_ads_freelance</div></div>
<div class="chat active"><div class="topbar"><span class="person-avatar" data-peer-id="-1513982592"></span></div>
<div class="bubbles scrolled-down"><div class="bubbles-inner"><div class="bubble" data-mid="4294967396" data-peer-id="-1513982592" data-timestamp="1000000000"><div class="message">Old synthetic row</div></div></div></div></div>'''


@unittest.skipUnless(importlib.util.find_spec('playwright'),'Install requirements-browser.txt')
class ChromeIdentityGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Fixture commands publish status. Never overwrite a running monitor's
        # filesystem status when this suite is executed from the live checkout.
        publisher=patch('chrome_pilot.publish');publisher.start();self.addCleanup(publisher.stop)
        from playwright.async_api import async_playwright
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        self.ctx=await self.browser.new_context(service_workers='block')
        self.requests=[]
        async def route(req):
            self.requests.append(req.request.url)
            if req.request.url=='https://web.telegram.org/k/':await req.fulfill(status=200,content_type='text/html',body=HTML)
            else:await req.abort()
        await self.ctx.route('**/*',route)
        self.page=await self.ctx.new_page();await self.page.goto('https://web.telegram.org/k/')
        self.state={};await pilot.own_settings(self.page,self.state);await pilot.source_profile(self.page,self.state)
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg=monitor.Config(Path(self.tmp.name)/'test.sqlite3',owner_id=42,client_sources=(pilot.SOURCE,),client_peer_ids=(pilot.PEER,),quiet_start=0,quiet_end=0)
        self.db=monitor.connect(self.cfg.db)
        self.capture=BrowserCapture(self.page,BrowserIntake(self.db,self.cfg,pilot.ACCOUNT,pilot.SOURCE,pilot.PEER),
            DomContract(pilot.ROOT_SELECTOR,pilot.DOM_PEER,True),lambda:pilot.verify_live(self.page,self.state))

    async def asyncTearDown(self):
        await self.capture.stop();self.db.close();self.tmp.cleanup()
        await self.ctx.close();await self.browser.close();await self.pw.stop()

    async def test_verified_panels_allow_baseline_without_importing_history(self):
        await self.capture.arm()
        self.assertTrue(self.capture.active)
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)
        self.assertEqual(self.requests,['https://web.telegram.org/k/'])

    async def test_settings_waits_for_username_row_but_rejects_different_username(self):
        await self.page.locator('.settings-container .row-title').evaluate('''e=>{
            e.textContent='';setTimeout(()=>e.textContent='maxmade_pro',200)}''')
        self.assertTrue((await pilot.own_settings(self.page,self.state))['account_verified'])
        await self.page.locator('.settings-container .row-title').evaluate("e=>e.textContent='different_user'")
        with self.assertRaises(BrowserAccessError):await pilot.own_settings(self.page,self.state)

    async def test_bounded_arm_backup_and_stop_use_only_isolated_database(self):
        from dataclasses import replace
        self.state.update(member=True)
        cfg=replace(self.cfg,token='123456:'+'a'*30)
        local_state=Path(self.tmp.name)/'private';local_state.mkdir()
        reflows=[]
        def api(method,*args,**kwargs):
            if method=='getMe' and limit is None:
                reflows.append(__import__('asyncio').create_task(self.page.evaluate(
                    "()=>{const e=document.querySelector('.settings-container');e.replaceWith(e.cloneNode(true))}")))
            return [] if method=='getUpdates' else {'username':'uffweb_bot'}
        with patch('chrome_pilot.STATE',local_state),patch('chrome_pilot.publish'),patch('monitor.load_env'),\
             patch('monitor.Config.from_env',return_value=cfg),patch.dict('os.environ',{'MONITOR_CLIENT_ACCOUNT':pilot.ACCOUNT}),\
             patch('monitor.Telegram.call',side_effect=api) as calls:
            for delay,limit in ((None,600),(.1,None)):
                result=await pilot.arm(self.page,self.state,max_seconds=limit)
                if reflows:await __import__('asyncio').gather(*reflows)
                self.assertTrue(result['capture_running'])
                self.assertEqual(result['pilot_limit_seconds'],limit)
                if delay:
                    async def callback_started():
                        while not any(call.args[0]=='getUpdates' for call in calls.call_args_list):
                            await __import__('asyncio').sleep(.02)
                    await __import__('asyncio').wait_for(callback_started(),timeout=5)
                await pilot.stop(self.state)
                self.assertTrue(self.state['task'].done())
                self.assertNotIn('db',self.state)
            self.assertEqual([c.args[0] for c in calls.call_args_list],['getMe','getMe','getWebhookInfo','getUpdates'])
            self.assertEqual(len(list(local_state.glob('before-pilot-*.sqlite3'))),2)
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)

    async def test_uppercase_join_rejects_before_bot_or_database_access(self):
        await self.page.locator('.profile-action').evaluate("e=>e.textContent='JOIN'")
        evidence=await pilot.source_profile(self.page,self.state)
        self.assertTrue(evidence['membership_required']);self.assertFalse(evidence['source_verified'])
        with patch('monitor.Telegram.call') as network,patch('monitor.connect') as database:
            with self.assertRaises(BrowserAccessError):await pilot.arm(self.page,self.state)
        network.assert_not_called();database.assert_not_called()

    async def test_own_username_change_disarms(self):
        await self.capture.arm()
        await self.page.locator('.settings-container .row-title').evaluate("e=>e.textContent='different_user'")
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        self.assertFalse(self.capture.active)

    async def test_source_uses_pinned_native_chat_link_without_reloading_stale_hash(self):
        await self.page.evaluate('''()=>{
          const peer=document.querySelector('.topbar [data-peer-id]');peer.dataset.peerId='-999';
          peer.style.cssText='display:block;width:20px;height:20px';
          const a=document.createElement('a');a.className='chatlist-chat';a.dataset.peerId='-1513982592';
          a.href='#-1513982592';a.textContent='Synthetic source';
          a.addEventListener('click',()=>document.querySelector('.topbar [data-peer-id]').dataset.peerId='-1513982592');
          document.body.append(a);
        }''')
        result=await pilot.dispatch(self.page,self.ctx,'source',self.state)
        self.assertEqual(result['phase'],'source_open_unverified')
        self.assertFalse(result['account_verified'])
        self.assertEqual(self.requests,['https://web.telegram.org/k/'])

    async def test_network_failure_is_retryable_but_account_change_requires_attention(self):
        await self.ctx.set_offline(True)
        with self.assertRaises(BrowserAccessError):await pilot.verify_live(self.page,self.state)
        self.assertEqual(self.state['failure_class'],'network_unavailable')
        await self.ctx.set_offline(False)
        await self.page.locator('.settings-container .row-title').evaluate("e=>e.textContent='different_user'")
        with self.assertRaises(BrowserAccessError):await pilot.verify_live(self.page,self.state)
        self.assertEqual(self.state['failure_class'],'manual_attention')

    async def test_webk_connecting_status_rejects_online_dom_until_connection_recovers(self):
        await self.page.evaluate('''()=>{
            const box=document.createElement('div');box.id='chatlist-container';
            box.innerHTML='<span class="sr-only" role="status">Connecting</span>';
            document.body.append(box);
        }''')
        with self.assertRaises(BrowserAccessError):await pilot.verify_live(self.page,self.state)
        self.assertEqual(self.state['failure_class'],'network_unavailable')
        await self.page.locator('#chatlist-container [role=status]').evaluate("e=>e.textContent=''")
        self.assertEqual((await pilot.verify_live(self.page,self.state)).account_username,pilot.ACCOUNT)

    async def test_group_navigation_closes_settings_with_close_button(self):
        import group_navigation
        await self.page.evaluate('''()=>{
            const left=document.createElement('div');left.id='column-left';
            left.innerHTML='<div class="sidebar-header"><button class="sidebar-close-button">Back</button></div>';
            left.querySelector('button').onclick=()=>document.querySelector('.settings-container').style.display='none';
            document.body.append(left);
        }''')
        await group_navigation.dialogs(self.page)
        self.assertFalse(await self.page.locator('.settings-container.active .profile-content.is-me').is_visible())

    async def test_identical_replacement_panel_cannot_reuse_verified_handle(self):
        await self.capture.arm()
        await self.page.locator('.settings-container').evaluate('e=>e.replaceWith(e.cloneNode(true))')
        with self.assertRaises(BrowserAccessError):await self.capture.drain()

    async def test_explicit_source_recheck_reverifies_replaced_settings(self):
        await self.page.locator('.settings-container').evaluate('e=>e.replaceWith(e.cloneNode(true))')
        with self.assertRaises(BrowserAccessError):await pilot.verify_live(self.page,self.state)
        await pilot.source_profile(self.page,self.state)
        self.assertEqual((await pilot.verify_live(self.page,self.state)).account_username,pilot.ACCOUNT)

    async def test_settings_reflow_waits_for_baseline_before_arming(self):
        original=pilot.own_settings
        async def reflow(page,state):
            result=await original(page,state)
            await page.evaluate('''selector=>{const root=document.querySelector(selector);const html=root.innerHTML;
                root.replaceChildren();setTimeout(()=>root.innerHTML=html,200)}''',pilot.ROOT_SELECTOR)
            return result
        with patch('chrome_pilot.own_settings',side_effect=reflow):
            await pilot.source_profile(self.page,self.state)
        await self.capture.arm()
        self.assertTrue(self.capture.active)
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)

    async def test_other_peer_disarms(self):
        await self.capture.arm()
        await self.page.locator('.topbar span').evaluate("e=>e.dataset.peerId='-999'")
        with self.assertRaises(BrowserAccessError):await self.capture.drain()

    async def test_scrolling_away_from_live_end_disarms(self):
        await self.capture.arm()
        await self.page.locator('.bubbles').evaluate("e=>e.classList.remove('scrolled-down')")
        with self.assertRaises(BrowserAccessError):await self.capture.drain()

    async def test_offline_disarms_and_does_not_resume_itself(self):
        await self.capture.arm();await self.ctx.set_offline(True)
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        await self.ctx.set_offline(False)
        self.assertFalse(self.capture.active)
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        await self.capture.arm();self.assertTrue(self.capture.active)

    async def test_reload_requires_new_identity_handles_and_baseline(self):
        await self.capture.arm();await self.page.reload()
        with self.assertRaises(BrowserAccessError):await self.capture.drain()
        with self.assertRaises(BrowserAccessError):await self.capture.arm()
        await pilot.own_settings(self.page,self.state);await pilot.source_profile(self.page,self.state)
        await self.capture.arm()
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],0)
