import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import source_review, update


class SourceReviewTests(unittest.TestCase):
    def test_source_only_changes_cannot_hide_package_changes(self):
        root=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as session:
            old=Path(session)/'old';new=Path(session)/'new'
            for tree in (old,new):
                update.dump(tree/'packages.json',{'schema':1,'packages':[{'pkgbase':'example'}]})
                (tree/'recipes/example').mkdir(parents=True)
                (tree/'recipes/example/PKGBUILD').write_text('package() { echo accepted; }')
                for directory in ('inputs','upstream','acceptance'):
                    (tree/directory).mkdir();(tree/directory/'example.json').write_text('frozen source')
                (tree/'.gitmodules').write_text('accepted enrollment')
            (new/'build-image.txt').write_text('different image')
            (new/'tools').mkdir();(new/'tools/controller.py').write_text('approved changed code')
            pins={'example':'1'*40}
            source_review.unchanged_packages(old,new,pins,pins)
            for path in ('recipes/example/PKGBUILD','inputs/example.json','upstream/example.json','acceptance/example.json','.gitmodules'):
                target=new/path;before=target.read_text();target.write_text('changed')
                with self.subTest(path=path),self.assertRaisesRegex(ValueError,'source-only route'):
                    source_review.unchanged_packages(old,new,pins,pins)
                target.write_text(before)
            with self.assertRaisesRegex(ValueError,'pins'):
                source_review.unchanged_packages(old,new,pins,{'example':'2'*40})

    def test_approval_is_bound_to_exact_workflow_attempt_and_environment(self):
        base='a'*40;head='b'*40
        pr={'draft':True,'head':{'ref':'feature','repo':{'full_name':'owner/repo'}}}
        run={'head_sha':base,'head_branch':'main','event':'workflow_dispatch',
             'path':'.github/workflows/verification.yml','run_attempt':2,'run_started_at':'2026-10-09T12:00:00Z',
             'display_title':f'Source review PR 7 head {head} base {base}'}
        jobs={'jobs':[{'id':91,'name':f'Approve source PR 7 head {head} base {base}','conclusion':'success'}]}
        history=[{'state':'approved','user':{'type':'User'},'environments':[{'id':13,'name':'code-review'}]}]
        environment={'id':13,'protection_rules':[{'type':'required_reviewers','reviewers':[{'type':'User','reviewer':{'id':42,'type':'User'}}]}]}
        tree={'truncated':False,'tree':[{'path':'packages.json','mode':'100644','type':'blob','sha':'d'*40},
                                      {'path':'recipes/example','mode':'160000','type':'commit','sha':'e'*40},
                                      {'path':'.github/workflows/verification.yml','mode':'100644','type':'blob','sha':'f'*40}]}
        review={'state':'APPROVED','commit_id':head,'user':{'id':42,'type':'User'},'submitted_at':'2026-10-09T11:00:00Z'}
        tag={'ref':'refs/tags/source-review-'+head,'object':{'type':'commit','sha':head}}
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'8','GITHUB_RUN_ATTEMPT':'2','GITHUB_SHA':base,'GITHUB_REF':'refs/heads/main'}),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'pr_identity',return_value=(pr,base,head)),patch.object(update,'require_review_environment'):
            def api(path):
                if path.endswith('/attempts/2'):return run
                if path.endswith('/environments/code-review'):return environment
                if '/git/trees/' in path:return tree
                if path.endswith('/pulls/7/reviews/4'):return review
                if '/git/ref/tags/' in path:return tag
                if '/jobs?' in path:return jobs
                if path.endswith('/approvals'):return history
                raise AssertionError(path)
            with patch.object(update,'api',side_effect=api):
                self.assertEqual(source_review.authority(7,base,head)['job_id'],91)
                for field,value in [('head_sha',head),('head_sha','c'*40),('head_branch','feature'),('run_attempt',1),('path','.github/workflows/candidate.yml')]:
                    before=run[field];run[field]=value
                    with self.subTest(field=field),self.assertRaisesRegex(ValueError,'workflow identity'):
                        source_review.authority(7,base,head)
                    run[field]=before
                for field,value in [('GITHUB_SHA',head),('GITHUB_REF','refs/heads/feature')]:
                    with self.subTest(environment=field),patch.dict(os.environ,{field:value}),self.assertRaisesRegex(ValueError,'workflow identity'):
                        source_review.authority(7,base,head)
                run.update(head_sha=head,head_branch='feature')
                with patch.dict(os.environ,{'GITHUB_SHA':head,'GITHUB_REF':'refs/heads/feature'}),self.assertRaisesRegex(ValueError,'workflow identity'):
                    source_review.authority(7,base,head)
                run.update(head_sha=base,head_branch='main')
                review['body']=source_review.admission_body(7,base,head)
                run.update(head_sha=head,head_branch='source-review-'+head)
                with patch.dict(os.environ,{'GITHUB_SHA':head,'GITHUB_REF':'refs/tags/source-review-'+head}):
                    self.assertEqual(source_review.authority(7,base,head,bootstrap=True,admission=4)['job_id'],91)
                    review['state']='COMMENTED'
                    self.assertEqual(source_review.authority(7,base,head,bootstrap=True,admission=4)['job_id'],91)
                    for field,value in [('state','DISMISSED'),('state','PENDING'),('commit_id',base),('user',{'id':42,'type':'Bot'}),('user',{'id':99,'type':'User'}),('body','self-approved'),('submitted_at','2026-10-09T12:00:00Z')]:
                        before=review[field];review[field]=value
                        with self.subTest(review=field,value=value),self.assertRaisesRegex(ValueError,'operator admission'):
                            source_review.authority(7,base,head,bootstrap=True,admission=4)
                        review[field]=before
                    for field,value in [('sha',base),('type','tag')]:
                        before=tag['object'][field];tag['object'][field]=value
                        with self.subTest(tag=field),self.assertRaisesRegex(ValueError,'immutable tag'):
                            source_review.authority(7,base,head,bootstrap=True,admission=4)
                        tag['object'][field]=before
                    for field,value in [('GITHUB_REF','refs/heads/feature'),('GITHUB_REF','refs/tags/source-review-'+base)]:
                        with patch.dict(os.environ,{field:value}),self.assertRaisesRegex(ValueError,'workflow identity'):
                            source_review.authority(7,base,head,bootstrap=True,admission=4)
                    no_decision=json.loads(review['body'].split('\n',1)[1]);no_decision.pop('decision')
                    original_body=review['body']
                    review['body']=source_review.ADMISSION_PREFIX+json.dumps(no_decision,sort_keys=True,separators=(',',':'))
                    with self.assertRaisesRegex(ValueError,'operator admission'):
                        source_review.authority(7,base,head,bootstrap=True,admission=4)
                    review['body']=original_body
                    tree['tree'][-1]['sha']='0'*40
                    with self.assertRaisesRegex(ValueError,'operator admission'):
                        source_review.authority(7,base,head,bootstrap=True,admission=4)
                    tree['tree'][-1]['sha']='f'*40
                    history.clear()
                    with self.assertRaisesRegex(ValueError,'human environment'):
                        source_review.authority(7,base,head,bootstrap=True,admission=4)
                    history.append({'state':'approved','user':{'type':'User'},'environments':[{'id':13,'name':'code-review'}]})
                run.update(head_sha=base,head_branch='main')
                history[0]['environments'][0]['id']=99
                with self.assertRaisesRegex(ValueError,'human environment'):
                    source_review.authority(7,base,head)

    def test_bootstrap_cannot_authorize_itself(self):
        with patch.object(update,'pr_identity') as identity:
            for admission in (None,{'head':'b'*40,'approved':True}):
                with self.subTest(admission=admission),self.assertRaisesRegex(ValueError,'authenticated operator admission'):
                    source_review.authority(7,'a'*40,'b'*40,bootstrap=True,admission=admission)
            identity.assert_not_called()

    def test_operator_claim_rejects_changed_scope_and_incomplete_git_trees(self):
        base='a'*40;head='b'*40
        pr={'draft':True,'head':{'repo':{'full_name':'owner/repo'}}}
        old={'truncated':False,'tree':[{'path':'packages.json','mode':'100644','type':'blob','sha':'c'*40},
                                     {'path':'recipes/example','mode':'160000','type':'commit','sha':'d'*40},
                                     {'path':'.github/workflows/verification.yml','mode':'100644','type':'blob','sha':'e'*40}]}
        new=copy.deepcopy(old)
        def api(path):
            return old if '/'+base+'?' in path else new
        with patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'pr_identity',return_value=(pr,base,head)),patch.object(update,'api',side_effect=api):
            claim=source_review.bootstrap_claim(7,base,head)
            self.assertEqual(claim['workflow_blob_sha'],'e'*40)
            for entry in new['tree'][:2]:
                before=entry['sha'];entry['sha']='f'*40
                with self.subTest(path=entry['path']),self.assertRaisesRegex(ValueError,'package scope'):
                    source_review.bootstrap_claim(7,base,head)
                entry['sha']=before
            new['truncated']=True
            with self.assertRaisesRegex(ValueError,'truncated'):
                source_review.bootstrap_claim(7,base,head)
            new['truncated']=False;new['tree'][-1]['mode']='120000'
            with self.assertRaisesRegex(ValueError,'regular Git blob'):
                source_review.bootstrap_claim(7,base,head)

    def test_operator_transport_rejects_stale_pr_without_actions_token_transport(self):
        base='a'*40;head='b'*40
        pr={'state':'open','draft':True,'base':{'ref':'main','sha':base,'repo':{'full_name':'owner/repo'}},
            'head':{'sha':head,'repo':{'full_name':'owner/repo'}}}
        tree={'truncated':False,'tree':[{'path':'packages.json','mode':'100644','type':'blob','sha':'c'*40},
                                      {'path':'.github/workflows/verification.yml','mode':'100644','type':'blob','sha':'d'*40}]}
        def operator(path):
            if path.endswith('/pulls/7'):return pr
            if path.endswith('/git/ref/heads/main'):return {'object':{'sha':base}}
            if '/git/trees/' in path:return tree
            raise AssertionError(path)
        with patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=AssertionError('Actions token transport must not be used')):
            body=source_review.admission_body(7,base,head,operator)
            self.assertEqual(json.loads(body.split('\n',1)[1])['decision'],'approve-bootstrap')
            for identity in ('base','head'):
                pr[identity]['sha']='e'*40
                with self.subTest(identity=identity),self.assertRaisesRegex(ValueError,'base/head changed'):
                    source_review.admission_body(7,base,head,operator)
                pr[identity]['sha']=base if identity=='base' else head

    def test_changed_package_content_prevents_candidate_execution_and_statuses(self):
        root=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as session:
            old=Path(session)/'old';new=Path(session)/'new'
            for tree in (old,new):
                update.dump(tree/'packages.json',{'schema':1,'packages':[{'pkgbase':'example'}]})
                (tree/'recipes/example').mkdir(parents=True)
                (tree/'recipes/example/PKGBUILD').write_text('accepted')
            (new/'recipes/example/PKGBUILD').write_text('unapproved package payload')
            def checkout(sha,destination,pins):
                pins.update({'example':'1'*40})
                return old if sha=='a'*40 else new
            with patch.object(source_review,'authority',return_value={}),patch.object(update,'checkout_data',side_effect=checkout),patch.object(update,'run_candidate_tests') as tests,patch.object(update,'status') as status:
                with self.assertRaisesRegex(ValueError,'recipe content'):
                    source_review.validate(7,'a'*40,'b'*40,Path(session)/'evidence')
                tests.assert_not_called();status.assert_not_called()

    def test_accepted_controller_runs_approved_head_with_accepted_image(self):
        root=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as session:
            old=Path(session)/'old';new=Path(session)/'new'
            image='ghcr.io/archlinux/archlinux@sha256:'+'1'*64
            for tree in (old,new):
                update.dump(tree/'packages.json',{'schema':1,'packages':[{'pkgbase':'example'}]})
            (old/'build-image.txt').write_text(image)
            (new/'build-image.txt').write_text('untrusted candidate image')
            proof={'run_id':'8','run_attempt':'2','job_id':91,'base':'a'*40,'head':'b'*40}
            with patch.object(source_review,'authority',return_value=proof),patch.object(update,'checkout_data',side_effect=[old,new]),patch.object(update,'run_candidate_tests') as tests,patch.object(update,'status') as status,patch.object(update,'repository',return_value='owner/repo'):
                evidence=source_review.validate(7,'a'*40,'b'*40,Path(session)/'evidence')
                tests.assert_called_once_with(new,image)
                self.assertEqual(evidence['packages'],[])
                self.assertTrue(evidence['package_content_unchanged'])
                self.assertEqual({call.args[1] for call in status.call_args_list},{'verify','candidate-build','recipe-policy'})
                self.assertTrue(all(call.args[0]=='b'*40 for call in status.call_args_list))

    def test_missing_authority_prevents_checkout_and_tests(self):
        with patch.object(source_review,'authority',side_effect=ValueError('missing human approval')),patch.object(update,'checkout_data') as checkout,patch.object(update,'run_candidate_tests') as tests:
            with self.assertRaisesRegex(ValueError,'human approval'):
                source_review.validate(7,'a'*40,'b'*40,'unused')
            checkout.assert_not_called();tests.assert_not_called()
