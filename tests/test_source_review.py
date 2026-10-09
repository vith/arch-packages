import copy
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

    def test_authority_binds_actual_workflow_attempt_and_direct_commit_tag(self):
        base='a'*40;head='b'*40
        pr={'draft':False,'head':{'repo':{'full_name':'owner/repo'}}}
        run={'id':8,'head_sha':base,'head_branch':'main','event':'workflow_dispatch',
             'path':'.github/workflows/verification.yml','run_attempt':2,
             'display_title':f'Source review PR 7 head {head} base {base}'}
        tree={'truncated':False,'tree':[{'path':'packages.json','mode':'100644','type':'blob','sha':'d'*40},
                                      {'path':'recipes/example','mode':'160000','type':'commit','sha':'e'*40},
                                      {'path':'.github/workflows/verification.yml','mode':'100644','type':'blob','sha':'f'*40}]}
        tag={'ref':'refs/tags/source-review-'+head,'object':{'type':'commit','sha':head}}
        def api(path):
            if path.endswith('/attempts/2'):return run
            if '/git/trees/' in path:return tree
            if '/git/ref/tags/' in path:return tag
            raise AssertionError(path)
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'8','GITHUB_RUN_ATTEMPT':'2','GITHUB_SHA':base,'GITHUB_REF':'refs/heads/main'}),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'pr_identity',return_value=(pr,base,head)),patch.object(update,'api',side_effect=api):
            self.assertEqual(source_review.authority(7,base,head)['head'],head)
            for field,value in [('id',9),('head_sha',head),('head_branch','feature'),('run_attempt',1),('path','.github/workflows/candidate.yml'),('event','push'),('display_title','another PR')]:
                before=run[field];run[field]=value
                with self.subTest(field=field),self.assertRaisesRegex(ValueError,'workflow identity'):
                    source_review.authority(7,base,head)
                run[field]=before
            for field,value in [('GITHUB_SHA',head),('GITHUB_REF','refs/heads/feature')]:
                with self.subTest(environment=field),patch.dict(os.environ,{field:value}),self.assertRaisesRegex(ValueError,'workflow identity'):
                    source_review.authority(7,base,head)
            pr['head']['repo']['full_name']='fork/repo'
            with self.assertRaisesRegex(ValueError,'workflow identity'):
                source_review.authority(7,base,head)
            pr['head']['repo']['full_name']='owner/repo'
            tree['truncated']=True
            with patch.object(update,'checkout_data') as checkout,patch.object(update,'run_candidate_tests') as tests,patch.object(update,'status') as status:
                with self.assertRaisesRegex(ValueError,'truncated'):
                    source_review.validate(7,base,head,'unused')
                checkout.assert_not_called();tests.assert_not_called();status.assert_not_called()
            tree['truncated']=False
            run.update(head_sha=head,head_branch='source-review-'+head)
            with patch.dict(os.environ,{'GITHUB_SHA':head,'GITHUB_REF':'refs/tags/source-review-'+head}):
                self.assertEqual(source_review.authority(7,base,head,bootstrap=True)['head'],head)
                for field,value in [('sha',base),('type','tag')]:
                    before=tag['object'][field];tag['object'][field]=value
                    with self.subTest(tag=field),self.assertRaisesRegex(ValueError,'immutable tag'):
                        source_review.authority(7,base,head,bootstrap=True)
                    tag['object'][field]=before

    def test_git_scope_rejects_package_changes_and_incomplete_trees(self):
        base='a'*40;head='b'*40
        old={'truncated':False,'tree':[{'path':'packages.json','mode':'100644','type':'blob','sha':'c'*40},
                                     {'path':'recipes/example','mode':'160000','type':'commit','sha':'d'*40},
                                     {'path':'.github/workflows/verification.yml','mode':'100644','type':'blob','sha':'e'*40}]}
        new=copy.deepcopy(old)
        def api(path):
            return old if '/'+base+'?' in path else new
        with patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=api):
            source_review.unchanged_git_scope(base,head)
            for entry in new['tree'][:2]:
                before=entry['sha'];entry['sha']='f'*40
                with self.subTest(path=entry['path']),self.assertRaisesRegex(ValueError,'package scope'):
                    source_review.unchanged_git_scope(base,head)
                entry['sha']=before
            new['truncated']=True
            with self.assertRaisesRegex(ValueError,'truncated'):
                source_review.unchanged_git_scope(base,head)
            new['truncated']=False;new['tree'][-1]['mode']='120000'
            with self.assertRaisesRegex(ValueError,'regular Git blob'):
                source_review.unchanged_git_scope(base,head)

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

    def test_accepted_controller_runs_exact_head_with_accepted_image(self):
        root=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as session:
            old=Path(session)/'old';new=Path(session)/'new'
            image='ghcr.io/archlinux/archlinux@sha256:'+'1'*64
            for tree in (old,new):
                update.dump(tree/'packages.json',{'schema':1,'packages':[{'pkgbase':'example'}]})
            (old/'build-image.txt').write_text(image)
            (new/'build-image.txt').write_text('untrusted candidate image')
            proof={'run_id':'8','run_attempt':'2','base':'a'*40,'head':'b'*40}
            with patch.object(source_review,'authority',return_value=proof),patch.object(update,'checkout_data',side_effect=[old,new]),patch.object(update,'run_candidate_tests') as tests,patch.object(update,'status') as status,patch.object(update,'repository',return_value='owner/repo'):
                evidence=source_review.validate(7,'a'*40,'b'*40,Path(session)/'evidence')
                tests.assert_called_once_with(new,image)
                self.assertEqual(evidence['packages'],[])
                self.assertTrue(evidence['package_content_unchanged'])
                self.assertEqual({call.args[1] for call in status.call_args_list},{'verify','candidate-build','recipe-policy'})
                self.assertTrue(all(call.args[0]=='b'*40 for call in status.call_args_list))

    def test_stale_identity_prevents_checkout_tests_and_statuses(self):
        with patch.object(update,'pr_identity',side_effect=ValueError('candidate base/head changed')),patch.object(update,'checkout_data') as checkout,patch.object(update,'run_candidate_tests') as tests,patch.object(update,'status') as status:
            with self.assertRaisesRegex(ValueError,'base/head changed'):
                source_review.validate(7,'a'*40,'b'*40,'unused')
            checkout.assert_not_called();tests.assert_not_called();status.assert_not_called()

    def test_identity_drift_after_tests_prevents_success_statuses(self):
        root=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as session:
            old=Path(session)/'old';new=Path(session)/'new'
            for tree in (old,new):
                update.dump(tree/'packages.json',{'schema':1,'packages':[{'pkgbase':'example'}]})
            (old/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'1'*64)
            with patch.object(source_review,'authority',side_effect=[{}, {}, ValueError('candidate base/head changed')]),patch.object(update,'checkout_data',side_effect=[old,new]),patch.object(update,'run_candidate_tests') as tests,patch.object(update,'status') as status:
                with self.assertRaisesRegex(ValueError,'base/head changed'):
                    source_review.validate(7,'a'*40,'b'*40,Path(session)/'evidence')
                tests.assert_called_once()
                status.assert_not_called()

    def test_merge_requires_successful_actual_validation_and_latest_exact_head_statuses(self):
        base='a'*40;head='b'*40
        proof={'run_id':'8','run_attempt':'2','base':base,'head':head}
        jobs={'total_count':1,'jobs':[{'name':f'Validate source PR 7 head {head} base {base}','conclusion':'success'}]}
        statuses=[{'context':context,'state':'success'} for context in ('verify','candidate-build','recipe-policy')]
        merges=[];dispatches=[]
        def api(path,method='GET',data=None):
            if '/jobs?' in path:return jobs
            if path.endswith('/statuses'):return statuses
            if path.endswith('/merge'):
                merges.append(data)
                return {'merged':True,'sha':'c'*40}
            if path.endswith('/publish.yml/dispatches'):
                dispatches.append(data)
                return None
            raise AssertionError(path)
        with patch.object(source_review,'authority',return_value=proof),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=api):
            jobs['jobs'][0]['conclusion']='failure'
            with self.assertRaisesRegex(ValueError,'validation job'):
                source_review.finalize(7,base,head)
            self.assertEqual(merges,[]);self.assertEqual(dispatches,[])
            jobs['jobs'][0]['conclusion']='success'
            for context in ('verify','candidate-build','recipe-policy'):
                statuses.insert(0,{'context':context,'state':'pending'})
                with self.subTest(context=context),self.assertRaisesRegex(ValueError,'statuses missing'):
                    source_review.finalize(7,base,head)
                statuses.pop(0)
            self.assertEqual(merges,[]);self.assertEqual(dispatches,[])
            source_review.finalize(7,base,head)
            self.assertEqual(merges,[{'sha':head,'merge_method':'merge'}])
            self.assertEqual(dispatches,[{'ref':'main','inputs':{'accepted_sha':'c'*40}}])

    def test_tuple_drift_at_merge_prevents_merge_and_publication(self):
        jobs={'jobs':[{'name':f'Validate source PR 7 head {"b"*40} base {"a"*40}','conclusion':'success'}]}
        statuses=[{'context':context,'state':'success'} for context in ('verify','candidate-build','recipe-policy')]
        def api(path):
            if '/jobs?' in path:return jobs
            if path.endswith('/statuses'):return statuses
            raise AssertionError('merge/publication must not run: '+path)
        with patch.object(source_review,'authority',side_effect=[{'run_id':'8','run_attempt':'2'},ValueError('candidate base/head changed')]),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=api):
            with self.assertRaisesRegex(ValueError,'base/head changed'):
                source_review.finalize(7,'a'*40,'b'*40)
