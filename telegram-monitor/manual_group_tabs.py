"""Open the user's selected catalogue groups for manual joins; no capture/join clicks."""
from pathlib import Path
import json
import monitor
import chrome_pilot

GROUPS=(
    'freelead','free_chat_for_freelance','Frilanser_100','chat_frilans',
    'tilda_official_chat','smmruschats','marketologiyuga','imweek_ru',
    'bit24dev','bitrixfordevelopers','bitrixinside24','spbeventchat',
    'business_assistants','biznes_chat','businessclass_rbc','onpeak_chat',
    'odp2021','chat_kovalev','expopeople','founderjob',
)


async def open_tabs(page,context,state):
    state['manual_join_mode']=True
    # Verify the currently selected local account before opening any candidates.
    await chrome_pilot.own_settings(page,state)
    result=[];targets=[]
    for name in GROUPS:
        url='https://web.telegram.org/k/#@'+name
        existing=next((p for p in context.pages if p is not page and p.url==url),None)
        target=existing or await context.new_page()
        targets.append(target)
        record={'username':name,'url':url,'opened':False,'automated_join':False,'reader_enabled':False}
        try:
            if not existing:await target.goto(url,wait_until='commit',timeout=15000)
            record['opened']=True
        except Exception as error:record['error_type']=type(error).__name__
        result.append(record)
        out=Path(__file__).resolve().parent/'state/local-chrome/manual-group-tabs.json'
        temp=out.with_suffix('.tmp');temp.write_text(json.dumps({'at':monitor.iso(monitor.now()),'requested':20,'groups':result},indent=2),encoding='utf-8');temp.replace(out)
    first=targets[0] if targets else None
    if first:
        await first.bring_to_front()
        cdp=await context.new_cdp_session(first)
        try:
            window=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'windowState':'normal'}})
        finally:await cdp.detach()
    return {'manual_group_tabs_opened':sum(r['opened'] for r in result),'automated_joins':0}
