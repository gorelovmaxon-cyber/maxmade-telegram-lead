"""Isolated local Chrome host. No TinyFish, Telethon login or startup task.

Control commands and status stay in ignored state/local-chrome/. Login is manual.
Opening a browser never arms the separate, explicitly requested bounded pilot.
"""
from __future__ import annotations
import argparse,asyncio,datetime,json,os,subprocess,time,importlib
from pathlib import Path
from urllib.parse import urlsplit
import monitor

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'state/local-chrome'
PROFILE=STATE/'profile'
HOST_ACTIVE=False

def lifecycle(event,**values):
    """Small metadata-only browser log, including why the host exits."""
    STATE.mkdir(parents=True,exist_ok=True)
    file=STATE/'browser-lifecycle.jsonl'
    if file.exists() and file.stat().st_size>1024*1024:
        file.replace(STATE/'browser-lifecycle.previous.jsonl')
    record={'at':monitor.iso(monitor.now()),'pid':os.getpid(),'event':event,**values}
    with file.open('a',encoding='utf-8') as out:out.write(json.dumps(record)+'\n')


def choose_open_page(context,current):
    """Closing one tab must not close the other manual group tabs."""
    pages=[candidate for candidate in context.pages if not candidate.is_closed()]
    if current in pages:return current
    return pages[0] if pages else None

def private_directory(path: Path):
    path.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        sid=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True).strip().split(',')[-1].strip('"')
        subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)

def status(**values):
    STATE.mkdir(parents=True,exist_ok=True)
    file=STATE/'status.json'
    try:previous=json.loads(file.read_text(encoding='utf-8'))
    except (OSError,ValueError):previous={}
    if previous.get('pid')!=os.getpid():previous={}
    record={**previous,**values,'pid':os.getpid(),'at':datetime.datetime.now(monitor.UTC).isoformat(timespec='seconds')}
    tmp=STATE/('status-'+str(os.getpid())+'-'+str(time.time_ns())+'.tmp')
    tmp.write_text(json.dumps(record,indent=2),encoding='utf-8')
    for attempt in range(5):
        try:tmp.replace(file);break
        except PermissionError:
            if attempt==4:tmp.unlink(missing_ok=True);return
            time.sleep(.02)

