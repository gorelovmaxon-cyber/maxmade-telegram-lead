"""Host subtree working set and available RAM; no command lines or credentials."""
import json
import os
import subprocess
import monitor


def measure(pid):
    if os.name!='nt':return {'at':monitor.iso(monitor.now()),'measurement':'unsupported_platform'}
    script='''$nodes=Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId
$ids=[System.Collections.Generic.HashSet[int]]::new()
[void]$ids.Add(ROOT_PID)
do {$count=$ids.Count; foreach($node in $nodes){if($ids.Contains([int]$node.ParentProcessId)){[void]$ids.Add([int]$node.ProcessId)}}} while($ids.Count -gt $count)
$ws=0; $processes=0
foreach($item in $ids){$proc=Get-Process -Id $item -ErrorAction SilentlyContinue; if($proc){$ws+=$proc.WorkingSet64;$processes++}}
$memory=Get-CimInstance Win32_OperatingSystem
$apps=@(Get-Process -Name ChatGPT,codex -ErrorAction SilentlyContinue)
@{host_tree_working_set_bytes=$ws;process_count=$processes;app_process_count=$apps.Count;available_physical_bytes=[long]$memory.FreePhysicalMemory*1024;total_physical_bytes=[long]$memory.TotalVisibleMemorySize*1024} | ConvertTo-Json -Compress
'''.replace('ROOT_PID',str(int(pid)))
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        value=json.loads(result.stdout) if result.returncode==0 else {'measurement':'unconfirmed'}
    except Exception as error:value={'measurement':'unconfirmed','error_type':type(error).__name__}
    value['at']=monitor.iso(monitor.now())
    try:
        import operator_checks
        operator_checks.record_resources(value)
    except OSError:pass
    return value


if __name__=='__main__':
    import automation
    pid=automation.read_json(automation.STATE/'status.json').get('pid')
    print(json.dumps(measure(pid)))
