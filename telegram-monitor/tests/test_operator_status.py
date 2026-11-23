import datetime as dt
import unittest
import operator_status as status
import monitor

class OwnerStatusTests(unittest.TestCase):
    def setUp(self):self.at=dt.datetime(2026,11,9,9,tzinfo=monitor.UTC)
    def source(self,name,offset=0,phase='reading'):
        stamp=monitor.iso(self.at-dt.timedelta(seconds=offset))
        return {'username':name,'phase':phase,'capture_running':True,'last_live_check':stamp,'last_web_transport':stamp}
    def test_silent_sources_are_healthy_with_fresh_guards(self):
        result=status.summarize({'source_states':[self.source(n) for n in ('one','two','three')]},['one','two','three'],True,False,self.at)
        self.assertEqual(result['operating_status'],'работает')
        self.assertEqual(result['reading_sources'],3)
    def test_old_reading_is_not_health_and_partial_is_explicit(self):
        host={'source_states':[self.source('one'),self.source('two',90)]}
        result=status.summarize(host,['one','two','three'],True,False,self.at)
        self.assertEqual(result['operating_status'],'частично работает')
        self.assertEqual(result['reading_sources'],1)
        self.assertEqual(result['source_status'][1]['state'],'проверка устарела')
    def test_manual_stop_overrides_old_good_status(self):
        result=status.summarize({'source_states':[self.source('one')]},['one'],True,True,self.at)
        self.assertEqual(result['operating_status'],'остановлен');self.assertEqual(result['reading_sources'],0)
        self.assertFalse(result['automatic_recovery_pending'])
    def test_attention_and_clock_future_are_not_false_success(self):
        host={'source_states':[self.source('one',-300),self.source('two',phase='manual_attention')]}
        result=status.summarize(host,['one','two'],True,False,self.at)
        self.assertEqual(result['operating_status'],'нужна помощь');self.assertEqual(result['reading_sources'],0)
