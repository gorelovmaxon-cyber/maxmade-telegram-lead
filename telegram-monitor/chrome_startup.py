"""Prepare an interactive-user Windows launch, with no credentials or TinyFish.

Automatic bootstrap verifies the existing local login; it never logs in or joins.
Continuous capture stops on guard failure. There is no automatic history recovery.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess

import chrome_pilot

ROOT=Path(__file__).resolve().parent
TASK='MaxMade-LocalChrome-Reader'


async def activate(page,context,state,automatic=False):
    if state.get('multi_source_requested'):
        reader=state.get('multi_reader')
        if automatic and reader:
            current=reader.metadata()
            if current.get('runtime_alive'):
                # A delayed supervisor request cannot reset independently
                # recovering sources or clear an owner's identity pause.
                return {**current,'automatic_restart_skipped':'source_runtime_owns_recovery'}
        import multi_source
        try:return await multi_source.activate(page,context,state)
        except Exception as error:
            # A failed read-only bot handshake must not destroy the logged-in browser.
            return {'phase':'startup_needs_local_attention','capture_running':False,
                    'delivery_running':False,'account_verified':False,'source_verified':False,
                    'multi_source':False,'runtime_alive':False,'error_type':type(error).__name__,
                    'failure_class':'startup_transient','startup_stage':'multi_source_start'}
    stage='stop_previous_capture'
    try:
        if state.get('task') and not state['task'].done():await chrome_pilot.stop(state)
        stage='check_login'
        if await page.locator('input[type="tel"],input[type="password"],#auth-pages:not(.hide) .qr-canvas').count():
            return {'phase':'login_required','capture_running':False,'account_verified':False,
                    'source_verified':False,'failure_class':'manual_attention'}
        stage='verify_account_before_navigation'
        await chrome_pilot.own_settings(page,state)
        stage='navigate_source'
        opened=await chrome_pilot.dispatch(page,context,'source',state)
        if opened.get('phase')!='source_open_unverified':raise ValueError('Source unavailable')
        # Wait for the actual group DOM, not the initial page shell.
        await page.locator('.chat.active .topbar .person-avatar[data-peer-id="-1513982592"]').first.wait_for(timeout=30000)
        for attempt in range(3):
            stage='verify_panels'
            checked=await chrome_pilot.source_profile(page,state)
            if not checked['source_verified']:raise ValueError('Membership not verified')
            stage='verify_same_document_identity'
            try:
                await chrome_pilot.verify_live(page,state)
                break
            except chrome_pilot.BrowserAccessError:
                if attempt==2:raise
                # Recheck harmless panels only; no auth/join or repeat navigation.
                await page.wait_for_timeout(350)
        stage='arm_capture'
        result=await chrome_pilot.arm(page,state,max_seconds=None)
        if state.get('manual_join_mode'):
            return {**result,'launch_mode':'continuous_with_manual_join_tabs','window_action':'keep_visible',
                    'automatic_reconnect':False,'windows_login_verified':False}
        stage='minimize_window'
        cdp=await context.new_cdp_session(page)
        try:
            wid=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            await cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'minimized'}})
        finally:await cdp.detach()
        return {**result,'launch_mode':'continuous_until_guard_failure_or_stop','window_action':'minimize',
                'automatic_reconnect':False,'windows_login_verified':False}
    except Exception as error:
        await chrome_pilot.stop(state,'startup_needs_local_attention')
        return {'phase':'startup_needs_local_attention','capture_running':False,'delivery_running':False,
                'error_type':type(error).__name__,'startup_stage':stage,
                'access_reason':str(error) if isinstance(error,chrome_pilot.BrowserAccessError) else None,
                'source_navigation':state.get('source_navigation'),
                'automatic_reconnect':False,'windows_login_verified':False,
                'account_verified':False,'source_verified':False,
                'failure_class':'manual_attention' if isinstance(error,chrome_pilot.BrowserAccessError) or stage=='minimize_window' else 'startup_transient'}


def scheduled_task(action):
    if os.name!='nt':raise RuntimeError('Windows only')
    env=dict(os.environ,MONITOR_CHROME_ROOT=str(ROOT),MONITOR_CHROME_TASK=TASK,MONITOR_CHROME_ACTION=action)
    script=r'''
$ErrorActionPreference='Stop'
$task=Get-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK -ErrorAction SilentlyContinue
$scriptPath=Join-Path $env:MONITOR_CHROME_ROOT 'automation.py'
$oldPath=Join-Path $env:MONITOR_CHROME_ROOT 'local_chrome.py'
if ($task -and -not (@($task.Actions | Where-Object { $_.Arguments -and ($_.Arguments.Contains($scriptPath) -or $_.Arguments.Contains($oldPath)) }).Count)) { throw 'Different task owns this name' }
switch ($env:MONITOR_CHROME_ACTION) {
 'install' {
  $python=Join-Path $env:MONITOR_CHROME_ROOT '.venv\Scripts\pythonw.exe'
  $start=New-ScheduledTaskAction -Execute $python -Argument ('"'+$scriptPath+'" run') -WorkingDirectory $env:MONITOR_CHROME_ROOT
  $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
  $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK -Action $start -Principal $principal -Trigger $trigger -Settings $settings -Description 'Local Chrome supervisor; bounded recovery; manual stop respected; no TinyFish.' -Force | Out-Null
  Disable-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK | Out-Null
 }
 'enable' { Enable-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK | Out-Null }
 'disable' { Disable-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK | Out-Null }
 'start' { Start-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK }
 'stop' { Stop-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK }
}
$task=Get-ScheduledTask -TaskName $env:MONITOR_CHROME_TASK -ErrorAction SilentlyContinue
if ($task) {
 $info=Get-ScheduledTaskInfo -TaskName $env:MONITOR_CHROME_TASK
 [pscustomobject]@{name=$env:MONITOR_CHROME_TASK;state=[string]$task.State;enabled=[bool]$task.Settings.Enabled;last_result=$info.LastTaskResult} | ConvertTo-Json -Compress
} else { '{"installed":false}' }
'''
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=env,
        capture_output=True,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('Windows task operation failed; details suppressed')
    return json.loads(result.stdout)


def main():
    parser=argparse.ArgumentParser(description='Isolated Chrome Windows launch preparation (installs disabled)')
    parser.add_argument('action',choices=['install','status','enable','disable','start','stop'])
    args=parser.parse_args()
    try:print(json.dumps(scheduled_task(args.action)))
    except Exception as error:print(json.dumps({'result':'unconfirmed','error_type':type(error).__name__}));raise SystemExit(2)


if __name__=='__main__':main()
