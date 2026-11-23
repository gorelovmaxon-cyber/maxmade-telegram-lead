"""A bounded Chrome pilot must leave the existing pending queue and statuses alone."""
import unittest
import test_monitor as fixtures
import monitor
import chrome_pilot
from dataclasses import replace


class BrowserDeliveryScopeTests(unittest.TestCase):
    setUp = fixtures.MonitorTests.setUp
    tearDown = fixtures.MonitorTests.tearDown
    ingest = fixtures.MonitorTests.ingest

    def test_persisted_chrome_queue_survives_quiet_hours_and_excludes_legacy(self):
        self.cfg=replace(self.cfg,source_ids=(),client_sources=(chrome_pilot.SOURCE,),client_peer_ids=(chrome_pilot.PEER,))
        source='client:'+str(chrome_pilot.PEER)
        old=self.ingest(mid='101',source=source,edit='client:old')
        current=self.ingest(mid='102',source=source,edit='browser:fixture-hash')
        scope=chrome_pilot.delivery_candidates(self.db)
        self.assertEqual(scope,(current,))
        sink=fixtures.FakeSink()
        self.assertEqual(monitor.deliver(self.db,self.cfg,sink,self.t.replace(hour=22),scope),0)
        # A newly attached reader derives the same pending scope from SQLite.
        self.assertEqual(monitor.deliver(self.db,self.cfg,sink,self.t,chrome_pilot.delivery_candidates(self.db)),1)
        self.assertEqual(dict(self.db.execute('select candidate_id,state from deliveries')),{old:'pending',current:'sent'})

    def test_callback_worker_reuses_owner_signature_and_never_ingests_other_messages(self):
        current=self.ingest(mid='103')
        sink=fixtures.FakeSink();monitor.deliver(self.db,self.cfg,sink,self.t)
        callback={'update_id':10,'callback_query':{'id':'fixture','from':{'id':42},
            'message':{'chat':{'id':42},'message_id':91},
            'data':f'c:{current}:taken:'+monitor.signature(self.cfg.token,current,'taken')}}
        updates=[callback,{'update_id':11,'message':{'chat':{'id':-100123},'message_id':999,'date':int(self.t.timestamp()),'text':'Never ingest from callback worker'}}]
        class Api:
            def __init__(self):self.calls=[]
            def call(self,method,payload,timeout=5):
                self.calls.append((method,payload))
                return updates if method=='getUpdates' else True
        api=Api();chrome_pilot.poll_owner_callbacks(self.db,self.cfg,api)
        self.assertEqual(self.db.execute('select owner_status from candidates where id=?',(current,)).fetchone()[0],'taken')
        self.assertEqual(self.db.execute('select count(*) from events').fetchone()[0],1)
        self.assertEqual(api.calls[0][1]['allowed_updates'],['callback_query'])
        self.assertEqual([method for method,_ in api.calls],['getUpdates','answerCallbackQuery'])
    def test_pilot_delivers_only_its_observed_candidate(self):
        old = self.ingest(mid='101')
        current = self.ingest(mid='102')
        taken = self.ingest(mid='103')
        with self.db:
            self.db.execute("UPDATE candidates SET owner_status='taken' WHERE id=?",(taken,))
        sink = fixtures.FakeSink()
        self.assertEqual(monitor.deliver(self.db,self.cfg,sink,self.t,(current,taken)),1)
        states = dict(self.db.execute('SELECT candidate_id,state FROM deliveries'))
        self.assertEqual(states,{old:'pending',current:'sent',taken:'pending'})
        self.assertEqual(self.db.execute('SELECT owner_status FROM candidates WHERE id=?',(taken,)).fetchone()[0],'taken')
        self.assertEqual(monitor.deliver(self.db,self.cfg,sink,self.t,()),0)
        self.assertEqual(len(sink.calls),1)
