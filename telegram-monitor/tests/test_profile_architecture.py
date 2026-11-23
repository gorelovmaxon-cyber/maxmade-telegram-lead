import copy
import unittest
import selection_engine as engine
import message_classification as roles
import order_profile

class ProfileSeparationTests(unittest.TestCase):
    def profile(self):
        return {'version':'synthetic-photo-v1','services':{'photography':{'pattern':r'фотосъем\w*|фотограф\w*'}},
          'notification_policy':{'version':'photo-hiring-v1','routes':{'vacancy':'main','partnership':'review'}}}
    def test_other_business_and_employment_preference_without_web_terms(self):
        profile=self.profile();a=engine.evaluate('Нужна предметная фотосъёмка для каталога',profile)
        self.assertEqual((a['category'],a['queue'],a['service_fit']),('demand','main','matched'))
        b=engine.evaluate('Нужен фотограф в штат, оклад',profile)
        self.assertEqual((b['category'],b['queue']),('vacancy','main'))
        self.assertEqual(b['profile_version'],'synthetic-photo-v1')
        self.assertEqual(b['policy_version'],'photo-hiring-v1')
        self.assertEqual(b['rules_version'],roles.RULES_VERSION)
        self.assertEqual(engine.evaluate('Нужен сайт',profile)['queue'],'none')
    def test_same_category_different_business_fit_and_policy(self):
        text='Нужен фотограф в штат, оклад'
        before=roles.classify(text)
        a=engine.evaluate(text,self.profile())
        b=engine.evaluate(text,order_profile.profile())
        self.assertEqual(before,roles.classify(text))
        self.assertEqual(a['queue'],'main');self.assertNotEqual(b['queue'],'main')
    def test_no_source_registry_or_delivery_side_effects(self):
        profile=self.profile();before=copy.deepcopy(profile)
        for _ in range(2):engine.evaluate('Предлагаю услуги фотографа',profile)
        self.assertEqual(profile,before)
        self.assertEqual(engine.evaluate('Предлагаю услуги фотографа',profile)['queue'],'none')
    def test_unknown_budget_contact_do_not_block_profile_match(self):
        a=engine.evaluate('Нужна фотосъемка',self.profile())
        self.assertEqual(a['queue'],'main')
