from pathlib import Path
from types import SimpleNamespace as NS
import json
import unittest
from serious_game_backend.application.contract_facts import FACT_KEYS, conduct_household_viewing, record_contract_signatory_contact, resolve_contract_facts
from serious_game_backend.domain.gameplay_governance import GovernanceActionRecord, HouseholdContract

class ContractFactTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'content/packages/pkg_gameplay_v3'
        read = lambda name: json.loads((root / name).read_text(encoding='utf-8-sig'))
        data = read('households.json')
        self.p = NS(households=[NS(**(data['defaults'] | h)) for h in data['households']], governance_config=read('governance_config.json'), npc_profiles=[])
        self.p.limited_signatory_for = lambda hid: NS(name='杨志勇') if hid == 'YANG-02' else None
        self.s = NS(flags=set(), logs=[], governance_actions={}, household_contracts={}, resource_reservations=[], game_state=NS(story_day=50))
    def contract(self, hid='LAO-01'):
        c = HouseholdContract('c-'+hid, 'batch', hid, '杨志勇', None, 50, term_sheet=dict.fromkeys(FACT_KEYS, True) | {'housing_resource_id':'housing_d1_100'})
        self.s.household_contracts[c.contract_id] = c
        return c
    def facts(self,c): return resolve_contract_facts(self.s,self.p,c)
    def action(self):
        a=GovernanceActionRecord('visit','household_visit',50,('npc_lao_juetou',),('1.1',))
        self.s.governance_actions['visit']=a
        return a
    def view(self,a,**kw):
        return conduct_household_viewing(self.s,self.p,a,**({'household_id':'LAO-01','housing_resource_id':'housing_d1_100','invitation':'现在去看这套房好吗','npc_accepted':True}|kw))
    def test_forged_checkbox_prose_no_effect(self):
        self.s.logs.append({'type':'dialogue','text':'已看房授权核验全部办妥'})
        self.assertFalse(any(self.facts(self.contract()).values()))
    def test_story_facts_scoped(self):
        self.s.flags.update({'村账已摊','旧案了结','补偿口径已澄清'})
        self.assertTrue(self.facts(self.contract('ZMC-01'))['ledger_disclosed'])
        self.assertTrue(self.facts(self.contract('TAN-01'))['old_case_resolved'])
        self.assertTrue(self.facts(self.contract('MIAO-01'))['prior_payment_verified'])
        self.assertFalse(any(self.facts(self.contract('MA-01')).values()))
    def test_shared_flag_not_viewing(self):
        c=self.contract(); self.s.flags.add('样板签约')
        self.s.logs.append({'type':'decision','decision_id':'dp1_06','option_id':'a'})
        self.assertFalse(self.facts(c)['real_unit_viewed'])
        self.s.logs.append({'type':'decision','decision_id':'dp6_02','option_id':'b'})
        # The scene does not identify this pool: history is not universal proof.
        self.assertFalse(self.facts(c)['real_unit_viewed'])
        a=self.action(); self.assertIsNotNone(self.view(a))
        self.assertTrue(self.facts(c)['real_unit_viewed'])
        c.term_sheet['housing_resource_id']='housing_d1_80'
        self.assertFalse(self.facts(c)['real_unit_viewed'])
    def test_verified_principal_not_representative(self):
        c=self.contract('YANG-02'); c.signatory_name='杨波'
        self.assertFalse(record_contract_signatory_contact(self.s,self.p,c))
        c.signatory_name='杨志勇'
        self.assertTrue(record_contract_signatory_contact(self.s,self.p,c))
        self.assertTrue(record_contract_signatory_contact(self.s,self.p,c))
        self.assertEqual(1,len(self.s.logs))
        self.assertTrue(self.facts(c)['authorization_confirmed'])
        self.assertFalse(self.facts(self.contract('ZDS-01'))['authorization_confirmed'])
    def test_viewing_resource_scope_idempotence_no_reservation(self):
        c=self.contract(); a=self.action(); first=self.view(a)
        self.assertIsNotNone(first); self.assertEqual('安置小区',first['location'])
        self.assertEqual(first,self.view(a)); self.assertEqual(1,len(a.hard_outcomes))
        self.assertEqual([],self.s.resource_reservations); self.assertTrue(self.facts(c)['real_unit_viewed'])
        c.term_sheet['housing_resource_id']='housing_d1_80'
        self.assertFalse(self.facts(c)['real_unit_viewed'])
    def test_no_self_report_refusal_future_pool_wrong_person(self):
        a=self.action()
        self.assertIsNone(self.view(a,invitation='已经看过房了'))
        self.assertIsNone(self.view(a,npc_accepted=False))
        self.assertIsNone(self.view(a,housing_resource_id='housing_d60_100'))
        self.assertIsNone(self.view(a,invitation='明天一起去看'))
        a.target_ids=('npc_zhou_dashan',)
        self.assertIsNone(self.view(a)); self.assertEqual([],a.hard_outcomes)
    def test_exhausted_pool(self):
        a=self.action()
        self.s.resource_reservations.append(NS(resource_id='housing_d1_100',quantity=4,status='delivered'))
        self.assertIsNone(self.view(a))
