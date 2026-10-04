import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import update


SRCINFO='pkgbase = example\n\tpkgver = 1.0\n\tpkgrel = 1\n\tarch = x86_64\npkgname = example\n'


class CandidateBoundaries(unittest.TestCase):
    def test_retry_reuses_only_exact_commit_content_and_history(self):
        commit = {'tree': {'sha': 'a'*40}, 'parents': [{'sha': 'b'*40}], 'message': 'update'}
        with patch.object(update, 'repository', return_value='owner/repo'), patch.object(update, 'api', return_value=commit):
            self.assertTrue(update.matches_commit('c'*40, 'a'*40, ['b'*40], 'update'))
            self.assertFalse(update.matches_commit('c'*40, 'd'*40, ['b'*40], 'update'))
            self.assertFalse(update.matches_commit('c'*40, 'a'*40, ['d'*40], 'update'))
            self.assertFalse(update.matches_commit('c'*40, 'a'*40, ['b'*40], 'manual work'))
            self.assertFalse(update.matches_commit(None, 'a'*40, ['b'*40], 'update'))

    def test_documentation_changes_need_review_without_package_builds(self):
        with tempfile.TemporaryDirectory() as session:
            old = Path(session) / 'old'
            new = Path(session) / 'new'
            old.mkdir()
            new.mkdir()
            (old / 'README.md').write_text('before')
            (new / 'README.md').write_text('after')
            self.assertEqual(update.affected_packages(old, new, {'example': {}}), ([], True))

    def setUp(self):
        root=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def archive(self, entries):
        archive=self.root/'input.tar'
        with tarfile.open(archive,'w') as handle:
            for name,kind,value in entries:
                info=tarfile.TarInfo(name);info.mode=0o644
                if kind=='file':
                    data=value.encode();info.size=len(data);handle.addfile(info,io.BytesIO(data))
                elif kind=='symlink':
                    info.type=tarfile.SYMTYPE;info.linkname=value;handle.addfile(info)
                elif kind=='hardlink':
                    info.type=tarfile.LNKTYPE;info.linkname=value;handle.addfile(info)
                else:
                    info.mode=0o4755;data=value.encode();info.size=len(data);handle.addfile(info,io.BytesIO(data))
        return archive

    def test_extraction_rejects_traversal_links_and_modes(self):
        for index,entries in enumerate([
            [('wrapper/../escape','file','bad')],
            [('wrapper/link','symlink','../../escape')],
            [('wrapper/link','symlink','/etc/passwd')],
            [('wrapper/link','hardlink','wrapper/data')],
            [('wrapper/file','setuid','data')],
            [('wrapper/link','symlink','data'),('wrapper/link/child','file','data')],
            [('wrapper/data','file','a'),('wrapper/data','file','b')],
        ]):
            with self.subTest(entries=entries),self.assertRaises(ValueError):
                update.extract_tree(self.archive(entries),self.root/f'out-{index}')
        self.assertFalse((self.root/'escape').exists())

    def test_internal_symlinks_are_preserved(self):
        archive=self.archive([('wrapper/recipes/example/file','file','payload'),('wrapper/recipes/example/alias','symlink','file')])
        output=update.extract_tree(archive,self.root/'out',{'recipes'})
        self.assertTrue((output/'recipes/example/alias').is_symlink())
        self.assertEqual((output/'recipes/example/alias').read_text(),'payload')

    def test_typed_rendering_is_idempotent_and_preserves_code(self):
        recipe=self.root/'recipe';recipe.mkdir()
        old='a'*64;new='b'*64
        text="pkgver=1.0\npkgrel=1\nsha256sums=('"+old+"')\npackage() { echo preserved; }\n"
        (recipe/'PKGBUILD').write_text(text)
        (recipe/'.SRCINFO').write_text(SRCINFO.replace('pkgname = example','\tsha256sums = '+old+'\npkgname = example'))
        policy={'automatic':{'version':{'assignment':'pkgver','literal_assignment':'pkgver=1.0'},'pkgrel':{'assignment':'pkgrel','literal_assignment':'pkgrel=1'},'checksum_array':{'assignment':'sha256sums','literal':"sha256sums=('"+old+"')",'values':[old]},'checksums':[{'source_id':'code','algorithm':'sha256','index':0}]}}
        update.render_recipe(recipe,policy,'1.1',{'code':{'sha256':new}})
        first=(recipe/'PKGBUILD').read_text()
        update.render_recipe(recipe,policy,'1.1',{'code':{'sha256':new}})
        self.assertEqual((recipe/'PKGBUILD').read_text(),first)
        self.assertEqual(first,text.replace('pkgver=1.0','pkgver=1.1').replace(old,new))

    def test_aur_three_way_preserves_local_correction_and_payload(self):
        recipe=self.root/'maintained';recipe.mkdir()
        base='pkgver=1.0\n'+''.join('unchanged_'+str(i)+'=value\n' for i in range(8))+'checkdepends=(python-sentencepiece pytest)\n'+''.join('stable_'+str(i)+'=value\n' for i in range(8))+'build() { echo upstream; }\n'
        local=base.replace('python-sentencepiece ','').replace('echo upstream','echo local-native')
        theirs=base.replace('pkgver=1.0','pkgver=1.1')
        (recipe/'PKGBUILD').write_text(local);(recipe/'local.patch').write_text('retained patch')
        (recipe/'local.patch').chmod(0o755)
        transition={'previous_files':[{'path':'PKGBUILD','mode':'100644','content':base}],'files':[{'path':'PKGBUILD','mode':'100644','content':theirs}]}
        result=update.apply_aur_transition(recipe,transition,{'authority':'modified-aur'},self.root/'merge')
        self.assertEqual(result['conflicts'],[])
        self.assertEqual((recipe/'PKGBUILD').read_text(),local.replace('pkgver=1.0','pkgver=1.1'))
        self.assertEqual((recipe/'local.patch').read_text(),'retained patch')
        self.assertEqual((recipe/'local.patch').stat().st_mode&0o777,0o755)

    def test_aur_conflict_retains_entire_accepted_tree_without_markers(self):
        recipe=self.root/'maintained';recipe.mkdir()
        (recipe/'PKGBUILD').write_text('value=local\n');(recipe/'payload').write_text('local payload')
        transition={'previous_files':[{'path':'PKGBUILD','mode':'100644','content':'value=base\n'}],'files':[{'path':'PKGBUILD','mode':'100644','content':'value=upstream\n'},{'path':'new','mode':'100644','content':'new upstream payload'}]}
        before=update.tree_manifest(recipe)
        result=update.apply_aur_transition(recipe,transition,{'authority':'maintained'},self.root/'merge')
        self.assertEqual(result['conflicts'],['PKGBUILD'])
        self.assertIsNone(result['files'])
        self.assertEqual(update.tree_manifest(recipe),before)
        self.assertEqual((recipe/'PKGBUILD').read_text(),'value=local\n')
        self.assertFalse((recipe/'new').exists())

    def test_aur_three_way_detects_delete_modify_conflict(self):
        recipe=self.root/'maintained';recipe.mkdir()
        (recipe/'payload').write_text('locally modified')
        transition={'previous_files':[{'path':'payload','mode':'100644','content':'base'}],'files':[]}
        result=update.integrate_aur(recipe,transition,self.root/'merge')
        self.assertEqual(result['conflicts'],['payload'])
        self.assertEqual((recipe/'payload').read_text(),'locally modified')

    def test_stale_base_or_head_never_uses_approval(self):
        pr={'state':'open','base':{'ref':'main','sha':'a'*40,'repo':{'full_name':'owner/repo'}},'head':{'sha':'b'*40}}
        with patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',return_value=pr),patch.object(update,'main_sha',return_value='c'*40),self.assertRaisesRegex(ValueError,'changed'):
            update.pr_identity(1,'a'*40,'b'*40)

    def record(self):
        package={'pkgbase':'example','recipe_commit':'1'*40,'input_digest':'d'*64,'lock':{'schema':1,'version':'1.0-1','sources':[]},'expected_srcinfo':SRCINFO,'policy':{'outputs':[{'name':'example','arch':'x86_64'}]}}
        record={'schema':1,'repository':'owner/repo','base':'a'*40,'head':'b'*40,'recipe_pins':{'example':'1'*40},'previous_recipe_pins':{'example':'2'*40},'run_id':'1','run_attempt':'1','image':'arch@sha256:'+'c'*64,'harness_sha':'e'*64,'pr_number':1,'mechanical':True,'packages':[package]}
        path=self.root/'example.pkg.tar.zst';path.write_bytes(b'package bytes')
        evidence={k:record[k] for k in ('schema','repository','base','head','recipe_pins','previous_recipe_pins','run_id','run_attempt','image','harness_sha')}
        evidence['packages']=[{'pkgbase':'example','recipe_commit':package['recipe_commit'],'input_digest':package['input_digest'],'source_lock':package['lock'],'metadata':{'srcinfo':SRCINFO},'files':[{'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'name':'example','version':'1.0-1','arch':'x86_64'}]}]
        update.dump(self.root/'native-evidence.json',evidence)
        return record,evidence,path

    def test_native_receipt_rejects_changed_recipe_pin_before_status(self):
        record,evidence,path=self.record()
        evidence['packages'][0]['recipe_commit']='9'*40
        update.dump(self.root/'native-evidence.json',evidence)
        with patch.object(update,'pr_identity'),patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'frozen input'):
            update.validate_build(record,self.root)
        status.assert_not_called()
        evidence['packages'][0]['recipe_commit']=record['packages'][0]['recipe_commit']
        evidence['recipe_pins']={'example':'9'*40}
        update.dump(self.root/'native-evidence.json',evidence)
        with patch.object(update,'pr_identity'),patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'recipe_pins'):
            update.validate_build(record,self.root)
        status.assert_not_called()

    def test_affected_manifest_includes_hidden_content_modes_and_symlink_targets(self):
        old=self.root/'old';new=self.root/'new'
        for root in (old,new):
            recipe=root/'recipes/example';recipe.mkdir(parents=True)
            (recipe/'PKGBUILD').write_text('accepted code')
            (recipe/'.hidden').write_text('retained')
            (recipe/'other').write_text('retained')
            (recipe/'link').symlink_to('.hidden')
        for mutation in ('hidden','mode','link','code'):
            with self.subTest(mutation=mutation):
                recipe=new/'recipes/example'
                (recipe/'.hidden').write_text('changed' if mutation=='hidden' else 'retained')
                (recipe/'PKGBUILD').write_text('new code' if mutation=='code' else 'accepted code')
                (recipe/'PKGBUILD').chmod(0o755 if mutation=='mode' else 0o644)
                (recipe/'link').unlink();(recipe/'link').symlink_to('other' if mutation=='link' else '.hidden')
                self.assertEqual(update.affected_packages(old,new,{'example':{}}),(['example'],False))
        (new/'.gitmodules').write_text('untrusted URL')
        self.assertEqual(update.affected_packages(old,new,{'example':{},'second':{}}),(['example','second'],True))

    def test_prepare_classifies_full_pinned_recipe_and_history_not_opaque_sha(self):
        policy={'pkgbase':'example','sources':[],'automatic':{'version':{'assignment':'pkgver'},'pkgrel':{'assignment':'pkgrel'},'checksums':[]}}
        for mutation in ('version','code','hidden','mode','rewrite','modules'):
            with self.subTest(mutation=mutation):
                fixture=self.root/mutation
                for label,version in [('old','1.0'),('new','1.1')]:
                    root=fixture/label;recipe=root/'recipes/example';recipe.mkdir(parents=True)
                    (root/'packages.json').write_text(json.dumps({'schema':1,'packages':[policy]}))
                    (root/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'a'*64)
                    (root/'.gitmodules').write_text('enrolled modules')
                    (root/'inputs').mkdir()
                    (root/'inputs/example.json').write_text(json.dumps({'schema':1,'version':version+'-1','sources':[]}))
                    (recipe/'PKGBUILD').write_text(f'pkgver={version}\npkgrel=1\npackage() {{ echo accepted; }}\n')
                    (recipe/'.SRCINFO').write_text(SRCINFO.replace('1.0',version))
                    (recipe/'.payload').write_text('retained patch')
                recipe=fixture/'new/recipes/example'
                if mutation=='code':
                    (recipe/'PKGBUILD').write_text((recipe/'PKGBUILD').read_text().replace('echo accepted','echo malicious'))
                elif mutation=='hidden':
                    (recipe/'.payload').write_text('changed patch')
                elif mutation=='mode':
                    (recipe/'PKGBUILD').chmod(0o755)
                elif mutation=='modules':
                    (fixture/'new/.gitmodules').write_text('changed candidate URL')
                def checkout(sha,destination,pins):
                    pins.update(example=('1' if sha=='a'*40 else '2')*40)
                    return Path(shutil.copytree(fixture/('old' if sha=='a'*40 else 'new'),destination))
                evidence={'srcinfo':(recipe/'.SRCINFO').read_text(),'checksums':{},'source_templates_verified':True,'lock_verified':True,'auxiliary_inputs_verified':True,'authentic':True,'fast_forward':True}
                with patch.dict(os.environ,{'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'},clear=True),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'pr_identity',return_value=({},'a'*40,'b'*40)),patch.object(update,'checkout_data',side_effect=checkout),patch.object(update,'harness_digest',return_value='e'*64),patch.object(update,'independent_transition',return_value=evidence),patch.object(update.recipes,'is_ancestor',return_value=mutation!='rewrite'),patch('tools.recipe_gate._compare',return_value=1),patch.object(update,'status'):
                    record=update.prepare(1,fixture/'prepared')
                self.assertEqual(record['mechanical'],mutation=='version')
                self.assertEqual(record['packages'][0]['recipe_commit'],'2'*40)
                self.assertEqual(record['packages'][0]['previous_recipe_commit'],'1'*40)
                if mutation not in {'version','modules'}:
                    self.assertNotEqual(record['decisions'][0]['decision'],'mechanical')

    def test_pin_lookup_rejects_flattened_or_wrong_gitlink(self):
        for mode,kind,sha in [('100644','blob','1'*40),('040000','tree','1'*40),('160000','commit','not-a-sha')]:
            with self.subTest(mode=mode,sha=sha),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=[{'tree':{'sha':'a'*40}},{'tree':[{'path':'recipes/example','mode':mode,'type':kind,'sha':sha}]}]),self.assertRaisesRegex(ValueError,'gitlink'):
                update.pin_at('b'*40,'example')

    def test_full_outputs_required_before_any_success_status(self):
        record,evidence,path=self.record()
        evidence['packages'][0]['files']=[];update.dump(self.root/'native-evidence.json',evidence)
        with patch.object(update,'pr_identity'),patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'missing/extra'):
            update.validate_build(record,self.root)
        status.assert_not_called()

    def test_native_metadata_and_bytes_are_bound(self):
        record,evidence,path=self.record()
        path.write_bytes(b'altered')
        with patch.object(update,'pr_identity'),patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'bytes/version'):
            update.validate_build(record,self.root)
        status.assert_not_called()
        path.write_bytes(b'package bytes');evidence['packages'][0]['metadata']['srcinfo']=SRCINFO.replace('1.0','2.0');update.dump(self.root/'native-evidence.json',evidence)
        with patch.object(update,'pr_identity'),patch.object(update,'status'),self.assertRaisesRegex(ValueError,'metadata'):
            update.validate_build(record,self.root)

    def test_stale_head_after_human_approval_cannot_finalize(self):
        record,evidence,path=self.record();record['mechanical']=False
        update.dump(self.root/'candidate.json',record)
        pr={'state':'open','base':{'ref':'main','sha':record['base'],'repo':{'full_name':'owner/repo'}},'head':{'sha':'c'*40}}
        with patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'main_sha',return_value=record['base']),patch.object(update,'api',return_value=pr) as api,patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'changed'):
            update.finalize(1,record['base'],record['head'],self.root/'candidate.json',self.root)
        status.assert_not_called()
        self.assertTrue(all(call.args[1:] == () for call in api.call_args_list))

    def test_human_candidate_never_auto_merges(self):
        record,evidence,path=self.record();record['mechanical']=False
        update.dump(self.root/'candidate.json',record)
        states=[{'context':'candidate-build','state':'success'},{'context':'recipe-policy','state':'success'}]
        with patch.object(update,'pr_identity'),patch.object(update,'validate_build'),patch.object(update,'api',return_value=states) as api,patch.object(update,'repository',return_value='owner/repo'):
            result=update.finalize(1,record['base'],record['head'],self.root/'candidate.json',self.root)
        self.assertFalse(result['merged'])
        self.assertTrue(all(call.args[0].endswith('/statuses') for call in api.call_args_list))

    def test_mechanical_merge_has_expected_head_and_explicit_dispatch(self):
        record,evidence,path=self.record();update.dump(self.root/'candidate.json',record)
        responses=[[{'context':'candidate-build','state':'success'},{'context':'recipe-policy','state':'success'}],{'merged':True,'sha':'f'*40},None]
        with patch.object(update,'pr_identity'),patch.object(update,'validate_build'),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=responses) as api:
            update.finalize(1,record['base'],record['head'],self.root/'candidate.json',self.root)
        self.assertEqual(api.call_args_list[1].args[2],{'sha':record['head'],'merge_method':'merge'})
        self.assertEqual(api.call_args_list[2].args[2]['inputs']['accepted_sha'],'f'*40)


if __name__=='__main__':unittest.main()
