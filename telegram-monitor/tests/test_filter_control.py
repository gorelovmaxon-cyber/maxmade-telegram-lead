import contextlib
import asyncio
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import automation
import filter_control as control
import filter_policy as policy
import monitor

class HotSwitchTests(unittest.TestCase):
    def test_dispatch_resolves_version_after_reloading_old_live_constant(self):
        import local_chrome_runtime as runtime
        original=runtime.importlib.reload
        policy.V3=policy.PREVIOUS_V3
        try:
            with patch.object(runtime.importlib,'reload',side_effect=lambda module:original(module) if module is policy else module),patch.object(control,'hot_apply',return_value={'ok':True}) as apply:
                result=asyncio.run(runtime.dispatch(None,None,'filter_v3',{'multi_reader':object()}))
            self.assertTrue(result['ok'])
            self.assertEqual(apply.call_args.args[1],'maxmadepro-2026-11-09-v3.3')
        finally:original(policy)
    def test_declined_switch_does_not_report_capture_failure(self):
        import local_chrome_runtime as runtime
        reader=SimpleNamespace(metadata=lambda:{'phase':'reading','capture_running':True,'account_verified':True})
        with (patch.object(control,'hot_apply',side_effect=ValueError('gated')),
              patch.object(runtime.importlib,'reload',side_effect=lambda module:module)):
            result=asyncio.run(runtime.dispatch(None,None,'filter_v3',{'multi_reader':reader}))
        self.assertTrue(result['capture_running'])
        self.assertEqual(result['phase'],'reading')
        self.assertEqual(result['filter_switch']['result'],'not_applied')
    def test_switch_preserves_process_sessions_queue_and_sole_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state';state.mkdir()
            for file in control.INPUTS:
                destination=root/file;destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(control.ROOT/file,destination)
            (root/'.env').write_text('MONITOR_OWNER_ID=42\n',encoding='utf-8')
            with contextlib.closing(monitor.connect(root/'business.sqlite')) as db:
                cfg=monitor.Config(root/'business.sqlite',owner_id=42,source_ids=(-1,))
                monitor.ingest(db,cfg,'-1','1','Нужен сайт',monitor.now(),monitor.now())
                before=tuple(db.execute('SELECT * FROM candidates').fetchone())
                sent=tuple(db.execute('SELECT * FROM deliveries').fetchone())
                from multi_source import SourceSession
                session=SourceSession({'username':'fixture','peer_id':-1},None,db,cfg,None,asyncio.Event());session.generation=4
                sessions=[session]
                reader=SimpleNamespace(db=db,cfg=cfg,sessions=sessions,metadata=lambda:{'all_sources_reading':True})
                names=('connect','classify','ingest','due_rows','render','deliver','deliver_revisions','recover_interrupted','prune')
                originals={name:getattr(monitor,name) for name in names}
                original_profile=monitor.order_profile
                try:
                    with patch.object(control,'ROOT',root),patch.object(automation,'STATE',state):
                        receipt={'code_hashes':control.code_hashes(),'tests_passed':True,'holdout_fn':0,'development_fn':0,
                          'schema':'useful-selection-v33','required_examples_passed':True,'holdout_evaluated':True,
                          'contrasts_passed':True,'format_invariance_passed':True,'positive_holdout_checked':True,'known_positive_limit_disclosed':True,
                          'development_extra_auto':0,'holdout_extra_auto':0,'development_labels_sha256':'a'*64,
                          'holdout_labels_sha256':'b'*64,'sample_sha256':'c'*64}
                        (state/'filter-validation.json').write_text(json.dumps(receipt),encoding='utf-8')
                        threads=set(threading.enumerate())
                        result=control.hot_apply(reader,policy.V3)
                        self.assertEqual(set(threading.enumerate()),threads)
                        self.assertIs(reader.sessions,sessions)
                        self.assertEqual(sessions[0].generation,4)
                        import multi_source
                        self.assertIs(sessions[0].__class__,multi_source.SourceSession)
                        self.assertEqual(automation.read_json(state/'filter-active.json')['source_methods_updated'],1)
                        self.assertEqual(tuple(db.execute('SELECT * FROM candidates WHERE id=1').fetchone()),before)
                        self.assertEqual(tuple(db.execute('SELECT * FROM deliveries WHERE candidate_id=1').fetchone()),sent)
                        self.assertEqual(result['filter_policy']['callbacks_workers_added'],0)
                        self.assertEqual(monitor.order_profile.profile()['version'],policy.V3)
                        cid=monitor.ingest(db,cfg,'-1','2','У нас есть сайт',monitor.now(),monitor.now())
                        self.assertEqual(policy.latest(db,cid)['route'],'review')
                        control.hot_apply(reader,policy.V2)
                        self.assertEqual(policy.get_meta(db,'filter_active_version'),policy.V2)
                finally:
                    for name,value in originals.items():setattr(monitor,name,value)
                    monitor.order_profile=original_profile

    def test_unverified_code_cannot_activate(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receipt.json';path.write_text(json.dumps({'code_hashes':{},'tests_passed':True}))
            with self.assertRaises(ValueError):control.validate_receipt(path)
    def test_new_filter_rejects_historical_or_unmeasured_quality_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receipt.json'
            for fields in ({'tests_passed':True,'development_fn':0,'holdout_fn':0,'labels_sha256':'a'*64},
              {'schema':'useful-selection-v32','tests_passed':True,'required_examples_passed':True,'holdout_evaluated':False}):
                path.write_text(json.dumps(dict(fields,code_hashes=control.code_hashes())),encoding='utf-8')
                with self.assertRaises(ValueError):control.validate_receipt(path)
