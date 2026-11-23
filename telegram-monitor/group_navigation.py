"""Navigate selected public groups through the native Web K UI, never send/join."""
import re
import chrome_pilot

PINS={'top_ads_freelance':'-1513982592','biznes_chat':'-1371121321','businessclass_rbc':'-1853486481',
      'free_chat_for_freelance':'-1740614212','frilanser_100':'-2101594712'}


async def dialogs(page):
    await chrome_pilot.dismiss_menu(page)
    own=page.locator('.settings-container.active .profile-content.is-me')
    search=page.locator('#column-left .input-search-input:visible')
    back=page.locator('#column-left .sidebar-header .sidebar-back-button:visible, #column-left .sidebar-header .sidebar-close-button:visible')
    for _ in range(2):
        if not await own.is_visible() and not await search.count():break
        if await back.count()!=1:break
        was_own=await own.is_visible()
        await back.first.click(timeout=3000)
        if was_own:await own.wait_for(state='hidden',timeout=4000)
        await page.wait_for_timeout(350)


async def open_group(page,name,record):
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',name):raise ValueError('Invalid group username')
    record['stage']='show_dialogs'
    await dialogs(page)
    pin=PINS.get(name.lower())
    if pin:
        row=page.locator('.chatlist-chat[data-peer-id="'+pin+'"]')
        if await row.count()==1 and await row.is_visible():
            record['stage']='click_pinned_chat'
            await row.click(timeout=6000)
            record['stage']='verify_pinned_header'
            await page.locator('.chat.active .topbar .person-avatar[data-peer-id="'+pin+'"]').first.wait_for(state='attached',timeout=8000)
            return
    record['stage']='open_global_search'
    search=page.locator('#column-left .input-search-input:visible')
    if not await search.count():
        await page.locator('#column-left .sidebar-header button[aria-label="Search"]:visible').click(timeout=4000)
    search=page.locator('#column-left .input-search-input:visible')
    if await search.count()!=1:raise ValueError('Global search field ambiguous')
    await search.fill('@'+name,timeout=4000)
    record['stage']='exact_username_search_result'
    expression='''name=>{
      const match=t=>(t.match(/@[a-zA-Z0-9_]+/g)||[]).some(s=>s.toLowerCase()==='@'+name.toLowerCase());
      return [...document.querySelectorAll('#column-left .search-group-contacts .row[data-peer-id]')].filter(e=>
        e.getClientRects().length && [...e.querySelectorAll('.row-subtitle,.dialog-subtitle,[dir="ltr"]')].some(s=>match(s.textContent||'')));
    }'''
    await page.wait_for_function('''name=>{
      const match=t=>(t.match(/@[a-zA-Z0-9_]+/g)||[]).some(s=>s.toLowerCase()==='@'+name.toLowerCase());
      return [...document.querySelectorAll('#column-left .search-group-contacts .row[data-peer-id]')].some(e=>
        e.getClientRects().length && [...e.querySelectorAll('.row-subtitle,.dialog-subtitle,[dir="ltr"]')].some(s=>match(s.textContent||'')));
    }''',arg=name,timeout=8000)
    handles=await page.evaluate_handle(expression,name)
    # Fetch only exact search-result peer IDs, not any message snippets.
    peers=await handles.evaluate('a=>[...new Set(a.map(e=>e.dataset.peerId))]')
    if len(peers)!=1 or not re.fullmatch(r'-[1-9][0-9]*',peers[0]):raise ValueError('Public group search ambiguous')
    if pin and pin!=peers[0]:raise chrome_pilot.BrowserAccessError('Pinned group differs from search result')
    record['resolved_dom_peer']=peers[0]
    element=await handles.evaluate_handle('a=>a[0]')
    record['stage']='click_exact_search_result'
    await element.as_element().click(timeout=4000)
    await page.locator('.chat.active .topbar .person-avatar[data-peer-id="'+peers[0]+'"]').first.wait_for(timeout=8000)
    # Return to dialogs so the next Settings check is accessible.
    await dialogs(page)
