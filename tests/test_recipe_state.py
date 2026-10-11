import base64
from contextlib import ExitStack
import hashlib
import json
import os
import re
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
        root = Path.home() / '.local/state/omp/work/arch-control-tests'
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
        self.graphql_reads=0
        self.failure=subprocess.CalledProcessError(1, ['git', 'push'], stderr=b'rejected')

    def object(self, objects, value):
        sha=hashlib.sha1(state.sources.canonical(value)).hexdigest()
        objects[sha]=value
        return sha

    def api(self, route, method='GET', data=None):
        if route=='graphql':
            self.graphql_reads+=1
            rows={}
            for alias,expression in re.findall(r'(r[0-9]+): object\(expression: ("[^"]+")\)',data['query']):
                tip,path=json.loads(expression).split(':',1)
                row=self.trees[self.commits[tip]['tree']].get(path)
                if row is None:
                    rows[alias]=None
                else:
                    raw=base64.b64decode(self.blobs[row['sha']]['content'])
                    rows[alias]={'__typename':'Blob','byteSize':len(raw),'isBinary':False,'text':raw.decode()}
            return {'data':{'repository':rows}}
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

class BuildLifecycle(unittest.TestCase):
    setUp=ReceiptConcurrency.setUp
    def request(self,request_id='request-1',head='a'*40,commit='b'*40):
        return {'request_id':request_id,'repository':'owner/repo','base':'c'*40,
                'head':head,'pkgbase':'example','pr_number':3,'packages':[
                    {'pkgbase':'example','recipe_commit':head,'lock':{
                        'version':'1-1','sources':[{'id':'source','kind':'git',
                        'url':'https://example.com/repo','ref':'refs/heads/main',
                        'commit':commit,'git_context':{'version_tag':None}}]}}]}

    def producer(self,run='17'):
        return {'run_id':run,'run_attempt':'1','head_sha':'c'*40,
                'path':'.github/workflows/build-package.yml','pkgbase':'example'}

    def result(self,request):
        return {'schema':3,'request_id':request['request_id'],'pkgbase':'example',
                'record':request,'producer':self.producer(),'asset':{'id':1}}

    def complete(self,request,outcome='success'):
        state.reserve_build(request)
        state.register_build('example',request['request_id'],self.producer())
        if outcome=='success':
            state.put_build_result(request['request_id'],self.result(request))
        return state.finish_build('example',request['request_id'],outcome)

    def test_simultaneous_reservations_dispatch_only_one_request(self):
        first=self.request()
        second=self.request('request-2')
        winner=[]
        self.store.before_push=lambda:winner.append(state.reserve_build(second))
        pending,created=state.reserve_build(first)
        self.assertFalse(created)
        self.assertTrue(winner[0][1])
        self.assertEqual(pending['request_id'],'request-2')
        self.assertNotIn(state.record_path('build-request','request-1'),
                         self.store.trees[self.store.commits[self.store.tip]['tree']])

    def assert_same_inputs_are_read_only(self,outcome):
        completed=self.complete(self.request(),outcome)
        pushes=self.store.pushes
        self.assertEqual(state.reserve_build(self.request('request-2')),(completed,False))
        self.assertEqual(self.store.pushes,pushes)

    def test_same_inputs_do_not_retry_failure(self):
        self.assert_same_inputs_are_read_only('failure')

    def test_same_inputs_reuse_success(self):
        self.assert_same_inputs_are_read_only('success')

    def test_failure_retry_requires_new_identity(self):
        self.complete(self.request(),'failure')
        with self.assertRaisesRegex(ValueError,'new build request'):
            state.reserve_build(self.request(),retry=True)
        pending,created=state.reserve_build(self.request('request-2'),retry=True)
        self.assertTrue(created)
        self.assertEqual(pending['state'],'queued')
        self.assertIsNone(pending['worker'])

    def test_changed_sources_and_heads_replace_only_completed_requests(self):
        first=self.request()
        state.reserve_build(first)
        changed=self.request('request-2',commit='d'*40)
        self.assertFalse(state.reserve_build(changed)[1])
        self.complete(first,'failure')
        self.assertTrue(state.reserve_build(changed)[1])
        self.complete(changed)
        next_head=self.request('request-3',head='e'*40,commit='d'*40)
        self.assertTrue(state.reserve_build(next_head)[1])

    def test_tag_context_change_is_a_new_input(self):
        self.complete(self.request(),'failure')
        changed=self.request('request-2')
        changed['packages'][0]['lock']['sources'][0]['git_context']['version_tag']={
            'name':'refs/tags/v2','object':'d'*40,'commit':'b'*40}
        self.assertTrue(state.reserve_build(changed)[1])

    def test_stale_registration_and_result_cannot_touch_new_request(self):
        first=self.request()
        self.complete(first,'failure')
        second=self.request('request-2')
        state.reserve_build(second,retry=True)
        before=state.pending_build('example')
        with self.assertRaisesRegex(ValueError,'stale'):
            state.register_build('example','request-1',self.producer())
        with self.assertRaisesRegex(ValueError,'stale'):
            state.put_build_result('request-1',self.result(first))
        with self.assertRaisesRegex(ValueError,'stale'):
            state.finish_build('example','request-1','failure')
        self.assertEqual(state.pending_build('example'),before)

    def test_second_worker_and_mismatched_result_fail_closed(self):
        request=self.request()
        state.reserve_build(request)
        registered=state.register_build('example','request-1',self.producer())
        self.assertEqual(state.register_build('example','request-1',self.producer()),registered)
        with self.assertRaisesRegex(ValueError,'different worker'):
            state.register_build('example','request-1',self.producer('18'))
        result=self.result(request)
        result['producer']=self.producer('18')
        with self.assertRaisesRegex(ValueError,'producer'):
            state.put_build_result('request-1',result)
        with self.assertRaisesRegex(ValueError,'missing durable build-result'):
            state.finish_build('example','request-1','success')

    def test_result_is_not_whole_success_and_failed_output_is_unusable(self):
        request=self.request()
        state.reserve_build(request)
        state.register_build('example','request-1',self.producer())
        result=self.result(request)
        state.put_build_result('request-1',result)
        self.assertEqual(state.pending_build('example')['state'],'running')
        pending=state.finish_build('example','request-1','failure')
        self.assertIsNone(pending['result'])
        self.assertEqual(pending['state'],'failure')
        with self.assertRaisesRegex(ValueError,'outcome differs'):
            state.finish_build('example','request-1','success')

    def test_outer_snapshot_does_not_supply_a_write_lease(self):
        request=self.request()
        with state.read_snapshot() as snapshot:
            self.assertIsNone(state.pending_build('example'))
            pending,_=state.reserve_build(request)
            self.assertIsNone(state.pending_build('example'))
            running=state.register_build('example','request-1',self.producer())
            self.assertEqual(running['state'],'running')
            self.assertEqual(snapshot.tip,None)
        self.assertEqual(state.pending_build('example')['request_id'],pending['request_id'])

    def test_snapshot_batches_caches_and_returns_detached_values(self):
        self.store.retain('proposal','one',{'value':1})
        self.store.retain('proposal','two',{'value':2})
        with state.read_snapshot() as snapshot:
            values=snapshot.load_many([('proposal','one'),('proposal','two'),('proposal','missing')])
            self.assertEqual(self.store.graphql_reads,1)
            values[('proposal','one')]['value']=99
            self.store.retain('proposal','one',{'value':3})
            self.assertEqual(state.load('proposal','one'),{'value':1})
            self.assertIsNone(state.load_optional('proposal','missing'))
            self.assertEqual(self.store.graphql_reads,1)
            self.assertEqual(state.keys('proposal'),['one','two'])
        self.assertEqual(state.load('proposal','one'),{'value':3})

    def test_exact_expected_pointer_blocks_outdated_proposal(self):
        current=self.complete(self.request(),'failure')
        request=self.request('request-2',head='d'*40)
        request['expected_pending']='outdated'
        self.assertEqual(state.reserve_build(request),(current,False))

    def test_result_write_cas_cannot_retain_stale_output(self):
        first=self.request()
        state.reserve_build(first)
        state.register_build('example','request-1',self.producer())
        def replace():
            state.finish_build('example','request-1','failure')
            state.reserve_build(self.request('request-2'),retry=True)
        self.store.before_push=replace
        with self.assertRaisesRegex(ValueError,'stale'):
            state.put_build_result('request-1',self.result(first))
        self.assertEqual(state.pending_build('example')['request_id'],'request-2')
        self.assertIsNone(state.load_optional('build-result','request-1'))

    def test_success_and_result_are_duplicate_safe(self):
        request=self.request()
        pending=self.complete(request)
        pushes=self.store.pushes
        self.assertEqual(state.finish_build('example','request-1','success'),pending)
        self.assertEqual(state.put_build_result('request-1',self.result(request)),
                         state.digest(self.result(request)))
        self.assertEqual(self.store.pushes,pushes)

    def test_request_identity_and_producer_claims_are_validated(self):
        for mutation in (
                {'request_id':'../escape'}, {'repository':'other/repo'},
                {'head':'bad'}, {'pr_number':False}):
            request={**self.request(),**mutation}
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                state.reserve_build(request)
        state.reserve_build(self.request())
        for mutation in ({'run_id':True},{'run_attempt':'0'},
                         {'head_sha':'invalid'},{'legacy':True},
                         {'path':'.github/workflows/candidate.yml'}):
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                state.register_build('example','request-1',{**self.producer(),**mutation})

    def abandonment_pr(self,request,state_='open',head=None):
        return {'number':request['pr_number'],'state':state_,
                'base':{'ref':'pkg/example','sha':request['recipe_base'],
                        'repo':{'full_name':'owner/repo'}},
                'head':{'ref':request['head_branch'],'sha':head or request['head'],
                        'repo':{'full_name':'owner/repo'}}}

    def test_automatic_abandon_requires_closed_or_superseded_exact_pr(self):
        request={**self.request(),'recipe_base':'f'*40,'head_branch':'feature/example'}
        state.reserve_build(request)
        pr=self.abandonment_pr(request)
        def api(route,method='GET',data=None):
            if '/pulls/' in route:return pr
            return self.store.api(route,method,data)
        with patch.object(u,'api',side_effect=api):
            with self.assertRaisesRegex(ValueError,'explicit retry'):
                state.abandon_build('example','request-1')
            pr['head']['sha']='e'*40
            failed=state.abandon_build('example','request-1')
            self.assertEqual(failed['state'],'failure')
            self.assertIsNone(failed['worker'])
            self.assertIsNone(failed['result'])
            self.assertEqual(state.abandon_build('example','request-1'),failed)
        with self.assertRaisesRegex(ValueError,'not queued'):
            state.register_build('example','request-1',self.producer())

    def test_closed_pr_can_abandon_unregistered_request(self):
        request={**self.request(),'recipe_base':'f'*40,'head_branch':'feature/example'}
        state.reserve_build(request)
        pr=self.abandonment_pr(request,state_='closed')
        def api(route,method='GET',data=None):
            if '/pulls/' in route:return pr
            return self.store.api(route,method,data)
        with patch.object(u,'api',side_effect=api):
            self.assertEqual(state.abandon_build('example','request-1')['state'],'failure')

    def test_automatic_abandon_rejects_foreign_or_retargeted_pr(self):
        request={**self.request(),'recipe_base':'f'*40,'head_branch':'feature/example'}
        state.reserve_build(request)
        for mutation in ('repository','base','number'):
            pr=self.abandonment_pr(request,state_='closed')
            if mutation=='repository':pr['head']['repo']['full_name']='other/repo'
            elif mutation=='base':pr['base']['ref']='main'
            else:pr['number']=4
            def api(route,method='GET',data=None):
                if '/pulls/' in route:return pr
                return self.store.api(route,method,data)
            with self.subTest(mutation=mutation),patch.object(u,'api',side_effect=api),self.assertRaisesRegex(ValueError,'PR identity'):
                state.abandon_build('example','request-1')
        self.assertEqual(state.pending_build('example')['state'],'queued')

    def test_explicit_retry_abandons_missing_registration_without_expiry(self):
        request=self.request()
        state.reserve_build(request)
        failed=state.abandon_build('example','request-1',retry=True)
        self.assertEqual(failed['state'],'failure')
        self.assertFalse(state.reserve_build(self.request('request-2'))[1])
        self.assertTrue(state.reserve_build(self.request('request-2'),retry=True)[1])
        with self.assertRaisesRegex(ValueError,'stale'):
            state.register_build('example','request-1',self.producer())

    def test_concurrent_registration_wins_over_abandonment_cas(self):
        request=self.request()
        state.reserve_build(request)
        self.store.before_push=lambda:state.register_build('example','request-1',self.producer())
        with self.assertRaisesRegex(ValueError,'registered'):
            state.abandon_build('example','request-1',retry=True)
        pending=state.pending_build('example')
        self.assertEqual(pending['state'],'running')
        self.assertEqual(pending['worker'],self.producer())

    def test_registered_worker_requires_actual_whole_outcome(self):
        request=self.request()
        state.reserve_build(request)
        state.register_build('example','request-1',self.producer())
        with self.assertRaisesRegex(ValueError,'registered'):
            state.abandon_build('example','request-1',retry=True)
        self.assertEqual(state.pending_build('example')['state'],'running')

    def test_current_controller_producer_binds_original_request_without_mutation(self):
        request=self.request()
        state.reserve_build(request)
        producer={**self.producer(),'head_sha':'d'*40}
        pending=state.register_build('example','request-1',producer)
        self.assertEqual(pending['worker'],producer)
        self.assertEqual(state.build_request('request-1'),request)
        result={**self.result(request),'producer':producer}
        state.put_build_result('request-1',result)
        self.assertEqual(state.finish_build('example','request-1','success')['state'],'success')

    def test_snapshot_rejects_graphql_errors_and_oversize_blobs(self):
        self.store.retain('proposal','one',{'value':1})
        for response in (
                {'errors':[{'message':'unavailable'}]},
                {'data':{'repository':{'r0':{
                    '__typename':'Blob','isBinary':False,
                    'byteSize':u.MAX_TREE+1,'text':'{}'}}}}):
            with patch.object(u,'api',return_value=response),self.assertRaises(ValueError):
                state.StateSnapshot().load('proposal','one')

    def test_pending_cannot_be_written_without_cas(self):
        with self.assertRaisesRegex(ValueError,'CAS'):
            state.save('pending','example',{})


