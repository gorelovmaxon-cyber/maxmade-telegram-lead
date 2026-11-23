"""Small metadata-only dispatcher for documented local reader checks."""
import argparse
import local_chrome

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['show_membership_review','trial_close_context','candidate_joined_three','source_tail_metadata','resume_waiting_sources','candidate_biznes','candidate_remaining','candidate_next_batch','candidate_stage_five','prepare_operator_test','add_pending_source','candidate_pinned_rules','refresh_reports','candidate_rules_next','candidate_rules','resume_waiting_source','trial_close_one','trial_reload_one','prune_reader_pages','multi_resume','multi_inspect','source_rules','selected_layout_probe','verify_selected','source','verify','profile','arm','stop','drain','diagnose'])
    local_chrome.command('runtime_'+p.parse_args().action)
