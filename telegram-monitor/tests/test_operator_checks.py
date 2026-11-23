import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import monitor
import operator_checks as checks


class OperatorChecksTests(unittest.TestCase):
    def test_closed_interval_converts_offset_to_utc_before_sql_event_query(self):
        import datetime as dt
        import json
        import source_registry as registry
        with tempfile.TemporaryDirectory() as folder,patch('automation.STATE',Path(folder)):
            base=Path(folder);dbfile=base/'sources.sqlite3';original_connect=registry.connect
            with contextlib.closing(original_connect(dbfile)) as db:
                with db:db.execute("INSERT INTO browser_changes(username,message_id,version,change,observed_kind,observed_at) VALUES('fixture','1','v1','first_ingest','new','2026-11-09T09:06:00+00:00')")
            (base/'closed-codex-check.json').write_text(json.dumps({'expected_sources':['fixture']}),encoding='utf-8')
            start=dt.datetime(2026,11,9,9,5,tzinfo=dt.timezone.utc)
            samples=[]
            for seconds in range(0,301,60):
                stamp=monitor.iso(start+dt.timedelta(seconds=seconds))
                samples.append(json.dumps({'at':stamp,'sources':[{'username':'fixture','phase':'reading','last_live_check':stamp,'last_web_transport':stamp}]}))
            (base/'closed-codex-samples.jsonl').write_text('\n'.join(samples),encoding='utf-8')
            with patch.object(registry,'connect',lambda:original_connect(dbfile)):
                result=checks.record_closed_interval('2026-11-09T12:05:00+03:00','2026-11-09T12:10:00+03:00')
            self.assertEqual(result['new_originals_in_interval'],{'fixture':1})
            self.assertTrue(result['continuous_source_checks_accepted'])
    def test_closed_ui_attestation_is_separate_from_background_process_count_and_continuity(self):
        import datetime as dt
        start=monitor.now()-dt.timedelta(minutes=6);end=start+dt.timedelta(minutes=5)
        samples=[]
        for seconds in range(0,301,60):
            stamp=monitor.iso(start+dt.timedelta(seconds=seconds))
            samples.append({'at':stamp,'app_process_count':14,'sources':[
                {'username':'fixture','phase':'reading','last_live_check':stamp,'last_web_transport':stamp}]})
        changes=[{'username':'fixture','observed_at':samples[1]['at']}]
        result=checks.analyze_closed_interval(monitor.iso(start),monitor.iso(end),['fixture'],samples,changes)
        self.assertTrue(result['continuous_source_checks_accepted'])
        samples[3]['sources'][0]['phase']='manual_attention'
        result=checks.analyze_closed_interval(monitor.iso(start),monitor.iso(end),['fixture'],samples,changes)
        self.assertTrue(result['background_reading_observed'])
        self.assertFalse(result['continuous_source_checks_accepted'])
        self.assertEqual(len(result['failed_samples']),1)
        self.assertFalse(result['full_stream_completeness_proven'])
    def test_signed_owner_test_is_bound_to_sent_message_and_never_touches_business_db(self):
        class Api:
            def __init__(self):self.calls=[]
            def call(self,method,payload,**kwargs):
                self.calls.append((method,payload));return {'message_id':70}
        with tempfile.TemporaryDirectory() as folder,patch('automation.STATE',Path(folder)):
            cfg=monitor.Config(Path(folder)/'business.sqlite3',owner_id=42,token='fixture');api=Api()
            card=checks.prepare(cfg,api);self.assertEqual(card['state'],'sent')
            checks.prepare(cfg,api);self.assertEqual(len(api.calls),1)
            data=api.calls[0][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
            update={'callback_query':{'id':'fixture','data':data,'from':{'id':42},'message':{'message_id':71,'chat':{'id':42}}}}
            self.assertFalse(checks.handle_callback(cfg,update,api))
            update['callback_query']['message']['message_id']=70
            update['callback_query']['from']['id']=43
            self.assertFalse(checks.handle_callback(cfg,update,api))
            update['callback_query']['from']['id']=42
            self.assertTrue(checks.handle_callback(cfg,update,api))
            with contextlib.closing(checks.connect()) as db:first=db.execute('select accepted_at from cards').fetchone()[0]
            self.assertTrue(checks.handle_callback(cfg,update,api))
            with contextlib.closing(checks.connect()) as db:self.assertEqual(db.execute('select accepted_at from cards').fetchone()[0],first)
            self.assertFalse(cfg.db.exists())

    def test_unknown_send_is_not_repeated(self):
        class Api:
            def __init__(self):self.calls=0
            def call(self,*args,**kwargs):self.calls+=1;raise monitor.UnknownDelivery()
        with tempfile.TemporaryDirectory() as folder,patch('automation.STATE',Path(folder)):
            cfg=monitor.Config(Path(folder)/'business.sqlite3',owner_id=42,token='fixture');api=Api()
            with self.assertRaises(monitor.UnknownDelivery):checks.prepare(cfg,api)
            self.assertEqual(checks.prepare(cfg,api)['state'],'uncertain');self.assertEqual(api.calls,1)
