"""Show exactly the requested three groups in the monitor's own account; never click JOIN."""
import contextlib
import asyncio
import automation
import chrome_pilot
import monitor
import verify_selected_groups

NAMES=('onpeak_chat','businessclass_rbc','spbeventchat')


async def run(context,state):
    reader=state.get('multi_reader')
    if not reader or not reader.metadata()['all_sources_reading']:raise ValueError('Wait for current sources before opening manual review')
    existing=state.get('membership_review_pages',[])
    if any(not p.is_closed() for p in existing):
        for page in existing:
            if not page.is_closed():await page.bring_to_front();break
        return {'membership_review_already_open':True}
    pages=[];rows=[];state['membership_review_pages']=pages
    async with reader.attach_gate:
        for name in NAMES:
            row={'username':name};rows.append(row)
            page=await context.new_page();pages.append(page)
            try:
                await page.goto('https://web.telegram.org/k/',wait_until='domcontentloaded',timeout=25000)
                await page.locator('#column-left .sidebar-tools-button').wait_for(state='visible',timeout=20000)
                await page.bring_to_front();await chrome_pilot.own_settings(page,{})
                await asyncio.wait_for(verify_selected_groups.check(page,name,row),timeout=35)
            except Exception as error:row['error_type']=type(error).__name__
            automation.write_json(automation.STATE/'membership-review.json',{'at':monitor.iso(monitor.now()),
                'expected_account':'@'+chrome_pilot.ACCOUNT,'groups':rows,'automatic_joins':0})
    if pages:
        await pages[0].bring_to_front()
        cdp=await context.new_cdp_session(pages[0])
        try:
            wid=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'normal'}})
        finally:await cdp.detach()
    return {'membership_review_tabs':len(pages),'automatic_joins':0}
