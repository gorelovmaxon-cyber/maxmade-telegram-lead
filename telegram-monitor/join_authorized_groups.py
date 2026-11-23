"""User-authorized joins in the existing local Chrome page, sequentially.

Scope is the 20 catalogue usernames the user selected on 2026-11-05.
No client messages, payments, captcha solving, session export or reader expansion.
"""
from pathlib import Path
import json
import re

import chrome_pilot
import monitor
from manual_group_tabs import GROUPS

OUT=Path(__file__).resolve().parent/'state/local-chrome/authorized-joins-20261105.json'
ORDER=('bitrixinside24','biznes_chat','businessclass_rbc','onpeak_chat','odp2021','expopeople',
       *(name for name in GROUPS if name not in ('bitrixinside24','biznes_chat','businessclass_rbc','onpeak_chat','odp2021','expopeople')))
JOIN=re.compile(r'Join|Вступить|Присоединиться|Подать заявку|Subscribe|Подписаться',re.I)


def save(rows,complete=False):
    value={'at':monitor.iso(monitor.now()),'authorization':'user_requested_all_20_catalogue_joins_20261105',
           'complete':complete,'account':'@maxmade_pro','groups':rows,'reader_sources_unchanged':True}
    tmp=OUT.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2),encoding='utf-8');tmp.replace(OUT)


async def signals(page):
    # Only membership and safety controls, never message text or credentials.
    return await page.evaluate('''()=>{
      const visible=e=>!!e.getClientRects().length;
      const actions=[...document.querySelectorAll('#column-right .profile-action,.chat.active .chat-input button')].filter(visible).map(e=>e.textContent||'');
      const dialogs=[...document.querySelectorAll('.popup:not(.hide),.modal')].filter(visible).map(e=>e.textContent||'').join(' ');
      const payment=/stars|зв[её]зд|оплат|купить|purchase|payment|\$|₽|€|premium/i;
      return {leave:actions.filter(t=>/leave|покинуть|выйти/i.test(t)).length,
        join:actions.filter(t=>/join|вступить|присоединиться|подать заявку|subscribe|подписаться/i.test(t)).length,
        pending:actions.some(t=>/cancel request|отменить заявку|request sent|заявка отправлена/i.test(t)),
        paid:actions.some(t=>payment.test(t)) || payment.test(dialogs),
        challenge:!!document.querySelector('iframe[src*="captcha"],.g-recaptcha,.h-captcha') || /captcha|капч|prove.*human|подтвердите.*человек/i.test(dialogs),
        rate_limit:/too many|flood|слишком много|попробуйте позже|try again later/i.test(dialogs),
        dialog_visible:!!dialogs,
        peers:new Set([...document.querySelectorAll('.chat.active .topbar [data-peer-id]')].map(e=>e.dataset.peerId)).size};
    }''')


async def one(page,name):
    row={'username':name,'state':'unconfirmed','join_clicked':False}
    await page.goto('https://web.telegram.org/k/#@'+name,wait_until='domcontentloaded',timeout=20000)
    await page.reload(wait_until='domcontentloaded',timeout=20000)
    await page.locator('.chat.active .topbar .chat-info-container').wait_for(timeout=10000)
    await chrome_pilot.own_settings(page,{})
    profile=page.locator('#column-right .profile-content')
    if not await profile.is_visible():await page.locator('.chat.active .topbar .chat-info-container').click(timeout=4000)
    await profile.wait_for(state='visible',timeout=7000)
    link=page.locator('#column-right .row-title').filter(has_text=re.compile('^t\\.me/'+re.escape(name)+'$',re.I))
    await link.wait_for(state='visible',timeout=7000)
    # Allow membership actions to finish rendering after the profile opens.
    await page.wait_for_timeout(700)
    before=await signals(page);row['before']=before
    if before['peers']!=1:return row
    if before['rate_limit']:return {**row,'state':'rate_limited'}
    if before['challenge']:return {**row,'state':'challenge_requires_user'}
    if before['paid']:return {**row,'state':'payment_not_authorized'}
    if before['pending']:return {**row,'state':'request_pending'}
    if before['leave']>0 and before['join']==0:return {**row,'state':'already_joined'}
    button=page.locator('#column-right .profile-action:visible').filter(has_text=JOIN)
    if before['join']==0 or await button.count()!=1:return row
    await button.click(timeout=5000);row['join_clicked']=True
    await page.wait_for_timeout(1200)
    after=await signals(page)
    if after['paid']:return {**row,'state':'payment_not_authorized'}
    if after['challenge']:return {**row,'state':'challenge_requires_user'}
    if after['rate_limit']:return {**row,'state':'rate_limited'}
    if after['dialog_visible']:
        # Only a standard, exact Join confirmation. No unrelated modal actions.
        confirm=page.locator('.popup:visible button,.popup:visible .btn-primary,.modal:visible button').filter(has_text=re.compile(r'^(Join|Вступить|Присоединиться)( к группе| в группу)?$',re.I))
        if await confirm.count()==1:
            await confirm.click(timeout=4000);await page.wait_for_timeout(1200)
        else:return {**row,'state':'confirmation_requires_user'}
    # Membership can update asynchronously; no second join click/retry.
    for attempt in range(4):
        after=await signals(page)
        if after['rate_limit']:return {**row,'state':'rate_limited'}
        if after['pending']:return {**row,'state':'request_pending'}
        if after['leave']>0 and after['join']==0:return {**row,'state':'joined'}
        await page.wait_for_timeout(800)
    row['state']='clicked_unconfirmed';row['after']=after
    return row


async def run(page,context,state):
    state['manual_join_mode']=True
    await chrome_pilot.own_settings(page,state)
    rows=[]
    for name in ORDER:
        try:row=await one(page,name)
        except chrome_pilot.BrowserAccessError:
            rows.append({'username':name,'state':'account_not_verified','join_clicked':False});save(rows)
            return {'authorized_join_stopped':'account_not_verified','checked':len(rows)}
        except Exception as error:row={'username':name,'state':'unconfirmed','error_type':type(error).__name__}
        rows.append(row);save(rows)
        if row['state'] in ('rate_limited','challenge_requires_user','confirmation_requires_user'):
            return {'authorized_join_stopped':row['state'],'checked':len(rows)}
        await page.wait_for_timeout(1500)
    save(rows,True)
    return {'authorized_joins_complete':True,'checked':len(rows),
            'join_states':{key:sum(r['state']==key for r in rows) for key in {r['state'] for r in rows}}}
