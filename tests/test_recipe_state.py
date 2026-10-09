import os
import unittest
from unittest.mock import patch
from tools import recipe_state as state
from tools import update as u


class RecipeIdentity(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,{'GITHUB_REPOSITORY':'owner/repo'})
        environment.start()
        self.addCleanup(environment.stop)

    def record(self):
        return {'kind':'recipe','pr_number':3,'base':'c'*40,'recipe_base':'b'*40,'head':'a'*40,'head_branch':'recipe-updates/example/version','recipe_tree':'d'*40,'pkgbase':'example','watcher_id':'version'}

    def pr(self):
        r=self.record()
        return {'number':3,'state':'open','merged':False,'base':{'ref':'pkg/example','sha':r['recipe_base'],'repo':{'full_name':'owner/repo'}},'head':{'ref':'recipe-updates/example/version','sha':r['head'],'repo':{'full_name':'owner/repo'}}}

    def test_human_feature_branch_is_bound_without_bot_naming_convention(self):
        pr=self.pr()
        pr['head']['ref']='feature/example-dependencies'
        record={**self.record(),'watcher_id':None,'head_branch':pr['head']['ref']}
        with patch.object(u,'main_sha',return_value=record['base']),patch.object(u,'trusted_checkout_sha',return_value=record['base']),patch.object(u,'ref_head',return_value=record['recipe_base']),patch.object(u,'api',side_effect=[pr,{'protected':True},{'tree':{'sha':record['recipe_tree']}}]):
            self.assertEqual(state.assert_recipe_identity(record)['head']['ref'],record['head_branch'])

    def test_head_base_control_and_protection_each_invalidate_evidence(self):
        for change in ('head','base','control','unprotected'):
            with self.subTest(change=change):
                pr=self.pr();r=self.record()
                if change=='head':pr['head']['sha']='e'*40
                if change=='base':pr['base']['sha']='e'*40
                control='e'*40 if change=='control' else r['base']
                with patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'main_sha',return_value=control),patch.object(u,'trusted_checkout_sha',return_value=r['base']),patch.object(u,'ref_head',return_value=r['recipe_base']),patch.object(u,'api',side_effect=[pr,{'protected':change!='unprotected'}]):
                    with self.assertRaises(ValueError):state.assert_recipe_identity(r)

    def test_next_recipe_proposal_preserves_accepted_closed_head_ancestry(self):
        with patch.object(u.recipes,'is_ancestor',return_value=True):
            u.check_proposal_ref('a'*40,'b'*40,None,'c'*40)
        with patch.object(u.recipes,'is_ancestor',return_value=False),self.assertRaisesRegex(ValueError,'user-edited'):
            u.check_proposal_ref('a'*40,'b'*40,None,'c'*40)

    def test_claimed_generated_recipe_ownership_requires_attested_full_receipt(self):
        pr={'head':{'sha':'a'*40}}
        receipt={'repository':'owner/repo','proposal_origin':'watcher','head_branch':'recipe-updates/example/version','pkgbase':'example','watcher_id':'version','watcher_ids':['version'],'recipe_head':'a'*40,'recipe_tree':'d'*40,'recipe_base':'b'*40,'base':'c'*40}
        commit={'tree':{'sha':'d'*40},'parents':[{'sha':'b'*40}]}
        with patch.object(state,'load',return_value=receipt),patch.object(u,'api',side_effect=[commit,[]]),self.assertRaisesRegex(ValueError,'attestation'):
            u.owned_proposal(pr,'example','version')

    def test_watcher_writer_refuses_closed_manual_head_on_bot_named_branch(self):
        from contextlib import ExitStack
        C, B, H = (value*40 for value in 'cba')
        batch = {'schema': 1, 'base': C, 'receipts': [
            {'pkgbase': 'example', 'watcher_id': 'version', 'base': C,
             'recipe_commit': B, 'unchanged': True}]}
        def response(path, *args):
            if '/pulls?' in path:
                return []
            return {'tree': {'sha': 'd'*40}, 'parents': [{'sha': B}]}
        with ExitStack() as stack:
            stack.enter_context(patch.object(u, 'load', side_effect=[
                batch, {'watchers': [{'id': 'version'}]}]))
            stack.enter_context(patch.object(u, 'main_sha', return_value=C))
            stack.enter_context(patch.object(u, 'control_checkout', return_value=(C, {'example': B})))
            stack.enter_context(patch.object(u, 'policy_at', return_value={'example': {}}))
            stack.enter_context(patch.object(u, 'ref_head', side_effect=lambda branch: B if branch == 'pkg/example' else H))
            stack.enter_context(patch.object(u, 'api', side_effect=response))
            stack.enter_context(patch.object(state, 'protected_branch'))
            stack.enter_context(patch.object(state, 'load', return_value={'proposal_origin': 'manual'}))
            push = stack.enter_context(patch.object(u, 'push_ref'))
            attest = stack.enter_context(patch.object(state, 'attest'))
            with self.assertRaisesRegex(ValueError, 'user-edited proposal'):
                u.write_proposals('unused')
        push.assert_not_called()
        attest.assert_not_called()

    def test_immutable_record_refuses_same_key_with_different_frozen_source(self):
        with patch.object(state,'load_optional',return_value={'source':'original'}),self.assertRaisesRegex(ValueError,'immutable'):
            state.save('proposal','a'*40,{'source':'replacement'})

    def test_retry_preserves_receipt_content_identity(self):
        value={'source':'original','head':'a'*40}
        with patch.object(state,'load_optional',return_value=value),patch.object(u,'api') as api:
            self.assertEqual(state.save('proposal','a'*40,value),state.digest(value))
        api.assert_not_called()

    def test_explicit_dispatch_cannot_validate_a_later_human_head(self):
        pr={'base':{'ref':'pkg/example'},'head':{'sha':'b'*40}}
        with patch.dict(os.environ,{'EXPECTED_HEAD':'a'*40}),patch.object(u,'api',return_value=pr),self.assertRaisesRegex(ValueError,'dispatched head changed'):
            u.prepare(3,'unused')

    def test_explicit_enrollment_bootstrap_preserves_existing_recipe_work(self):
        with patch.object(u,'control_checkout',return_value=('c'*40,{'example':'a'*40})),patch.object(u,'main_sha',return_value='c'*40),patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'ref_head',return_value='b'*40),patch.object(u,'api',return_value={'protected':True}) as api,self.assertRaisesRegex(ValueError,'existing recipe branch'):
            u.initialize_recipe_branches()
        self.assertFalse(any(call.args[1:2]==('POST',) for call in api.call_args_list))

    def test_enrollment_bootstrap_refuses_control_advance_before_creation(self):
        with patch.object(u,'control_checkout',return_value=('c'*40,{'example':'a'*40})),patch.object(u,'main_sha',side_effect=['c'*40,'d'*40]),patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'ref_head',return_value=None),patch.object(u,'api',return_value={'protected':True}) as api,self.assertRaisesRegex(ValueError,'main advanced'):
            u.initialize_recipe_branches()
        self.assertFalse(any(call.args[1:2]==('POST',) for call in api.call_args_list))

    def test_legacy_main_recipe_proposals_cannot_enter_candidate_gate(self):
        pr={'base':{'ref':'main'},'head':{'ref':'updates/example/version'}}
        with patch.object(u,'api',return_value=pr),self.assertRaisesRegex(ValueError,'migrate-existing'):
            u.prepare(3,'unused')

    def test_review_environment_without_required_reviewer_fails_closed(self):
        with patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'api',return_value={'protection_rules':[]}),self.assertRaisesRegex(ValueError,'reviewer'):
            u.require_review_environment('code-review')

    def test_recipe_payload_rejects_controller_files(self):
        for path in ('.github/workflows/build.yml','inputs/example.json','upstream/example.json','acceptance/example.json'):
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'controller'):
                u.recipe_payload_path(path)

    def test_recipe_payload_preserves_native_files(self):
        for path in ('PKGBUILD','.SRCINFO','fix.patch','assets/data.json'):
            self.assertEqual(u.recipe_payload_path(path).as_posix(),path)

    def test_only_relevant_trusted_controls_invalidate_receipt_compatibility(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in state.CONTROLS:
                path=root/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('accepted')
            original=state.control_digest(root)
            (root/'README.md').write_text('unrelated human documentation')
            self.assertEqual(state.control_digest(root),original)
            (root/'tools/recipe_candidates.py').write_text('new validation rules')
            self.assertNotEqual(state.control_digest(root),original)

    def test_candidate_history_lookup_is_bounded_to_controller_namespace(self):
        rows={'tree':[{'path':'candidate/one.json','type':'blob','mode':'100644'},
                      {'path':'proposal/two.json','type':'blob','mode':'100644'}]}
        with patch.object(u,'ref_head',return_value='a'*40),patch.object(u,'api',side_effect=[{'tree':{'sha':'b'*40}},rows]):
            self.assertEqual(state.keys('candidate',''),['one'])

    def test_approval_lookup_never_reads_another_tuple_or_recipe_payload(self):
        rows={'tree':[{'path':'approved/tuple-12-1.json','type':'blob','mode':'100644'},
                      {'path':'approved/other-12-1.json','type':'blob','mode':'100644'},
                      {'path':'proposal/tuple-12-1.json','type':'blob','mode':'100644'}]}
        with patch.object(u,'ref_head',return_value='a'*40),patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'api',side_effect=[{'tree':{'sha':'b'*40}},rows]):
            self.assertEqual(state.keys('approved','tuple-'),['tuple-12-1'])

    def test_receipt_keys_cannot_escape_controller_root(self):
        for key in ('../recipe','nested/path','x.json',''):
            with self.subTest(key=key),self.assertRaises(ValueError):state.record_path('proposal',key)

    def test_human_token_cannot_publish_controller_attestation(self):
        value={'head':'a'*40}
        posted={'context':'arch-receipt/proposal/tuple','state':'success',
                'description':state.digest(value),'creator':{'login':'human'}}
        with patch.object(u,'api',side_effect=[[],posted]),self.assertRaisesRegex(ValueError,'trusted Actions'):
            state.attest('proposal','tuple',value,'a'*40)

    def test_stored_native_receipt_requires_exact_trusted_status_digest(self):
        value={'head':'a'*40,'lock':{'commit':'b'*40}}
        context='arch-receipt/built/tuple'
        for creator,description in (('human',state.digest(value)),('github-actions[bot]','f'*64)):
            rows=[{'context':context,'state':'success','description':description,'creator':{'login':creator}}]
            with self.subTest(creator=creator),patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'api',return_value=rows),self.assertRaisesRegex(ValueError,'attestation'):
                state.require_attestation('built','tuple',value,'a'*40)
        rows=[{'context':context,'state':'success','description':state.digest(value),'creator':{'login':'github-actions[bot]'}}]
        with patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'api',return_value=rows):
            state.require_attestation('built','tuple',value,'a'*40)

    def test_delayed_receipt_must_match_content_digest(self):
        value={'lock':{'version':'1'}}
        envelope={'schema':1,'digest':state.digest(value),'value':value}
        self.assertEqual(state.decode(envelope),value)
        envelope['value']['lock']['version']='2'
        with self.assertRaisesRegex(ValueError,'digest'):state.decode(envelope)