def command(action: str):
    STATE.mkdir(parents=True,exist_ok=True)
    if not host_running():
        print('{"phase":"host_not_running","capture_running":false}')
        return
    target=STATE/'command.json'
    if target.exists():raise RuntimeError('Command pending; wait for acknowledgment')
    ident=str(time.time_ns());tmp=STATE/'command.tmp'
    tmp.write_text(json.dumps({'id':ident,'action':action}),encoding='utf-8');tmp.replace(target)
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        try:record=json.loads((STATE/'status.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):record={}
        if record.get('last_command')==ident:
            print(json.dumps({k:record.get(k) for k in ('phase','account_username','account_verified','source_verified','membership_required','capture_running','error_type')}))
            return
        time.sleep(.2)
    print(json.dumps({'queued':action,'id':ident,'acknowledged':False}))

def host_running():
    try:
        with monitor.single_instance(STATE/'host.lock'):return False
    except RuntimeError:return True

async def inspect(page):
    url=urlsplit(page.url)
    if url.scheme!='https' or url.hostname!='web.telegram.org' or not url.path.startswith('/k/'):
        return dict(page='other',login_required=None)
    # Only structural counts; no page text, credentials, contacts or message bodies.
    data=await page.evaluate('''()=>({
      bubbles:document.querySelectorAll('.bubble[data-mid][data-peer-id][data-timestamp]').length,
      chats:document.querySelectorAll('.chatlist-chat').length,
      settings:document.querySelectorAll('.sidebar-left-section-content').length,
      login_inputs:document.querySelectorAll('input[type="tel"],input[type="password"],input[name="phone_number"]').length,
      auth_pages:document.querySelectorAll('#auth-pages:not(.hide),.auth-pages:not(.hide)').length,
      ready:document.readyState
    })''')
    return dict(page='telegram_web_k',structure=data,login_required=True if data['login_inputs'] else None)

async def host(continuous=False,manual_batch=False,manual_next=False,check_selected=False):
    global HOST_ACTIVE
    from playwright.async_api import async_playwright
    private_directory(STATE)
    assert PROFILE.resolve().is_relative_to(STATE.resolve())
    with monitor.single_instance(STATE/'host.lock'):
        HOST_ACTIVE=True
        lifecycle('host_start',continuous=continuous,manual_batch=manual_batch)
        status(phase='starting',account_verified=False,source_verified=False,capture_running=False,delivery_running=False,autostart_enabled=False,uses_tinyfish=False)
        async with async_playwright() as playwright:
            context=await playwright.chromium.launch_persistent_context(str(PROFILE),channel='chrome',headless=False,
                accept_downloads=False,viewport=None,locale='ru-RU',timezone_id='Europe/Moscow')
            tracked={}
            def track(candidate):
                if id(candidate) in tracked:return
                ident=len(tracked)+1;tracked[id(candidate)]=ident
                lifecycle('page_created',page=ident)
                candidate.on('close',lambda:lifecycle('page_closed',page=ident))
                candidate.on('crash',lambda:lifecycle('page_crashed',page=ident))
            for candidate in context.pages:track(candidate)
            context.on('page',track)
            context_closed=asyncio.Event()
            def closed(*args):
                context_closed.set();lifecycle('context_closed')
            context.on('close',closed)
            page=context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto('https://web.telegram.org/k/',wait_until='domcontentloaded',timeout=45000)
            except Exception as error:status(phase='navigation_unconfirmed',error_type=type(error).__name__)
            else:status(phase='awaiting_manual_login',error_type=None)
            runtime_state={'transport_probe_required':continuous,'multi_source_requested':(STATE/'multi-source-enabled').exists()}
            def navigated(frame):
                if frame==page.main_frame:
                    runtime_state['navigation_revision']=runtime_state.get('navigation_revision',0)+1
            page.on('framenavigated',navigated)
            if continuous and not context_closed.is_set():
                import chrome_startup
                status(**await chrome_startup.activate(page,context,runtime_state))
            if manual_batch:
                import batch_group_tabs
                try:status(**await batch_group_tabs.open_batch(page,context,runtime_state,next_batch=manual_next))
                except Exception as error:
                    lifecycle('manual_batch_incomplete',error_type=type(error).__name__)
                    status(phase='manual_batch_incomplete',error_type=type(error).__name__)
            if check_selected:
                import verify_selected_groups
                status(**await verify_selected_groups.run(page,context,runtime_state))
            exit_reason='all_tabs_closed'
            while True:
                if continuous and (STATE/'operator-stop').exists():
                    exit_reason='operator_stop_marker';break
                if context_closed.is_set():
                    exit_reason='context_closed';status(phase='browser_closed',capture_running=False,delivery_running=False,
                        runtime_alive=False,context_closed=True);break
                selected=choose_open_page(context,page)
                if selected is None:
                    if runtime_state.get('multi_reader'):runtime_state['multi_reader'].on_context_closed()
                    status(phase='browser_closed',capture_running=False,delivery_running=False,runtime_alive=False);break
                if selected is not page:
                    multi=runtime_state.get('multi_reader')
                    if multi:
                        page=selected;page.on('framenavigated',navigated)
                        lifecycle('control_tab_rebound',page=tracked.get(id(page)),remaining=len(context.pages))
                        continue
                    was_reading=bool(runtime_state.get('task') and not runtime_state['task'].done())
                    if was_reading:
                        import chrome_pilot
                        await chrome_pilot.stop(runtime_state,'source_disarmed')
                    for key in ('account','own','settings','source_profile','source_link'):
                        runtime_state.pop(key,None)
                    runtime_state['member']=False;runtime_state['contract_verified']=False
                    page=selected;page.on('framenavigated',navigated)
                    lifecycle('control_tab_rebound',page=tracked.get(id(page)),remaining=len(context.pages))
                    status(phase='source_disarmed' if was_reading else 'manual_browser_open',
                        failure_class='manual_attention',account_verified=False,source_verified=False,
                        capture_running=False,delivery_running=False,error_type=None)
                file=STATE/'command.json'
                if file.exists():
                    req=json.loads(file.read_text(encoding='utf-8'));file.unlink()
                    action=req.get('action')
                    try:
                        if action in ('auto','auto_if_needed'):
                            runtime_state['transport_probe_required']=True
                            import chrome_startup
                            status(**await chrome_startup.activate(page,context,runtime_state,automatic=action=='auto_if_needed'),last_command=req['id'])
                        elif action.startswith('runtime_'):
                            if action=='runtime_stop':(STATE/'operator-stop').write_text(monitor.iso(monitor.now()),encoding='utf-8')
                            import local_chrome_runtime
                            importlib.reload(local_chrome_runtime)
                            result=await local_chrome_runtime.dispatch(page,context,action[8:],runtime_state)
                            status(**result,last_command=req['id'])
                        elif action=='inspect':status(phase='inspected',inspection=await inspect(page),last_command=req['id'])
                        elif action=='candidates':
                            import browser_candidates
                            result=await browser_candidates.inspect(context)
                            from automation import write_json
                            write_json(STATE/'candidate-access.json',{'at':monitor.iso(monitor.now()),'candidates':result})
                            status(candidate_access=result,last_command=req['id'])
                        elif action=='reload':
                            if runtime_state.get('multi_reader'):
                                first=runtime_state['multi_reader'].sessions[0]
                                await first.page.reload(wait_until='domcontentloaded',timeout=30000)
                                status(last_command=req['id']);continue
                            if runtime_state.get('task'):
                                import chrome_pilot
                                await chrome_pilot.stop(runtime_state,'page_reloading')
                            runtime_state.pop('account',None);runtime_state['member']=False
                            await page.reload(wait_until='domcontentloaded',timeout=30000)
                            status(phase='reloaded_unverified',failure_class='page_reloaded',capture_running=False,
                                account_verified=False,source_verified=False,last_command=req['id'])
                        elif action in ('minimize','restore'):
                            cdp=await context.new_cdp_session(page)
                            try:
                                window=(await cdp.send('Browser.getWindowForTarget'))['windowId']
                                await cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'windowState':'minimized' if action=='minimize' else 'normal'}})
                            finally:await cdp.detach()
                            status(last_command=req['id'],window_action=action)
                        elif action in ('offline','online'):
                            await context.set_offline(action=='offline')
                            status(network_test_action=action,last_command=req['id'])
                        elif action=='close':
                            exit_reason='explicit_close_command'
                            # This explicit control command is a deliberate stop,
                            # including when a supervisor owns the browser.
                            (STATE/'operator-stop').write_text(monitor.iso(monitor.now()),encoding='utf-8')
                            if runtime_state.get('multi_reader'):await runtime_state['multi_reader'].close()
                            if runtime_state.get('task'):
                                import chrome_pilot
                                await chrome_pilot.stop(runtime_state,'browser_closing')
                            status(phase='closing',last_command=req['id']);break
                        else:status(phase='command_rejected',last_command=req.get('id'))
                    except Exception as error:status(phase='command_failed',error_type=type(error).__name__,last_command=req.get('id'))
                await asyncio.sleep(.5)
            if runtime_state.get('task'):
                import chrome_pilot
                await chrome_pilot.stop(runtime_state,'browser_closed')
            if runtime_state.get('multi_reader'):await runtime_state['multi_reader'].close()
            lifecycle('host_cleanup',reason=exit_reason)
            await context.close()
            status(phase='stopped',exit_reason=exit_reason,capture_running=False,delivery_running=False,runtime_alive=False)

