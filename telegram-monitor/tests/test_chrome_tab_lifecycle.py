"""The real host must preserve remaining tabs when its original tab closes."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

import local_chrome


@unittest.skipUnless(importlib.util.find_spec('playwright'),'Install requirements-browser.txt')
class ChromeTabLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_closing_first_and_replacement_tabs_preserves_others_until_explicit_close(self):
        from playwright.async_api import async_playwright
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(channel='chrome',headless=True)
                context=await browser.new_context(service_workers='block')
                async def route(req):await req.fulfill(content_type='text/html',body='<p>Local lifecycle fixture</p>')
                await context.route('**/*',route)
                await context.new_page()
                proxy=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=context)))
                @asynccontextmanager
                async def fake_playwright():yield proxy
                async def manual_batch(page,ctx,runtime,next_batch=False):
                    for _ in range(4):await ctx.new_page()
                    await page.close()
                    return {'phase':'manual_group_batch'}
                def mkdir(path):path.mkdir(parents=True,exist_ok=True)
                with patch('playwright.async_api.async_playwright',fake_playwright),\
                     patch('local_chrome.STATE',state),patch('local_chrome.PROFILE',state/'profile'),\
                     patch('local_chrome.private_directory',mkdir),patch('batch_group_tabs.open_batch',manual_batch):
                    task=asyncio.create_task(local_chrome.host(manual_batch=True))
                    async def wait_rebounds(count):
                        for _ in range(100):
                            file=state/'browser-lifecycle.jsonl'
                            events=[json.loads(s) for s in file.read_text().splitlines()] if file.exists() else []
                            if sum(e['event']=='control_tab_rebound' for e in events)>=count:return
                            if task.done():await task;self.fail('Host exited while other tabs remained')
                            await asyncio.sleep(.05)
                        self.fail('Host did not select an existing tab')
                    try:
                        await wait_rebounds(1)
                        self.assertEqual(len(context.pages),4)
                        self.assertFalse(task.done())
                        await context.pages[0].close()
                        await wait_rebounds(2)
                        self.assertEqual(len(context.pages),3)
                        self.assertFalse(task.done())
                        (state/'command.json').write_text(json.dumps({'id':'fixture-close','action':'close'}))
                        await asyncio.wait_for(task,timeout=10)
                        final=json.loads((state/'status.json').read_text())
                        self.assertEqual(final['exit_reason'],'explicit_close_command')
                        self.assertEqual(final['phase'],'stopped')
                    finally:
                        if not task.done():task.cancel()
                        await browser.close()


if __name__=='__main__':unittest.main()
