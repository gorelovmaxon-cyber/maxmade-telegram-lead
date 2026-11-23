"""Manage the Windows pilot through a scheduled supervisor with local health records."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
if sys.stdout is None or sys.stderr is None:
    STATE.mkdir(exist_ok=True)
    if sys.stdout is None:
        sys.stdout = (STATE / "supervisor.stdout.log").open("a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = (STATE / "supervisor.stderr.log").open("a", encoding="utf-8", buffering=1)

import monitor
import runtime_status as health

TASK_NAME = "MaxMade-Telegram-Pilot"
STOP_FILE = STATE / "pilot.stopped"


def processes(task: str, stop: bool = False) -> list[int]:
    env = dict(os.environ, MONITOR_PILOT_SCRIPT=str(ROOT / (task + ".py")), MONITOR_PILOT_TASK=task)
    script = r"""
$items = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and
    $_.CommandLine.Contains($env:MONITOR_PILOT_SCRIPT) -and
    ($env:MONITOR_PILOT_TASK -ne 'pilot' -or $_.CommandLine -match '\s+supervise(?:\s|$)')
})
"""
    if stop:
        script += """
foreach ($item in $items) {
    $current = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $item.ProcessId)
    if ($current -and $current.CommandLine -and $current.CommandLine.Contains($env:MONITOR_PILOT_SCRIPT)) {
        Stop-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
    }
}
'[]'
"""
    else:
        script += "ConvertTo-Json -Compress -InputObject @($items | ForEach-Object { $_.ProcessId })"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, capture_output=True, text=True, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout.strip() or "[]")


def scheduled_task(action: str) -> dict:
    env = dict(os.environ, MONITOR_PILOT_ROOT=str(ROOT), MONITOR_PILOT_NAME=TASK_NAME,
               MONITOR_PILOT_ACTION=action)
    script = r"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $env:MONITOR_PILOT_NAME -ErrorAction SilentlyContinue
$pilotScript = Join-Path $env:MONITOR_PILOT_ROOT 'pilot.py'
if ($task -and -not (@($task.Actions | Where-Object { $_.Arguments -and $_.Arguments.Contains($pilotScript) }).Count)) {
    throw 'A different task already owns this name'
}
if ($env:MONITOR_PILOT_ACTION -eq 'install') {
    $python = Join-Path $env:MONITOR_PILOT_ROOT '.venv\Scripts\pythonw.exe'
    $action = New-ScheduledTaskAction -Execute $python -Argument ('-u "' + $pilotScript + '" supervise') -WorkingDirectory $env:MONITOR_PILOT_ROOT
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $triggers = @(
        (New-ScheduledTaskTrigger -AtLogOn -User $user),
        (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
    )
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $env:MONITOR_PILOT_NAME -Action $action -Principal $principal -Trigger $triggers -Settings $settings -Description 'Read-only Telegram pilot on this Windows PC; no customer messages or LLM calls.' -Force | Out-Null
} elseif ($env:MONITOR_PILOT_ACTION -eq 'stop' -and $task) {
    Disable-ScheduledTask -TaskName $env:MONITOR_PILOT_NAME | Out-Null
    Stop-ScheduledTask -TaskName $env:MONITOR_PILOT_NAME -ErrorAction SilentlyContinue
} elseif ($env:MONITOR_PILOT_ACTION -eq 'start' -and $task) {
    Enable-ScheduledTask -TaskName $env:MONITOR_PILOT_NAME | Out-Null
    Start-ScheduledTask -TaskName $env:MONITOR_PILOT_NAME
}
$task = Get-ScheduledTask -TaskName $env:MONITOR_PILOT_NAME -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $env:MONITOR_PILOT_NAME
    @{registered=$true;state=[string]$task.State;last_result=$info.LastTaskResult} | ConvertTo-Json -Compress
} else {
    @{registered=$false} | ConvertTo-Json -Compress
}
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        # Keep scheduler errors local: no command line or account details in chat output.
        STATE.mkdir(exist_ok=True)
        (STATE / "scheduler-error.log").write_text(result.stderr, encoding="utf-8")
        raise RuntimeError("Scheduler action failed; see state/scheduler-error.log")
    return json.loads(result.stdout.strip())


def launch(task: str, command: str) -> subprocess.Popen:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise ValueError("Local .venv is missing; see README.md")
    STATE.mkdir(exist_ok=True)
    with (STATE / (task + "-pilot.stdout.log")).open("ab") as output, \
            (STATE / (task + "-pilot.stderr.log")).open("ab") as error:
        return subprocess.Popen([str(python), "-u", str(ROOT / (task + ".py")), command],
                                cwd=ROOT, stdout=output, stderr=error, stdin=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)


def supervise() -> None:
    with monitor.single_instance(STATE / "pilot-supervisor.lock"), health.lifecycle("supervisor"):
        children = {}
        retry_at = {task: 0.0 for task in ("reader", "monitor")}
        failures = {task: 0 for task in retry_at}
        started_at = {}
        try:
            while not STOP_FILE.exists():
                for task, command in (("reader", "run"), ("monitor", "run-live")):
                    child = children.get(task)
                    if child is not None and child.poll() is not None:
                        if time.monotonic() - started_at[task] > 120:
                            failures[task] = 0
                        failures[task] += 1
                        delay = min(60, 2 ** min(failures[task], 6))
                        health.event("supervisor", "child_exited", worker=task, child_pid=child.pid,
                                     exit_code=child.returncode, restart_after_seconds=delay)
                        retry_at[task] = time.monotonic() + delay
                        del children[task]
                    if task not in children and time.monotonic() >= retry_at[task]:
                        # A manual worker may already hold the application's lock.
                        if processes(task):
                            retry_at[task] = time.monotonic() + 30
                            continue
                        children[task] = launch(task, command)
                        started_at[task] = time.monotonic()
                        health.event("supervisor", "child_started", worker=task, child_pid=children[task].pid)
                health.update("supervisor", state="running", worker_pids={task: child.pid for task, child in children.items()}, restart_counts=failures)
                time.sleep(5)
        finally:
            for task in children:
                processes(task, stop=True)


def worker_status(task: str) -> dict:
    ids = processes("pilot" if task == "supervisor" else task)
    try:
        record = json.loads((STATE / (task + "-health.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    last_network_ok = record.get("last_network_ok")
    fresh = False
    if last_network_ok:
        age = monitor.now() - monitor.dt.datetime.fromisoformat(last_network_ok)
        fresh = 0 <= age.total_seconds() <= 90
    heartbeat_fresh = False
    if record.get("updated_at"):
        age = monitor.now() - monitor.dt.datetime.fromisoformat(record["updated_at"])
        heartbeat_fresh = 0 <= age.total_seconds() <= 90
    return {"task": task, "running": bool(ids), "process_ids": ids,
            "heartbeat_confirmed_recently": bool(ids) and record.get("pid") in ids and heartbeat_fresh,
            "network_confirmed_recently": None if task == "supervisor" else bool(ids) and record.get("pid") in ids and fresh and record.get("state") == "connected",
            "health": record}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "start", "stop", "status", "supervise"), default="status", nargs="?")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This launcher is for Windows; run reader.py and monitor.py directly elsewhere.")
    STATE.mkdir(exist_ok=True)
    if args.action == "supervise":
        supervise()
        return 0
    if args.action in ("install", "start"):
        STOP_FILE.unlink(missing_ok=True)
        status = scheduled_task("status")
        if args.action == "install" or not status["registered"]:
            scheduled_task("install")
        status = scheduled_task("start")
    elif args.action == "stop":
        STOP_FILE.write_text(health.stamp(), encoding="utf-8")
        status = scheduled_task("stop")
        processes("pilot", stop=True)
        for task in ("reader", "monitor"):
            processes(task, stop=True)
        health.event("pilot", "manual_stop")
    else:
        status = scheduled_task("status")
    print(json.dumps({"scheduled_task": TASK_NAME, **status}))
    for task in ("supervisor", "reader", "monitor"):
        print(json.dumps(worker_status(task)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Pilot action failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