class SourceTestLifecycle(unittest.TestCase):
    setUp=ReceiptConcurrency.setUp

    def evidence(self,run='20',attempt='1'):
        return {'schema':1,'repository':'owner/repo','base':'c'*40,
                'head':'a'*40,'tree':'b'*40,'pr_number':3,
                'producer':{'run_id':run,'run_attempt':attempt,'head_sha':'c'*40,
                            'path':'.github/workflows/candidate.yml'},
                'image':'ghcr.io/archlinux/archlinux@sha256:'+'d'*64}

    def test_same_head_retry_has_distinct_immutable_receipts(self):
        first=self.evidence()
        retry=self.evidence(attempt='2')
        first_key=state.save_source_test(first)
        retry_key=state.save_source_test(retry)
        self.assertNotEqual(first_key,retry_key)
        self.assertEqual(state.load('source-test',first_key),first)
        self.assertEqual(state.source_test_evidence('a'*40),retry)

    def test_newest_issuer_wins_numeric_order_not_string_order(self):
        older=self.evidence(run='9',attempt='12')
        newer=self.evidence(run='10',attempt='1')
        state.save_source_test(newer)
        state.save_source_test(older)
        self.assertEqual(state.source_test_evidence('a'*40),newer)
        retry=self.evidence(run='10',attempt='2')
        state.save_source_test(retry)
        self.assertEqual(state.source_test_evidence('a'*40),retry)

    def test_competing_newer_issuer_cannot_be_overwritten_by_old_cas(self):
        older=self.evidence(run='20')
        newer=self.evidence(run='21')
        self.store.before_push=lambda:state.save_source_test(newer)
        old_key=state.save_source_test(older)
        self.assertEqual(state.source_test_evidence('a'*40),newer)
        self.assertEqual(state.load('source-test',old_key),older)

    def test_duplicate_save_is_read_only_and_divergence_is_rejected(self):
        evidence=self.evidence()
        key=state.save_source_test(evidence)
        pushes=self.store.pushes
        self.assertEqual(state.save_source_test(evidence),key)
        self.assertEqual(self.store.pushes,pushes)
        with self.assertRaisesRegex(ValueError,'immutable'):
            state.save_source_test({**evidence,'tree':'e'*40})
        self.assertEqual(state.source_test_evidence('a'*40),evidence)

    def test_no_legacy_sha_fallback_and_index_is_cas_only(self):
        self.store.retain('source-test','a'*40,self.evidence())
        self.assertIsNone(state.source_test_evidence('a'*40))
        with self.assertRaisesRegex(ValueError,'CAS'):
            state.save('source-test-index','a'*40,{})

    def test_index_must_bind_exact_immutable_record(self):
        evidence=self.evidence()
        state.save_source_test(evidence)
        key='a'*40+'-20-1'
        pointer={'schema':1,'head':'a'*40,'key':key,'run_id':'20','run_attempt':'1'}
        self.store.retain('source-test',key,{**evidence,'head':'e'*40})
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            state.source_test_evidence('a'*40)
        self.store.retain('source-test-index','a'*40,{**pointer,'run_attempt':'2'})
        with self.assertRaisesRegex(ValueError,'index identity'):
            state.source_test_evidence('a'*40)

    def test_save_uses_fresh_tip_even_inside_cached_operation(self):
        with state.read_snapshot():
            self.assertIsNone(state.source_test_evidence('a'*40))
            state.save_source_test(self.evidence())
            self.assertIsNone(state.source_test_evidence('a'*40))
        self.assertEqual(state.source_test_evidence('a'*40),self.evidence())

    def test_invalid_issuer_does_not_update_index(self):
        for producer in (
                {'run_id':'0'}, {'run_attempt':True}, {'head_sha':'invalid'},
                {'path':'.github/workflows/untrusted.yml'}):
            evidence=self.evidence()
            evidence['producer']={**evidence['producer'],**producer}
            with self.subTest(producer=producer),self.assertRaises(ValueError):
                state.save_source_test(evidence)
        self.assertIsNone(self.store.tip)


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

    def test_controller_movement_preserves_exact_recipe_build_identity(self):
        record=self.record()
        pr=self.pr()
        current='e'*40
        with patch.object(u,'repository',return_value='owner/repo'),patch.object(u,'main_sha',return_value=current),patch.object(u,'trusted_checkout_sha',return_value=current),patch.object(u,'ref_head',return_value=record['recipe_base']),patch.object(u,'api',side_effect=[pr,{'protected':True},{'tree':{'sha':record['recipe_tree']}}]):
            self.assertEqual(state.assert_recipe_identity(record),pr)

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
            stack.enter_context(patch.object(u, 'open_pr_snapshot', return_value=[]))
            stack.enter_context(patch.object(state, 'load_many', return_value={('pending','example'):None}))
            stack.enter_context(patch.object(state, 'protected_branch'))
            stack.enter_context(patch.object(state, 'load', return_value={'proposal_origin': 'manual'}))
            push = stack.enter_context(patch.object(u, 'push_ref'))
            attest = stack.enter_context(patch.object(state, 'attest'))
            with self.assertRaises(ValueError):
                u.write_proposals('unused')
        push.assert_not_called()
        attest.assert_not_called()
        self.assertFalse(any(call.args[1:2] in [('POST',),('PATCH',)] for call in api.call_args_list))

    def test_open_proposal_preserves_unowned_human_head(self):
        C,B,H=(value*40 for value in 'cba')
        batch={'schema':1,'base':C,'receipts':[
            {'pkgbase':'example','watcher_id':'version','base':C,'recipe_commit':B,'unchanged':False}]}
        pr={'number':3,'head':{'sha':H,'ref':'recipe-updates/example'},
            'base':{'sha':B,'ref':'pkg/example'}}
        def response(path,*args):
            return {'tree':{'sha':'d'*40},'parents':[{'sha':B}]}
        with ExitStack() as stack:
            stack.enter_context(patch.object(u,'load',side_effect=[batch,{'watchers':[{'id':'version'}]}]))
            stack.enter_context(patch.object(u,'main_sha',return_value=C))
            stack.enter_context(patch.object(u,'control_checkout',return_value=(C,{'example':B})))
            stack.enter_context(patch.object(u,'policy_at',return_value={'example':{}}))
            stack.enter_context(patch.object(u,'ref_head',return_value=B))
            api=stack.enter_context(patch.object(u,'api',side_effect=response))
            stack.enter_context(patch.object(u,'open_pr_snapshot',return_value=[pr]))
            stack.enter_context(patch.object(state,'load_many',return_value={('pending','example'):None}))
            stack.enter_context(patch.object(state,'protected_branch'))
            stack.enter_context(patch.object(state,'load',return_value={'proposal_origin':'manual'}))
            push=stack.enter_context(patch.object(u,'push_ref'))
            dispatch=stack.enter_context(patch.object(u,'dispatch'))
            u.write_proposals('unused')
        dispatch.assert_not_called()
        push.assert_not_called()
        self.assertFalse(any(call.args[1:2] in [('POST',),('PATCH',)] for call in api.call_args_list))

    def test_immutable_record_refuses_same_key_with_different_frozen_source(self):
        with patch.object(state.StateSnapshot,'load_optional',return_value={'source':'original'}),patch.object(u,'ref_head',return_value='a'*40),self.assertRaisesRegex(ValueError,'immutable'):
            state.save('proposal','a'*40,{'source':'replacement'})

    def test_retry_preserves_receipt_content_identity(self):
        value={'source':'original','head':'a'*40}
        with patch.object(state.StateSnapshot,'load_optional',return_value=value),patch.object(u,'ref_head',return_value='a'*40),patch.object(u,'api') as api:
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
        workspace=Path.home()/'.local/state/omp/work/recipe-state-tests'
        workspace.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace) as directory:
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

    def test_receipt_listing_is_bounded_to_namespace_and_prefix(self):
        rows={'tree':[{'path':'proposal/tuple-one.json','type':'blob','mode':'100644'},
                      {'path':'proposal/other.json','type':'blob','mode':'100644'},
                      {'path':'acceptance/tuple-one.json','type':'blob','mode':'100644'}]}
        with patch.object(u,'ref_head',return_value='a'*40),patch.object(u,'api',side_effect=[{'tree':{'sha':'b'*40}},rows]):
            self.assertEqual(state.keys('proposal','tuple-'),['tuple-one'])

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