def main():
    parser=argparse.ArgumentParser(description='Dedicated local Chrome profile for a bounded monitor prototype')
    runtime_actions=['source','verify','profile','arm','stop','diagnose','settings','drain','open_groups']
    parser.add_argument('action',choices=['open','batch5','batch5-next','check-selected','run','auto','status','inspect','candidates','reload','minimize','restore','close','offline','online',*runtime_actions,*('runtime_'+a for a in runtime_actions)])
    args=parser.parse_args()
    if args.action=='status':
        record=json.loads((STATE/'status.json').read_text(encoding='utf-8')) if (STATE/'status.json').exists() else {'phase':'not_started'}
        record['host_running']=host_running()
        if not record['host_running']:record.update(capture_running=False,delivery_running=False)
        print(json.dumps(record,indent=2));return
    if args.action in ('open','run','batch5','batch5-next','check-selected'):
        try:asyncio.run(host(continuous=args.action=='run',manual_batch=args.action in ('batch5','batch5-next'),manual_next=args.action=='batch5-next',check_selected=args.action=='check-selected'))
        except Exception as error:
            if HOST_ACTIVE:
                status(phase='host_error',error_type=type(error).__name__,capture_running=False,delivery_running=False)
            # Do not print Playwright exceptions: they may contain private URLs.
        return
    command('runtime_'+args.action if args.action in runtime_actions else args.action)
if __name__=='__main__':main()
