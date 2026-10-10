import base64
from contextlib import ExitStack
import hashlib
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import recipe_state as state
from tools import update as u


class ControlDigestTests(unittest.TestCase):
    original_files = (
        'tools/update.py', 'tools/recipes.py', 'tools/recipe_candidates.py',
        'tools/recipe_acceptance.py', 'tools/recipe_state.py', 'tools/package_runs.py',
        'tools/build_store.py', 'tools/attestations.py', '.github/workflows/update.yml',
        '.github/workflows/candidate.yml', '.github/workflows/candidate-dispatch.yml',
        '.github/workflows/build-package.yml', 'tools/source_review.py',
        '.github/workflows/verification.yml',
    )

    def setUp(self):
        root = Path.home() / '.local/state/arch-packages/work/arch-control-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, files):
        for index, name in enumerate(files):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('accepted payload ' + str(index)).encode())
            path.chmod(0o644)
        self.declare(files)

    def declare(self, files):
        (self.root / 'tools/recipe_state.py').write_text(
            "raise RuntimeError('historical controller must not execute')\n"
            'CONTROLS = ' + repr(tuple(files)) + '\n')

    def expected(self, files):
        records = [
            {'path': name, 'mode': (self.root / name).stat().st_mode & 0o7777,
             'sha256': hashlib.sha256((self.root / name).read_bytes()).hexdigest()}
            for name in files
        ]
        encoded = (json.dumps(records, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=False) + '\n').encode()
        return hashlib.sha256(encoded).hexdigest()

    def test_original_controller_manifest_without_later_import_registration(self):
        self.fixture(self.original_files)
        self.assertFalse((self.root / 'tools/imports.py').exists())
        self.assertEqual(state.control_digest(self.root), self.expected(self.original_files))
class ReceiptStore:
    """Git object/ref model retaining complete trees across competing writes."""
    def __init__(self):
        self.tip=None
        self.blobs={}
        self.trees={}
        self.commits={}
        self.before_push=None
        self.pushes=0
        self.failure=subprocess.CalledProcessError(1, ['git', 'push'], stderr=b'rejected')

    def object(self, objects, value):
        sha=hashlib.sha1(state.sources.canonical(value)).hexdigest()
        objects[sha]=value
        return sha

    def api(self, route, method='GET', data=None):
        endpoint=route.split('/git/',1)[1]
        kind, _, suffix=endpoint.partition('/')
        if method=='POST':
            if kind=='blobs':
                return {'sha':self.object(self.blobs, data)}
            if kind=='trees':
                rows=dict(self.trees.get(data.get('base_tree'), {}))
                rows.update({row['path']:row for row in data['tree']})
                return {'sha':self.object(self.trees, rows)}
            if kind=='commits':
                return {'sha':self.object(self.commits, data)}
        if kind=='commits':
            return {'tree':{'sha':self.commits[suffix]['tree']}}
        if kind=='trees':
            return {'tree':list(self.trees[suffix.split('?')[0]].values()), 'truncated':False}
        if kind=='blobs':
            raw=base64.b64decode(self.blobs[suffix]['content'])
            return {**self.blobs[suffix], 'size':len(raw)}
        raise AssertionError((route, method, data))

    def retain(self, namespace, key, value):
        path=state.record_path(namespace,key)
        raw=state.sources.canonical({'schema':1,'digest':state.digest(value),'value':value})
        blob=self.api('/git/blobs','POST',{'content':base64.b64encode(raw).decode(),'encoding':'base64'})['sha']
        body={'tree':[{'path':path,'mode':'100644','type':'blob','sha':blob}]}
        if self.tip:body['base_tree']=self.commits[self.tip]['tree']
        tree=self.api('/git/trees','POST',body)['sha']
        self.tip=self.api('/git/commits','POST',{'tree':tree,'parents':[self.tip] if self.tip else [],'message':'competing write'})['sha']

    def push(self, cwd, sha, branch, previous):
        self.pushes+=1
        if self.before_push:
            action=self.before_push
            self.before_push=None
            action()
        if self.tip!=previous:
            raise self.failure
        self.tip=sha

    def value(self, namespace, key):
        row=self.trees[self.commits[self.tip]['tree']][state.record_path(namespace,key)]
        return state.decode(json.loads(base64.b64decode(self.blobs[row['sha']]['content'])))


