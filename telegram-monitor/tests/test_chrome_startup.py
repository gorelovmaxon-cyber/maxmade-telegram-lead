import unittest
from unittest.mock import AsyncMock,MagicMock,patch
import chrome_startup


class ChromeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_account_stops_before_navigation_or_any_arm(self):
        from browser_ingest import BrowserAccessError
        page=MagicMock();page.locator.return_value.count=AsyncMock(return_value=0)
        with patch('chrome_pilot.own_settings',new=AsyncMock(side_effect=BrowserAccessError('Selected account does not match'))),\
             patch('chrome_pilot.dispatch',new=AsyncMock()) as navigate,\
             patch('chrome_pilot.stop',new=AsyncMock()),patch('chrome_pilot.arm',new=AsyncMock()) as arm:
            result=await chrome_startup.activate(page,None,{})
        self.assertEqual(result['failure_class'],'manual_attention')
        navigate.assert_not_called();arm.assert_not_called()

    async def test_transient_panel_replacement_rechecks_before_any_arm(self):
        from browser_ingest import BrowserAccessError
        page=MagicMock();page.locator.return_value.first.wait_for=AsyncMock();page.wait_for_timeout=AsyncMock()
        page.locator.return_value.count=AsyncMock(return_value=0)
        context=MagicMock();cdp=MagicMock();cdp.send=AsyncMock(side_effect=[{'windowId':1},{}]);cdp.detach=AsyncMock()
        context.new_cdp_session=AsyncMock(return_value=cdp)
        with patch('chrome_pilot.own_settings',new=AsyncMock()),\
             patch('chrome_pilot.dispatch',new=AsyncMock(return_value={'phase':'source_open_unverified'})) as navigate,\
             patch('chrome_pilot.source_profile',new=AsyncMock(return_value={'source_verified':True})) as panels,\
             patch('chrome_pilot.verify_live',new=AsyncMock(side_effect=[BrowserAccessError('fixture'),None])),\
             patch('chrome_pilot.arm',new=AsyncMock(return_value={'capture_running':True})) as arm:
            result=await chrome_startup.activate(page,context,{})
        self.assertTrue(result['capture_running']);self.assertEqual(panels.await_count,2)
        navigate.assert_awaited_once();arm.assert_awaited_once()

    async def test_unverified_source_cannot_start_or_request_credentials(self):
        with patch('chrome_pilot.dispatch',new=AsyncMock(return_value={'phase':'source_not_armed'})),\
             patch('chrome_pilot.stop',new=AsyncMock()) as stop,\
             patch('chrome_pilot.arm',new=AsyncMock()) as arm:
            result=await chrome_startup.activate(None,None,{})
        self.assertFalse(result['capture_running'])
        self.assertEqual(result['phase'],'startup_needs_local_attention')
        arm.assert_not_called();stop.assert_awaited_once()

    async def test_window_setup_failure_stops_already_armed_capture(self):
        page=MagicMock();page.locator.return_value.first.wait_for=AsyncMock()
        page.locator.return_value.count=AsyncMock(return_value=0)
        context=MagicMock();context.new_cdp_session=AsyncMock(side_effect=OSError())
        with patch('chrome_pilot.own_settings',new=AsyncMock()),\
             patch('chrome_pilot.dispatch',new=AsyncMock(return_value={'phase':'source_open_unverified'})),\
             patch('chrome_pilot.source_profile',new=AsyncMock(return_value={'source_verified':True})),\
             patch('chrome_pilot.verify_live',new=AsyncMock()),\
             patch('chrome_pilot.arm',new=AsyncMock(return_value={'capture_running':True})) as arm,\
             patch('chrome_pilot.stop',new=AsyncMock()) as stop:
            result=await chrome_startup.activate(page,context,{})
        self.assertFalse(result['capture_running'])
        self.assertIsNone(arm.await_args.kwargs['max_seconds'])
        stop.assert_awaited_once()
