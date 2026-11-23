"""Enable a reviewed candidate only after a fresh exact-account membership audit.

Review covers the available public rules, not a claim of permission for outreach.
"""
import argparse
import contextlib
import json
import sqlite3
import automation
import monitor
import source_registry as registry

POLICIES={
 'onpeak_chat':('external','https://telegra.ph/Pravila-soobshchestva-predprinimatelej-Na-vershinu-10-24',
                'General and Friday rules reviewed 2026-11-06: no spam or unsolicited private messages; contextual help allowed. Advertising Friday only with reciprocal community-post link; Friday terms https://telegra.ph/Informaciya-po-reklamnuyu-pyatnicu-soobshchestva-Na-vershinu-10-24. Local reading only; no special automated-reading permission asserted.',
                'explicit_request_context_only_no_unsolicited_dm','friday_only_reciprocal_publication_conditions'),
 'bitrixinside24':('profile',None,'Public about reviewed 2026-11-06: Bitrix24 user discussion; questions/projects routed to the named manager. No additional linked/pinned rules located. Local reading only; no permission for unsolicited member replies asserted.',
                  'questions_and_projects_via_profile_manager_no_automatic_contact','unknown'),
 'business_assistants':('profile',None,'Public about reviewed 2026-11-06: business-assistant community for questions and support; vacancy/advertising placement via profile manager. No further linked/pinned rules located. Local reading only; vacancy placement is not permission for private pitches.',
                        'vacancy_ad_placement_via_manager_member_response_route_unknown','placement_via_manager'),
 'odp2021':('profile',None,'Public description reviewed 2026-11-06: entrepreneurs association with regional chats. No linked/pinned rules or response route identified. Local reading only; partnerships and announcements remain separate from direct orders.',
             'unknown_no_automatic_outreach','unknown'),
 'biznes_chat':('post','https://t.me/biznes_chat/585370','Rules post reviewed 2026-11-05: business discussion/recommendations allowed; unsolicited private service offers prohibited, advertising requires administration agreement. Optional 2026-11-06 re-fetch recorded separately; only read, no private pitching.',
                'private_service_offers_prohibited','admin_agreement_required'),
 'smmruschats':('profile',None,'Public about: no abuse, flooding or posting as channel. No full additional rules located; further conditions unknown.',
                'only_explicit_request_route_otherwise_unknown','unknown'),
 'free_chat_for_freelance':('profile',None,'Public freelance customer/performer community. No linked full rules or identified rules pin located; additional conditions unknown.',
                'only_explicit_request_route_otherwise_unknown','unknown'),
 'tilda_official_chat':('external','https://blog.tilda.cc/community-chat-rules','Public Q&A community. Own/third-party service advertising prohibited; this is not evidence of permission for private offers. Reading only; check the exact request before any manual response.',
                'only_explicit_request_route_otherwise_unknown','advertising_prohibited'),
}


def enable(db,name,audit):
    if name not in POLICIES:raise ValueError('No recorded policy review for this candidate')
    if automation.age(audit.get('at')) is None or not 0<=automation.age(audit['at'])<3600:raise ValueError('Audit is not fresh')
    row=next((r for r in audit.get('groups',[]) if r.get('username','').lower()==name),{})
    # The earlier audit recorded a successful identity/tail proof before the
    # optional re-fetch of this already reviewed, explicitly linked rule post.
    optional_refetch=(name=='biznes_chat' and row.get('stage')=='complete' and row.get('tail_audit')
                      and 'https://t.me/biznes_chat/585370' in row.get('dom',{}).get('rules_links',[]))
    if not (row.get('account_verified') and row.get('source_verified') and row.get('membership')=='joined'
            and row.get('message_metadata_accessible') and row.get('stage')=='complete'
            and (not row.get('error_type') or optional_refetch)):
        raise ValueError('Candidate audit incomplete or membership unverified')
    peer=row.get('peer_id');old=db.execute('select peer_id from sources where username=?',(name,)).fetchone()
    if not old or not isinstance(peer,int) or peer>=-10**12 or old[0] not in (None,peer):raise ValueError('Pinned source identity differs')
    _,url,note,route,publish=POLICIES[name]
    with db:
        db.execute('''update sources set enabled=1,access='joined',checked_at=?,peer_id=?,type='group',
          rules_state='reviewed',rules_url=?,rules_note=?,read_permission='local_client',reply_route=?,publish_permission=? where username=?''',
          (audit['at'],peer,url,note,route,publish,name))
        registry.enabled(db)
    return {'enabled_candidate':name,'peer_id':peer,'rules_scope':'available_public_rules; additional_conditions_unknown; no_automatic_outreach'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('username',choices=POLICIES);p.add_argument('--audit',default='candidate-pinned-rules.json');a=p.parse_args()
    with contextlib.closing(registry.connect()) as db:
        backup=automation.STATE/('registry-before-enable-'+monitor.now().strftime('%Y%m%dT%H%M%SZ')+'.sqlite3')
        with contextlib.closing(sqlite3.connect(backup)) as copy:db.backup(copy)
        print(json.dumps(enable(db,a.username,automation.read_json(automation.STATE/a.audit))))
