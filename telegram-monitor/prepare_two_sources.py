"""Use dated local access/rules evidence, never infer membership from a URL."""
import contextlib
import json
import automation
import source_registry as registry


def prepare():
    status=automation.read_json(automation.STATE/'status.json')
    proof=status.get('rules_probe',{});access=proof.get('access',{})
    if access.get('peer_id')!=-1001428955589 or access.get('membership')!='joined' or not access.get('account_verified') or not access.get('source_verified'):
        raise ValueError('FreeLead needs a fresh exact access verification')
    text=proof.get('rules_text','').lower()
    if 'отклика на запросы' not in text or 'интересанту' not in text:raise ValueError('Selected rules post was not read')
    with contextlib.closing(registry.connect()) as db:
        registry.initialize(db,automation.read_json(automation.STATE/'selected-group-verification.json'))
        with db:
            db.execute('''update sources set peer_id=-1001428955589,type='group',access='joined',checked_at=?,
              rules_state='reviewed',rules_url='https://t.me/freelead/1189432',
              rules_note='No advertising or job-seeker posts; responses to specialist requests by personal message. No automatic replies. No explicit automated-reading clause found; no special permission asserted.',
              publish_permission='advertising_prohibited',reply_route='personal_reply_to_explicit_request',
              read_permission='local_client',enabled=1 where username='freelead' ''',(status['at'],))
        rows=registry.enabled(db)
    receipt={'at':status['at'],'stage':2,'sources':[r['username'] for r in rows],'rules_permalink':'https://t.me/freelead/1189432',
             'account_verified':True,'automatic_joins':0,'special_scraping_permission':False}
    automation.write_json(automation.STATE/'two-source-preparation.json',receipt)
    print(json.dumps(receipt))


if __name__=='__main__':prepare()
