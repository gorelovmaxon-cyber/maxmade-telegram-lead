"""Inspect only public group description and one expressly selected rules post."""
import contextlib
import re
import chrome_pilot
import verify_selected_groups


async def run(context):
    page=await context.new_page()
    try:
        await page.goto('https://web.telegram.org/k/',wait_until='domcontentloaded',timeout=30000)
        await chrome_pilot.own_settings(page,{})
        record={}
        await verify_selected_groups.check(page,'freelead',record)
        # Only this public rules permalink, never an arbitrary history range.
        link=page.locator('#column-right a[href]').filter(has_text=re.compile('1189432'))
        exact=page.locator('#column-right a[href="https://t.me/freelead/1189432"]')
        if await exact.count()==1:await exact.click(timeout=5000)
        elif await link.count()==1:await link.click(timeout=5000)
        else:return {'rules_probe':{'access':record,'rules_post':'link_unavailable'}}
        bubble=page.locator('.chat.active .bubble[data-mid="4296156728"], .chat.active .bubble[data-mid="1189432"]')
        await bubble.wait_for(timeout=10000)
        return {'rules_probe':{'access':record,
                'rules_text':(await bubble.locator('.message').inner_text())[:7000]}}
    finally:
        with contextlib.suppress(Exception):await page.close()
