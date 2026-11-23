"""Bounded metadata-only access checks. Never joins or reads message bodies."""
import asyncio
import re
import chrome_pilot

CANDIDATES=('freelead','free_chat_for_freelance','Frilanser_100')


async def inspect(context):
    results=[]
    for name in CANDIDATES:
        page=await context.new_page();record={'username':name,'enabled':False,'message_sample_reviewed':False}
        try:
            await page.goto('https://web.telegram.org/k/#@'+name,wait_until='domcontentloaded',timeout=30000)
            await page.locator('.chat.active .topbar [data-peer-id]').first.wait_for(timeout=20000)
            await chrome_pilot.own_settings(page,{})
            await page.locator('.chat.active .topbar .chat-info-container').click(timeout=5000)
            panel=page.locator('#column-right .profile-content');await panel.wait_for(state='visible',timeout=10000)
            link=page.locator('#column-right .row-title').filter(has_text=re.compile('^t\\.me/'+re.escape(name)+'$',re.I))
            await link.wait_for(state='visible',timeout=10000)
            record.update(await page.evaluate('''()=>({
              peers:[...document.querySelectorAll('.chat.active .topbar [data-peer-id]')].map(e=>e.dataset.peerId),
              join:[...document.querySelectorAll('#column-right .profile-action')].filter(e=>/Join|Вступить|Подать заявку/i.test(e.textContent)).length,
              leave:[...document.querySelectorAll('#column-right .profile-action')].filter(e=>/Leave|Покинуть|Выйти/i.test(e.textContent)).length})'''))
            record.update(identity_verified=True,account_verified=True,member=record['leave']==1 and record['join']==0)
        except Exception as error:record.update(identity_verified=False,error_type=type(error).__name__)
        finally:await page.close()
        results.append(record)
    return results