class ReceiptConcurrency(unittest.TestCase):
    def setUp(self):
        self.store=ReceiptStore()
        stack=ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(u,'ref_head',side_effect=lambda branch:self.store.tip))
        stack.enter_context(patch.object(u,'api',side_effect=self.store.api))
        stack.enter_context(patch.object(u,'repository',return_value='owner/repo'))
        stack.enter_context(patch.object(state.sources,'git'))
        stack.enter_context(patch.object(u,'push_ref',side_effect=self.store.push))

    def test_competing_receipt_preserves_both_writes_and_other_namespaces(self):
        self.store.retain('approved','original',{'approved':True})
        self.store.before_push=lambda:self.store.retain('proposal','other',{'source':'other'})
        state.save('candidate','ours',{'source':'ours'})
        self.assertEqual(self.store.value('candidate','ours'),{'source':'ours'})
        self.assertEqual(self.store.value('proposal','other'),{'source':'other'})
        self.assertEqual(self.store.value('approved','original'),{'approved':True})

    def test_competing_identical_identity_is_reused_without_another_push(self):
        value={'source':'ours'}
        self.store.before_push=lambda:self.store.retain('candidate','ours',value)
        self.assertEqual(state.save('candidate','ours',value),state.digest(value))
        self.assertEqual(self.store.value('candidate','ours'),value)
        self.assertEqual(self.store.pushes,1)

    def test_competing_divergent_identity_remains_immutable(self):
        self.store.before_push=lambda:self.store.retain('candidate','ours',{'source':'theirs'})
        with self.assertRaisesRegex(ValueError,'immutable'):
            state.save('candidate','ours',{'source':'ours'})
        self.assertEqual(self.store.value('candidate','ours'),{'source':'theirs'})
        self.assertEqual(self.store.pushes,1)

    def test_unchanged_tip_push_failure_propagates_original_error(self):
        def fail():
            raise self.store.failure
        self.store.before_push=fail
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            state.save('candidate','ours',{'source':'ours'})
        self.assertIs(caught.exception,self.store.failure)
        self.assertIsNone(self.store.tip)
        self.assertEqual(self.store.pushes,1)

    def test_continuing_contention_fails_closed_at_bound(self):
        def compete():
            self.store.retain('proposal','other',{'revision':self.store.pushes})
            self.store.before_push=compete
        self.store.before_push=compete
        with self.assertRaisesRegex(ValueError,'retry bound'):
            state.save('candidate','ours',{'source':'ours'})
        self.assertEqual(self.store.pushes,32)
        self.assertEqual(self.store.value('proposal','other'),{'revision':32})
        rows=self.store.trees[self.store.commits[self.store.tip]['tree']]
        self.assertNotIn(state.record_path('candidate','ours'),rows)

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
        C, B, H = (value*40 for value in 'cba')
        batch = {'schema': 1, 'base': C, 'receipts': [
            {'pkgbase': 'example', 'watcher_id': 'version', 'base': C,
             'recipe_commit': B, 'unchanged': False}]}
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
            api = stack.enter_context(patch.object(u, 'api', side_effect=response))
            stack.enter_context(patch.object(state, 'protected_branch'))
            stack.enter_context(patch.object(state, 'load', return_value={'proposal_origin': 'manual'}))
            push = stack.enter_context(patch.object(u, 'push_ref'))
            attest = stack.enter_context(patch.object(state, 'attest'))
            with self.assertRaisesRegex(ValueError, 'user-edited proposal'):
                u.write_proposals('unused')
        push.assert_not_called()
        attest.assert_not_called()
        self.assertFalse(any(call.args[1:2] in [('POST',),('PATCH',)] for call in api.call_args_list))

    def test_unchanged_retained_legacy_branch_does_not_block_later_proposal(self):
        workspace=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        workspace.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace) as directory,ExitStack() as stack:
            root=Path(directory);control=root/'control';batch_dir=root/'batch'
            C,B,H=(value*40 for value in 'cba')
            legacy_branch='recipe-updates/cloudflare/version'
            omp_branch='recipe-updates/omp/version'
            refs={'pkg/cloudflare':B,'pkg/omp':B,legacy_branch:H}
            blobs={};trees={};commits={};proposals={};receipts={};dispatches=[]
            policy={'sources':[],'automatic':{
                'version':{'assignment':'pkgver','literal_assignment':'pkgver=1.0'},
                'pkgrel':{'assignment':'pkgrel','literal_assignment':'pkgrel=1'}}}
            provenance={'aur':None,'watchers':[{'id':'version','kind':'release'}]}
            accepted=control/'recipes/omp';accepted.mkdir(parents=True)
            original='pkgver=1.0\npkgrel=1\npackage() { echo preserved; }\n'
            metadata='pkgbase = omp\n\tpkgver = 1.1\n\tpkgrel = 1\n\tarch = x86_64\npkgname = omp\n'
            (accepted/'PKGBUILD').write_text(original)
            (accepted/'.SRCINFO').write_text(metadata.replace('1.1','1.0'))
            submitted=batch_dir/'omp/version/recipe';u.recipes.copy_recipe(accepted,submitted)
            (submitted/'PKGBUILD').write_text(original.replace('1.0','1.1'))
            (submitted/'.SRCINFO').write_text(metadata)
            for name in ('cloudflare','omp'):
                u.dump(control/'upstream'/f'{name}.json',provenance)
            u.dump(control/'inputs/omp.json',{'schema':1,'version':'1.0-1','sources':[]})
            u.dump(batch_dir/'receipts.json',{'schema':1,'base':C,'receipts':[
                {'pkgbase':'cloudflare','watcher_id':'version','base':C,'recipe_commit':B,'unchanged':True},
                {'pkgbase':'omp','watcher_id':'version','base':C,'recipe_commit':B,'unchanged':False,
                 'lock':{'schema':1,'version':'1.1-1','sources':[]},'provenance':provenance,
                 'metadata':{'srcinfo':metadata,'pkgver':'1.1','pkgrel':'1'},
                 'tree':u.tree_manifest(submitted)}]})

            def ref_head(branch):
                if branch==legacy_branch:
                    raise AssertionError('no-op must not inspect retained branch')
                return refs.get(branch)

            def api(path,method='GET',data=None):
                endpoint=path.removeprefix('/repos/owner/repo')
                if endpoint.startswith('/pulls?'):
                    return []
                if endpoint=='/git/blobs' and method=='POST':
                    content=base64.b64decode(data['content'])
                    sha=hashlib.sha1(content).hexdigest();blobs[sha]=content
                    return {'sha':sha}
                if endpoint=='/git/trees' and method=='POST':
                    sha='d'*40;trees[sha]=data['tree']
                    return {'sha':sha}
                if endpoint=='/git/commits' and method=='POST':
                    sha='e'*40;commits[sha]=data
                    return {'sha':sha}
                if endpoint=='/pulls' and method=='POST':
                    number=7
                    proposals[number]={**data,'number':number,'head':{'sha':refs[data['head']]}}
                    return proposals[number]
                if endpoint=='/pulls/7' and method=='GET':
                    return proposals[7]
                if endpoint=='/actions/workflows/candidate.yml/dispatches' and method=='POST':
                    dispatches.append(data)
                    return None
                raise AssertionError((endpoint,method,data))

            def push_ref(cwd,sha,branch,previous):
                self.assertEqual(refs.get(branch),previous)
                refs[branch]=sha

            stack.enter_context(patch.object(u,'ROOT',control))
            stack.enter_context(patch.object(u,'main_sha',return_value=C))
            stack.enter_context(patch.object(u,'control_checkout',return_value=(C,{'cloudflare':B,'omp':B})))
            stack.enter_context(patch.object(u,'policy_at',return_value={'cloudflare':policy,'omp':policy}))
            stack.enter_context(patch.object(u,'ref_head',side_effect=ref_head))
            stack.enter_context(patch.object(u,'api',side_effect=api))
            stack.enter_context(patch.object(u,'push_ref',side_effect=push_ref))
            stack.enter_context(patch.object(u.sources,'git'))
            stack.enter_context(patch.object(state,'protected_branch'))
            stack.enter_context(patch.object(state,'load',return_value={'proposal_origin':'legacy'}))
            stack.enter_context(patch.object(state,'load_optional',side_effect=lambda kind,key:receipts.get(key)))
            stack.enter_context(patch.object(state,'save',side_effect=lambda kind,key,value:receipts.update({key:value})))
            stack.enter_context(patch.object(state,'attest'))
            u.write_proposals(batch_dir)

            self.assertEqual(refs,{'pkg/cloudflare':B,'pkg/omp':B,legacy_branch:H,omp_branch:'e'*40})
            commit=commits[refs[omp_branch]]
            self.assertEqual(commit['parents'],[B])
            published={entry['path']:blobs[entry['sha']].decode() for entry in trees[commit['tree']]}
            self.assertEqual(published,{'PKGBUILD':original.replace('1.0','1.1'),'.SRCINFO':metadata})
            self.assertEqual(proposals[7]['base'],'pkg/omp')
            self.assertEqual(dispatches,[{'ref':'main','inputs':{'pr_number':'7','expected_head':'e'*40}}])

    def test_unchanged_open_proposal_still_refuses_unowned_refresh(self):
        C,B,H=(value*40 for value in 'cba')
        batch={'schema':1,'base':C,'receipts':[
            {'pkgbase':'example','watcher_id':'version','base':C,'recipe_commit':B,'unchanged':True}]}
        def response(path,*args):
            if '/pulls?' in path:
                return [{'number':3,'head':{'sha':H},'base':{'sha':B}}]
            return {'tree':{'sha':'d'*40},'parents':[{'sha':B}]}
        with ExitStack() as stack:
            stack.enter_context(patch.object(u,'load',side_effect=[batch,{'watchers':[{'id':'version'}]}]))
            stack.enter_context(patch.object(u,'main_sha',return_value=C))
            stack.enter_context(patch.object(u,'control_checkout',return_value=(C,{'example':B})))
            stack.enter_context(patch.object(u,'policy_at',return_value={'example':{}}))
            stack.enter_context(patch.object(u,'ref_head',return_value=B))
            api=stack.enter_context(patch.object(u,'api',side_effect=response))
            stack.enter_context(patch.object(state,'protected_branch'))
            stack.enter_context(patch.object(state,'load',return_value={'proposal_origin':'manual'}))
            push=stack.enter_context(patch.object(u,'push_ref'))
            with self.assertRaisesRegex(ValueError,'user-edited proposal'):
                u.write_proposals('unused')
        push.assert_not_called()
        self.assertFalse(any(call.args[1:2] in [('POST',),('PATCH',)] for call in api.call_args_list))

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
            (root/'tools/recipe_state.py').write_text('CONTROLS = '+repr(state.CONTROLS)+'\n')
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
