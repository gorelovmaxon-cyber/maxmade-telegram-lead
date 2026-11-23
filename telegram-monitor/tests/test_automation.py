import contextlib
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automation as ops
import monitor


class AutomationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=ops.journal(self.root/'operations.sqlite3')
        self.cfg=monitor.Config(self.root/'fixture.sqlite3',token='123:'+('a'*25),owner_id=42)

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_process_and_silence_cannot_substitute_for_fresh_verified_transport(self):
        stamp=monitor.iso(monitor.now())
        host=dict(phase='reading',capture_running=True,account_verified=True,source_verified=True,at=stamp,
                  last_live_check=stamp,last_web_transport=stamp)
        self.assertTrue(ops.health(host,True,{})['healthy'])
        host['last_web_transport']=monitor.iso(monitor.now()-dt.timedelta(seconds=61))
        self.assertFalse(ops.health(host,True,{})['healthy'])
        host['last_web_transport']=stamp;host['source_verified']=False
        self.assertFalse(ops.health(host,True,{})['healthy'])
        self.assertFalse(ops.health(host,False,{})['account_verified'])

    def test_outage_and_recovery_alerts_are_deduplicated_and_never_client_cards(self):
        ident=ops.begin_outage(self.db,'fixture_network')
        self.assertEqual(ops.begin_outage(self.db,'same_outage'),ident)
        ops.end_outage(self.db);ops.end_outage(self.db)
        calls=[]
        class Sink:
            def call(s,method,payload,timeout=0):
                calls.append((method,payload))
                return {'username':'uffweb_bot','message_id':123}
        ops.flush_alerts(self.db,self.cfg,Sink());ops.flush_alerts(self.db,self.cfg,Sink())
        sends=[p for m,p in calls if m=='sendMessage']
        self.assertEqual(len(sends),0)
        self.assertEqual(self.db.execute("select state from alerts where kind='recovery'").fetchone()[0],'quiet_log_only')

    def test_long_outage_warns_once_then_recovers_without_debug_details(self):
        ops.begin_outage(self.db,'connection_or_process_lost',monitor.iso(monitor.now()-dt.timedelta(minutes=6)))
        calls=[]
        class Sink:
            def call(s,method,payload,timeout=0):
                if method=='sendMessage':calls.append(payload)
                return {'username':'uffweb_bot','message_id':123}
        ops.flush_alerts(self.db,self.cfg,Sink());ops.flush_alerts(self.db,self.cfg,Sink())
        self.assertEqual(len(calls),1)
        ops.end_outage(self.db);ops.flush_alerts(self.db,self.cfg,Sink());ops.flush_alerts(self.db,self.cfg,Sink())
        self.assertEqual(len(calls),2)
        for payload in calls:
            self.assertEqual(payload['chat_id'],42)
            self.assertNotIn('UTC',payload['text']);self.assertNotIn('connection_or_process_lost',payload['text'])
            self.assertNotIn('reply_markup',payload)

    def test_current_short_outage_waits_and_intentional_stop_stays_quiet(self):
        ops.begin_outage(self.db,'fixture')
        class Sink:
            def call(s,*args,**kwargs):raise AssertionError('Short outage must not call Telegram')
        ops.flush_alerts(self.db,self.cfg,Sink())
        ops.end_outage(self.db);ops.flush_alerts(self.db,self.cfg,Sink())
        ops.begin_outage(self.db,'operator_stopped',monitor.iso(monitor.now()-dt.timedelta(hours=2)))
        ops.flush_alerts(self.db,self.cfg,Sink());ops.end_outage(self.db);ops.flush_alerts(self.db,self.cfg,Sink())

    def test_one_source_needing_attention_is_visible_while_other_source_remains_healthy(self):
        stamp=monitor.iso(monitor.now())
        host=dict(phase='reading',capture_running=True,account_verified=True,source_verified=True,at=stamp,
                  last_live_check=stamp,last_web_transport=stamp,source_states=[{'phase':'reading'},{'phase':'manual_attention'}])
        health=ops.health(host,True,{})
        self.assertTrue(health['healthy']);self.assertTrue(health['manual_action_required'])

    def test_ambiguous_send_is_not_retried_after_reopening_journal(self):
        ops.begin_outage(self.db,'fixture',monitor.iso(monitor.now()-dt.timedelta(minutes=6)))
        class Sink:
            count=0
            def call(s,method,payload,timeout=0):
                if method=='getMe':return {'username':'uffweb_bot'}
                s.count+=1;raise monitor.UnknownDelivery()
        sink=Sink();ops.flush_alerts(self.db,self.cfg,sink)
        self.db.close();self.db=ops.journal(self.root/'operations.sqlite3')
        ops.flush_alerts(self.db,self.cfg,sink)
        self.assertEqual(sink.count,1)
        self.assertEqual(self.db.execute('select state from alerts').fetchone()[0],'uncertain')

    def test_unavailable_bot_keeps_diagnostic_pending_without_send(self):
        ops.begin_outage(self.db,'fixture',monitor.iso(monitor.now()-dt.timedelta(minutes=6)))
        class Sink:
            def call(s,method,*args,**kwargs):
                self.assertEqual(method,'getMe');raise monitor.UnknownDelivery()
        ops.flush_alerts(self.db,self.cfg,Sink())
        self.assertEqual(self.db.execute('select state from alerts').fetchone()[0],'pending')

    def test_report_never_credits_sleep_stale_checks_or_missing_samples(self):
        start=monitor.now()-dt.timedelta(seconds=200)
        stamps=[monitor.iso(start+dt.timedelta(seconds=i)) for i in (0,10,20,150,160,170)]
        with self.db:
            for i,s in enumerate(stamps):self.db.execute('insert into samples(at,live_at,healthy) values(?,?,?)',(s,s if i!=2 else stamps[1],1))
        with contextlib.closing(monitor.connect(self.cfg.db)):pass
        with patch('automation.JOURNAL',self.root/'operations.sqlite3'),patch('automation.config',return_value=self.cfg):result=ops.report()
        self.assertEqual(result['observed_seconds'],30)
        self.assertFalse(result['observation_complete'])

    def test_persistent_manual_stop_prevents_launch_even_with_scheduler_restart(self):
        marker=self.root/'operator-stop';marker.write_text('stop')
        with patch('automation.STATE',self.root),patch('automation.STOP',marker),\
             patch('local_chrome.private_directory'),patch('automation.launch_host') as launch:
            ops.supervise()
        launch.assert_not_called()


if __name__=='__main__':unittest.main()
