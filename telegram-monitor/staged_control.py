"""Local observation and source-review operations; no Telegram content or secrets printed."""
import argparse
import contextlib
import json
import source_registry as registry


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['observe','observation','operator-status','closed-interval'])
    parser.add_argument('--start');parser.add_argument('--end')
    args=parser.parse_args()
    if args.action=='closed-interval':
        if not args.start or not args.end:parser.error('--start and --end required')
        import operator_checks
        print(json.dumps(operator_checks.record_closed_interval(args.start,args.end)));raise SystemExit(0)
    if args.action=='operator-status':
        import operator_checks
        print(json.dumps(operator_checks.status()));raise SystemExit(0)
    with contextlib.closing(registry.connect()) as db:
        if args.action=='observe':registry.start_observation(db,'20261106 staged 2 -> 5 -> 10 -> 20')
        print(json.dumps(registry.observation_report(db)))
