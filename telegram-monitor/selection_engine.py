"""Three stages: generic roles -> explicit business adapter -> notification policy."""
from __future__ import annotations
import re
import message_classification as roles

RULES_VERSION=roles.RULES_VERSION
DEFAULT_ROUTES={'demand':'main','partnership':'partnership','vacancy':'employment','ambiguous':'review',
                'offer':'none','other':'none','closed_signal':'none'}


def profile_match(text,classification,profile):
    adapter=profile.get('selection_adapter','patterns')
    if adapter=='maxmade_v32':
        import maxmade_selection_v32
        return maxmade_selection_v32.select(text,profile)
    if adapter=='maxmade_v33':
        import maxmade_selection
        return maxmade_selection.select(text,profile)
    if adapter=='maxmade_v31':
        # This is the existing specialised business matcher, not the generic contract.
        import order_selection
        return order_selection.select(text)
    if adapter!='patterns':raise ValueError('Unknown business-profile matcher')
    s=text.lower().replace('ё','е')
    services=[name for name,spec in profile['services'].items() if roles.positive(spec['pattern'],s)]
    kind=classification['category'];task=classification['task_exists']
    uncertain=bool(roles.positive(profile.get('unconfirmed_competence_pattern',r'(?!)'),s))
    fit=('needs_verification' if uncertain else 'matched') if services else 'outside'
    return {'kind':kind,'task_exists':task,'service_fit':fit,'author_role':
      'service_provider' if kind=='offer' else 'requester' if task else 'unknown',
      'services':services,'need':text[:350] if task else 'Задача для исполнителя не подтверждена',
      'reason':'Проверены роль автора и услуги выбранного профиля'}


def apply_policy(match,profile):
    routes=dict(DEFAULT_ROUTES,**profile.get('notification_policy',{}).get('routes',{}))
    if set(routes.values())-{'main','partnership','employment','review','none'}:raise ValueError('Unsupported notification route')
    route=routes[match['kind']]
    if profile.get('selection_adapter') in ('maxmade_v32','maxmade_v33') and match.get('queue') in ('none','review','employment'):
        return match['queue']
    if route in ('main','partnership') and not (match['task_exists'] and match['services'] and match['service_fit'] in ('matched','partial','needs_verification')):
        route='review' if match['service_fit']=='unknown' else 'none'
    return route


def evaluate(text,profile):
    if not profile.get('version') or not profile.get('services'):raise ValueError('Versioned business profile with services required')
    generic=roles.classify(text)
    match=profile_match(text,generic,profile)
    route=apply_policy(match,profile)
    category=match['kind'] if match['kind']!='other' else generic['category']
    return dict(match,queue=route,category=category,generic_category=generic['category'],
      rules_version=RULES_VERSION,profile_version=profile['version'],
      policy_version=profile.get('notification_policy',{}).get('version','notification-policy-v1'))
