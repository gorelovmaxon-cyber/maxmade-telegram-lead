"""Evidence gate; accepting a stage never enables or joins another source."""
import argparse
import contextlib
import hashlib
import json
import re
from pathlib import Path
import automation
import monitor
import source_registry as registry


def evaluate(stage,report,host,trials,tests_ok,resources):
    reasons=[];rows=[s for s in report.get('sources',[]) if s['enabled']]
    if stage not in (2,5,10,20) or len(rows)!=stage:reasons.append('enabled_source_count_differs')
    states={s['username']:s for s in host.get('source_states',[])}
    for row in rows:
        name=row['username'];state=states.get(name,{})
        if state.get('phase')!='reading' or not state.get('capture_running') or automation.age(state.get('last_live_check')) is None or automation.age(state.get('last_live_check'))>15:
            reasons.append(name+':not_currently_reading')
        if automation.age(state.get('last_web_transport')) is None or automation.age(state.get('last_web_transport'))>60:reasons.append(name+':transport_unconfirmed')
        if row['observed_seconds']<300:reasons.append(name+':less_than_five_observed_minutes')
        if not row.get('new_messages_during_observation',row['events_during_observation']):reasons.append(name+':no_real_event_during_observation')
        if any(g['end_at'] is None for g in row['gaps']):reasons.append(name+':open_gap')
        if row['access']!='joined' or row['rules_state']!='reviewed':reasons.append(name+':access_or_rules_unverified')
    required={'close','reload','context_offline_online','operator_stop_start_and_host_restart'}
    passed=set()
    for trial in trials.get('trials',[]):
        if len(trial.get('before',{}).get('sources',[]))!=stage or trial.get('result')!='recovered':continue
        case=trial['case']
        if case in ('close','reload') and not trial.get('all_other_samples_live'):continue
        if case=='context_offline_online' and not trial.get('both_paused'):continue
        if case=='operator_stop_start_and_host_restart' and not (trial.get('fully_stopped') and trial.get('stayed_stopped')):continue
        passed.add(case)
    for case in sorted(required-passed):reasons.append('missing_live_trial:'+case)
    if not tests_ok:reasons.append('final_tests_unconfirmed')
    if resources.get('available_physical_bytes',0)<1500000000:reasons.append('insufficient_measured_free_memory')
    if automation.age(resources.get('at')) is None or automation.age(resources.get('at'))>120:reasons.append('memory_measurement_stale')
    return {'stage':stage,'technical_stage_accepted':not reasons,'reasons':reasons,
            'direct_order_accepted':False,'full_stream_completeness_proven':False,'automatic_expansion':False,
            'windows_login_accepted':False,'codex_closed_accepted':False,'observation_24h_accepted':False}


def run(stage,tests,accept=False):
    cfg=automation.config()
    with contextlib.closing(registry.connect()) as db:
        with contextlib.closing(__import__('sqlite3').connect(cfg.db.as_uri()+'?mode=ro',uri=True)) as business:report=registry.report(db,business)
        test_text=tests.read_text(encoding='utf-8',errors='replace')
        tests_ok=bool(re.search(r'Ran [0-9]+ tests? in .*\n\s*\nOK\s*$',test_text))
        value=evaluate(stage,report,automation.read_json(automation.STATE/'status.json'),
            automation.read_json(automation.STATE/'two-source-trials.json'),tests_ok,
            automation.read_json(automation.STATE/'resource-sample.json'))
        value['at']=monitor.iso(monitor.now());value['test_log_sha256']=hashlib.sha256(tests.read_bytes()).hexdigest()
        value['code_sha256']={name:hashlib.sha256((automation.ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
            for name in ('multi_source.py','source_registry.py','automation.py','browser_ingest.py','browser_capture.py')}
        if accept and value['technical_stage_accepted']:
            with db:db.execute('insert or replace into stage_acceptance(stage,at,evidence) values(?,?,?)',(stage,value['at'],json.dumps(value)))
        value['recorded']=bool(accept and value['technical_stage_accepted'])
        automation.write_json(automation.STATE/'stage-review.json',value)
    print(json.dumps(value))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',type=int,choices=[2,5,10,20]);p.add_argument('--tests',type=Path,required=True);p.add_argument('--accept',action='store_true')
    a=p.parse_args();run(a.stage,a.tests,a.accept)
