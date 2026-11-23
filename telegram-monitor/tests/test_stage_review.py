import unittest
import monitor
from stage_review import evaluate


class StageGateTests(unittest.TestCase):
    def fixture(self):
        now=monitor.iso(monitor.now())
        rows=[dict(username=n,enabled=True,observed_seconds=301,events_during_observation=1,gaps=[],access='joined',rules_state='reviewed') for n in ('first','second')]
        host={'source_states':[dict(username=n,phase='reading',capture_running=True,last_live_check=now,last_web_transport=now) for n in ('first','second')]}
        trials={'trials':[dict(case=c,result='recovered',before={'sources':[{},{}]},all_other_samples_live=True,both_paused=True,fully_stopped=True,stayed_stopped=True) for c in ('close','reload','context_offline_online','operator_stop_start_and_host_restart')]}
        return rows,host,trials,{'at':now,'available_physical_bytes':2000000000}

    def test_technical_acceptance_never_claims_order_windows_or_24h(self):
        rows,host,trials,resources=self.fixture();v=evaluate(2,{'sources':rows},host,trials,True,resources)
        self.assertTrue(v['technical_stage_accepted']);self.assertFalse(v['direct_order_accepted'])
        self.assertFalse(v['automatic_expansion']);self.assertFalse(v['observation_24h_accepted'])

    def test_process_or_old_baseline_without_new_event_cannot_accept_stage(self):
        rows,host,trials,resources=self.fixture();rows[1]['events_during_observation']=0
        self.assertFalse(evaluate(2,{'sources':rows},host,trials,True,resources)['technical_stage_accepted'])

    def test_two_source_trials_cannot_accept_five_and_low_memory_blocks_expansion(self):
        rows,host,trials,resources=self.fixture()
        self.assertFalse(evaluate(5,{'sources':rows},host,trials,True,resources)['technical_stage_accepted'])
        resources['available_physical_bytes']=1000000000
        self.assertFalse(evaluate(2,{'sources':rows},host,trials,True,resources)['technical_stage_accepted'])
