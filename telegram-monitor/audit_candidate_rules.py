"""One temporary page; public description and specifically linked rules only."""
import asyncio
import contextlib
import importlib
import chrome_pilot
import verify_selected_groups
import automation


async def run(context,names=None,destination='candidate-rules.json'):
    importlib.reload(verify_selected_groups)
    rows=[];page=await context.new_page();stage='new_page'
    try:
        await page.goto('https://web.telegram.org/k/',wait_until='domcontentloaded',timeout=25000)
        stage='wait_sidebar'
        await page.locator('#column-left .sidebar-tools-button').wait_for(state='visible',timeout=20000)
        stage='verify_own_account'
        try:await chrome_pilot.own_settings(page,{})
        except chrome_pilot.BrowserAccessError:
            names=await page.locator('.settings-container.active').evaluate_all('els=>els.flatMap(e=>[...e.querySelectorAll(".row")].filter(r=>/^(Username|Имя пользователя|Имя пользователя Telegram)$/i.test(r.querySelector(".row-subtitle")?.textContent?.trim()||"")).map(r=>r.querySelector(".row-title")?.textContent?.trim()))')
            return {'candidate_rules_error':{'stage':'own_account_before_candidate_navigation','observed_usernames':names,'expected_account_match':False}}
        for index,name in enumerate(names or ('bitrixinside24','biznes_chat','free_chat_for_freelance','Frilanser_100','tilda_official_chat')):
            item={'username':name};rows.append(item)
            try:
                if index:
                    await page.close();page=await context.new_page()
                    await page.goto('https://web.telegram.org/k/',wait_until='domcontentloaded',timeout=25000)
                    await page.locator('#column-left .sidebar-tools-button').wait_for(state='visible',timeout=20000)
                    await chrome_pilot.own_settings(page,{})
                await page.bring_to_front()
                await asyncio.wait_for(verify_selected_groups.check(page,name,item),timeout=30)
                item['stage']='visible_tail'
                down=page.locator('.chat.active .bubbles-go-down:not(.hide):visible')
                if await down.count()==1:await down.click(force=True,timeout=4000);await page.wait_for_timeout(600)
                # Local aggregate of the current visible tail only. Never import it
                # as new events or export bodies/authors/participant lists.
                peer=str(item['peer_id']+10**12)
                tail=await page.locator('.chat.active .bubbles .bubbles-inner:not(.bubbles-remover) .bubble[data-mid][data-peer-id="'+peer+'"][data-timestamp]').evaluate_all('''els=>els.filter(e=>e.getClientRects().length && !e.classList.contains('is-date')).map(e=>({timestamp:e.dataset.timestamp,body:e.querySelector('.message')?.innerText||''}))''')
                import monitor
                import datetime as dt
                counts={};stamps=[];cfg=automation.config()
                for message in tail:
                    try:stamp=dt.datetime.fromtimestamp(float(message['timestamp']),monitor.UTC)
                    except (ValueError,TypeError,OverflowError):continue
                    stamps.append(stamp)
                    kind,_,priority,_=monitor.classify(message['body'],stamp,cfg)
                    key=kind+':'+priority;counts[key]=counts.get(key,0)+1
                item['tail_audit']={'visible_messages':len(stamps),'latest_at':monitor.iso(max(stamps)) if stamps else None,
                    'classification_counts':counts,'scope':'visible_tail_only_not_history_or_new_event_proof'}
                item['public_about']=await page.locator('#column-right .row-title.pre-wrap').all_text_contents()
                item['stage']='complete'
                pin=page.locator('.chat.active .pinned-message[data-mid]:visible')
                item['pinned_rule_found']=False
                if await pin.count()==1:
                    subtitle=await pin.locator('.pinned-message-subtitle').inner_text()
                    if __import__('re').search(r'правил|rules|регламент',subtitle,__import__('re').I):
                        mid=await pin.get_attribute('data-mid')
                        if not __import__('re').fullmatch('[1-9][0-9]*',mid or ''):raise ValueError('Pinned rule ID missing')
                        await pin.locator('.pinned-message-wrapper').click(timeout=5000)
                        rule=page.locator('.chat.active .bubble[data-mid="'+mid+'"] .message')
                        await rule.wait_for(timeout=10000)
                        item.update(pinned_rule_found=True,pinned_rule_mid=mid,pinned_rule_text=(await rule.inner_text())[:7000])
                if name=='biznes_chat':
                    try:
                        link=page.locator('#column-right a[href="https://t.me/biznes_chat/585370"]')
                        if await link.count()==1:
                            await link.click(force=True,timeout=5000)
                            bubble=page.locator('.chat.active .bubble[data-mid="4295552666"], .chat.active .bubble[data-mid="585370"]')
                            await bubble.wait_for(timeout=10000)
                            item['rules_text']=(await bubble.locator('.message').inner_text())[:7000]
                    except Exception as error:item['rules_fetch_error']=type(error).__name__
            except Exception as error:
                item['error_type']=type(error).__name__
                with contextlib.suppress(Exception):
                    item['layout_failure']=await page.evaluate('''()=>{
                      const b=document.querySelector('.chat.active .bubbles-go-down:not(.hide)');const r=b?.getBoundingClientRect();
                      const top=r?document.elementFromPoint(r.x+r.width/2,r.y+r.height/2):null;
                      return {width:innerWidth,height:innerHeight,down_box:r?{x:r.x,y:r.y,w:r.width,h:r.height}:null,
                        down_center_class:top?.className,center_is_down:!!top?.closest('.bubbles-go-down'),own_visible:!!document.querySelector('.settings-container.active .profile-content.is-me')?.getClientRects().length};}''')
            automation.write_json(automation.STATE/destination,{'at':__import__('monitor').iso(__import__('monitor').now()),'groups':rows,'automatic_joins':0})
    except Exception as error:
        automation.write_json(automation.STATE/destination,{'groups':rows,'stage':stage,'error_type':type(error).__name__,'automatic_joins':0})
        return {'candidate_rules_error':{'stage':stage,'error_type':type(error).__name__}}
    finally:
        with contextlib.suppress(Exception):await page.close()
    return {'candidate_rules_checked':len(rows)}
