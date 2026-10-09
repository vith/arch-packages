import copy
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

from tools import recipe_state, update


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
        for target in ('tools.recipe_state.save','tools.recipe_state.attest'):
            mocked=patch(target)
            mocked.start();self.addCleanup(mocked.stop)

    def test_frozen_transition_survives_branch_advance_but_rejects_frozen_tamper(self):
        git=update.sources.git
        remote=self.root/'source';git('init',remote)
        git('config','user.email','fixture@example.invalid',cwd=remote)
        git('config','user.name','Fixture',cwd=remote)
        (remote/'payload').write_text('first');git('add','payload',cwd=remote);git('commit','-m','first',cwd=remote)
        git('branch','-M','integration',cwd=remote);git('tag','v1.0.0',cwd=remote)
        template=r'^([0-9]+(?:\.[0-9]+)+)\.fork\.r([0-9]+)\.g([0-9a-f]{12})$'
        native='git+'+str(remote)+'#branch=integration'
        policy={'sources':[{'id':'code','kind':'git','mutable':True,'source_template':native},
                           {'id':'patch','kind':'local','mutable':False,'source_template':'local.patch'}],
                'automatic':{'version':{'derivation':'frozen-authentic-tag-ancestry','template':template}}}
        locks=[]
        with patch.object(update.sources,'public_url',lambda value:value):
            for index,label in enumerate(('old','new')):
                if index:
                    (remote/'payload').write_text('second');git('add','payload',cwd=remote);git('commit','-m','second',cwd=remote)
                head=git('rev-parse','HEAD',cwd=remote)
                source=update.sources.freeze_source({'id':'code','kind':'git','source':native,'url':str(remote),'ref':'refs/heads/integration','commit':head,'checksums':{'sha256':'SKIP'},'work_dir':str(self.root/('freeze-'+label))})
                version='1.0.0.fork.r'+str(index)+'.g'+head[:12]
                tree=self.root/label
                recipe=tree/'recipes/example';recipe.mkdir(parents=True)
                (recipe/'PKGBUILD').write_text('pkgver='+version+'\n')
                (recipe/'.SRCINFO').write_text(SRCINFO.replace('1.0',version))
                (recipe/'local.patch').write_text('accepted patch')
                auxiliary=update.sources.freeze_source({'id':'patch','kind':'local','source':'local.patch','checksums':{'sha256':hashlib.sha256(b'accepted patch').hexdigest()}})
                lock={'schema':1,'version':version+'-1','sources':[source,auxiliary]};locks.append(lock)
                update.dump(tree/'inputs/example.json',lock)
                update.dump(tree/'upstream/example.json',{'aur':None,'watchers':[]})
            (remote/'payload').write_text('third');git('add','payload',cwd=remote);git('commit','-m','third',cwd=remote)
            sources=update.sources
            with patch.object(sources,'discover_git',side_effect=AssertionError('no latest discovery')):
                evidence=update.frozen_transition(self.root/'old',self.root/'new','example',policy,self.root/'historical')
            self.assertTrue(evidence['fast_forward'])
            self.assertTrue(evidence['authentic'])
            with self.assertRaises(ValueError):
                update.independent_transition(self.root/'old',self.root/'new','example',policy,self.root/'live')
            accepted_lock=copy.deepcopy(locks[1])
            lock_path=self.root/'new/inputs/example.json'
            local_path=self.root/'old/recipes/example/local.patch'
            accepted_local=local_path.read_bytes()
            for mutation in ('count','tag','source','local'):
                with self.subTest(mutation=mutation):
                    proposed=copy.deepcopy(accepted_lock)
                    if mutation=='count':
                        proposed['version']=proposed['version'].replace('.r1.','.r9.')
                    elif mutation=='tag':
                        proposed['sources'][0]['git_context']['version_tag']['commit']='f'*40
                    elif mutation=='source':
                        proposed['sources'][0]['commit']=locks[0]['sources'][0]['commit']
                    else:
                        local_path.write_text('altered patch')
                    update.dump(lock_path,proposed)
                    try:
                        with self.assertRaises(ValueError):
                            update.frozen_transition(self.root/'old',self.root/'new','example',policy,self.root/('bad-'+mutation))
                    finally:
                        update.dump(lock_path,accepted_lock)
                        local_path.write_bytes(accepted_local)
            restored=update.frozen_transition(self.root/'old',self.root/'new','example',policy,self.root/'restored')
            self.assertEqual(restored,evidence)


    def test_branch_count_metadata_and_transition_reject_frozen_version_tamper(self):
        git=update.sources.git
        remote=self.root/'count-source';git('init',remote)
        git('config','user.email','fixture@example.invalid',cwd=remote)
        git('config','user.name','Fixture',cwd=remote)
        git('symbolic-ref','HEAD','refs/heads/integration',cwd=remote)
        native='git+'+str(remote)+'#branch=integration'
        rules={'derivation':'frozen-git-revision-count','template':r'^r[0-9]+\.[0-9a-f]{7,40}$'}
        policy={'sources':[{'id':'code','kind':'git','mutable':True,'source_template':native,'url_template':str(remote),'checksum_algorithm':'sha256','checksum_index':0}],
                'automatic':{'version':{**rules,'assignment':'pkgver','literal_assignment':'pkgver=r1.abcdef0'},
                             'pkgrel':{'value':'1','assignment':'pkgrel','literal_assignment':'pkgrel=1'},'checksums':[]}}
        locks=[]
        with patch.object(update.sources,'public_url',lambda value:value):
            for count,label in enumerate(('old','new'),1):
                (remote/'payload').write_text(label)
                git('add','payload',cwd=remote);git('commit','-m',label,cwd=remote)
                commit=git('rev-parse','HEAD',cwd=remote)
                work=self.root/('count-'+label)
                source=update.sources.freeze_source({'id':'code','kind':'git','source':native,'url':str(remote),'ref':'refs/heads/integration','commit':commit,'checksums':{'sha256':'SKIP'},'work_dir':str(work/'code')})
                self.assertIsNone(source['git_context']['version_tag'])
                version='r'+str(count)+'.'+git('rev-parse','--short=7',commit,cwd=remote)
                recipe=self.root/label/'recipes/example';recipe.mkdir(parents=True)
                (recipe/'PKGBUILD').write_text('pkgver='+version+'\npkgrel=1\n')
                (recipe/'.SRCINFO').write_text(SRCINFO.replace('1.0',version).replace('pkgname = example','\tsource = '+native+'\n\tsha256sums = SKIP\npkgname = example'))
                lock={'schema':1,'version':version+'-1','sources':[source]};locks.append(lock)
                update.dump(self.root/label/'inputs/example.json',lock)
                update.dump(self.root/label/'upstream/example.json',{'aur':None,'watchers':[]})
                def offline_git(*args,**kwargs):
                    if args[0] in {'fetch','ls-remote','clone'}:
                        raise AssertionError('frozen metadata reached remote')
                    return git(*args,**kwargs)
                with patch.object(update.sources,'git',offline_git):
                    result=update.frozen_metadata(recipe,lock,policy,work)
                    self.assertEqual(result['version'],version+'-1')
                    wrong=copy.deepcopy(lock);wrong['version']='r99.'+commit[:7]+'-1'
                    with self.assertRaisesRegex(ValueError,'version mismatch'):
                        update.frozen_metadata(recipe,wrong,policy,work,preserve_pkgrel=True)
            live=update.independent_transition(self.root/'old',self.root/'new','example',policy,self.root/'count-live')
            self.assertTrue(live['fast_forward'])
            self.assertTrue(live['authentic'])
            (remote/'payload').write_text('later')
            git('add','payload',cwd=remote);git('commit','-m','later',cwd=remote)
            with patch.object(update.sources,'discover_git',side_effect=AssertionError('no latest discovery')):
                historical=update.frozen_transition(self.root/'old',self.root/'new','example',policy,self.root/'count-historical')
            self.assertEqual(historical,live)
            lock_path=self.root/'new/inputs/example.json'
            watcher={'id':'branch','kind':'git','url':str(remote),'ref':'refs/heads/integration','source_id':'code'}
            update.dump(self.root/'new/upstream/example.json',{'aur':None,'watchers':[watcher]})
            with patch.object(update,'ROOT',self.root/'new'),patch.object(update,'control_checkout',return_value=('a'*40,{'example':'b'*40})),patch.object(update,'policy_at',return_value={'example':policy}):
                update.discover(self.root/'count-discovery')
            receipt=update.load(self.root/'count-discovery/receipts.json')['receipts'][0]
            third=git('rev-parse','HEAD',cwd=remote)
            expected='r3.'+git('rev-parse','--short=7',third,cwd=remote)
            self.assertEqual(receipt['metadata']['pkgver'],expected)
            self.assertEqual(receipt['lock']['sources'][0]['commit'],third)
            self.assertEqual(receipt['lock']['version'],expected+'-1')
            self.assertEqual(receipt['provenance']['watchers'],[watcher])
            update.dump(self.root/'new/upstream/example.json',{'aur':None,'watchers':[]})
            for mutation in ('count','commit','same-source'):
                with self.subTest(mutation=mutation):
                    wrong=copy.deepcopy(locks[1])
                    if mutation=='count':
                        wrong['version']=wrong['version'].replace('r2.','r9.')
                    elif mutation=='commit':
                        wrong['sources'][0]['commit']=locks[0]['sources'][0]['commit']
                    else:
                        wrong['sources']=copy.deepcopy(locks[0]['sources'])
                    update.dump(lock_path,wrong)
                    with self.assertRaises(ValueError):
                        update.frozen_transition(self.root/'old',self.root/'new','example',policy,self.root/('count-bad-'+mutation))
            update.dump(lock_path,locks[1])


    def test_prefixed_tag_provenance_requires_prefixed_source_and_immutable_prefix(self):
        git=update.sources.git
        remote=self.root/'prefixed-tags';git('init',remote)
        git('config','user.email','fixture@example.invalid',cwd=remote)
        git('config','user.name','Fixture',cwd=remote)
        (remote/'payload').write_text('release')
        git('add','payload',cwd=remote);git('commit','-m','release',cwd=remote)
        commit=git('rev-parse','HEAD',cwd=remote)
        for tag in ('B5.0.1','B7'):
            git('tag',tag,cwd=remote)
        previous={'id':'release','kind':'tag','url':str(remote),'source_id':'code',
                  'tag_pattern':r'^B([0-9]+(?:\.[0-9]+)*)$','version_prefix':'B',
                  'accepted_tag':'B5.0.1','accepted_tag_object':commit,'accepted_peeled_commit':commit}
        current={**previous,'accepted_tag':'B7'}
        old={'aur':None,'watchers':[previous]};new={'aur':None,'watchers':[current]}
        source={'id':'code','kind':'archive','source':'looking-glass-B7.tar.gz'}
        lock={'sources':[source]}
        policy={'sources':[{'id':'code','source_template':'looking-glass-{version}.tar.gz'}]}
        self.assertEqual(update.verify_provenance(old,new,lock,policy),['release'])
        numeric=copy.deepcopy(lock);numeric['sources'][0]['source']='looking-glass-7.tar.gz'
        with self.assertRaisesRegex(ValueError,'watcher tag disagree'):
            update.verify_provenance(old,new,numeric,policy)
        changed=copy.deepcopy(new);changed['watchers'][0]['version_prefix']='C'
        with self.assertRaisesRegex(ValueError,'watcher policy changed'):
            update.verify_provenance(old,changed,lock,policy)

    def test_projected_aur_artifact_url_preserves_native_version_and_rejects_lock_tamper(self):
        artifact=self.root/'upstream/0.1.302-1/virtio-win.iso'
        artifact.parent.mkdir(parents=True);artifact.write_bytes(b'authenticated ISO fixture')
        digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
        url_template=(self.root/'upstream').as_uri()+'/{artifact_version}/virtio-win.iso'
        source_template='virtio-win-{version}.iso::'+url_template
        enrollment={'id':'iso','kind':'archive','source_template':source_template,'url_template':url_template,
                    'checksum_algorithm':'sha256','checksum_index':0,
                    'version_projection':{'pattern':r'^(?P<release>[0-9]+\.[0-9]+\.[0-9]+)\.(?P<build>[0-9]+)$','template':'{release}-{build}'}}
        policy={'sources':[enrollment]}
        native='virtio-win-0.1.302.1.iso::'+artifact.as_uri()
        recipe=self.root/'projected-recipe';recipe.mkdir()
        (recipe/'PKGBUILD').write_text('pkgver=0.1.302.1\npkgrel=1\n')
        claims='pkgbase = example\n\tpkgver = 0.1.302.1\n\tpkgrel = 1\n\tepoch = 2\n\tarch = x86_64\n\tsource = '+native+'\n\tsha256sums = '+digest+'\npkgname = example\n'
        (recipe/'.SRCINFO').write_text(claims)
        source={key:None for key in update.sources.FIELDS}
        source.update(id='iso',kind='archive',source='old.iso',url='https://example.invalid/old.iso',checksums={'sha256':'a'*64})
        lock={'schema':1,'version':'2:0.1.301.1-1','sources':[source]}
        with patch.object(update.sources,'public_url',lambda value:value):
            aligned=update.align_aur_lock(recipe,lock,policy,self.root/'projected-work')
            lock['version']=aligned['version']
            update.validate_source_policy(lock,policy)
            metadata=update.frozen_metadata(recipe,lock,policy,self.root/'projected-frozen',preserve_pkgrel=True)
            self.assertEqual(metadata['version'],'2:0.1.302.1-1')
            self.assertEqual(metadata['srcinfo'],claims)
            self.assertEqual(source['url'],artifact.as_uri())
            self.assertEqual(source['source'],native)
            for mutation in ('alias','url','version'):
                with self.subTest(mutation=mutation):
                    wrong=copy.deepcopy(lock)
                    if mutation=='alias':
                        wrong['sources'][0]['source']=native.replace('virtio-win-0.1.302.1.iso::','virtio-win-0.1.302-1.iso::')
                    elif mutation=='url':
                        wrong['sources'][0]['url']=artifact.as_uri().replace('0.1.302-1','0.1.302.1')
                    else:
                        wrong['version']='2:0.1.302-1'
                    with self.assertRaises(ValueError):
                        update.validate_source_policy(wrong,policy)

    def test_grouped_release_provenance_authenticates_each_asset_without_primary_reuse(self):
        git=update.sources.git
        remote=self.root/'grouped-release';git('init',remote)
        git('config','user.email','fixture@example.invalid',cwd=remote);git('config','user.name','Fixture',cwd=remote)
        (remote/'payload').write_text('release');git('add','payload',cwd=remote);git('commit','-m','release',cwd=remote)
        commit=git('rev-parse','HEAD',cwd=remote)
        for tag in ('v1.0.0','v1.0.1'):
            git('tag',tag,cwd=remote)
        assets=[{'id':101,'name':'fonts-1.0.1.zip','browser_download_url':'https://example.invalid/fonts-1.0.1.zip'},
                {'id':102,'name':'fonts-term-1.0.1.zip','browser_download_url':'https://example.invalid/fonts-term-1.0.1.zip'}]
        release={'id':42,'draft':False,'prerelease':False,'assets':assets}
        payload=self.root/'release.json';payload.write_text(json.dumps(release))
        previous={'id':'font-release','kind':'release','repository':'fixture/fonts','url':str(remote),'source_id':'primary',
                  'related_source_ids':['term'],'asset_templates':{'primary':'fonts-{version}.zip','term':'fonts-term-{version}.zip'},
                  'tag_pattern':r'^v([0-9]+\.[0-9]+\.[0-9]+)$','accepted_tag':'v1.0.0',
                  'accepted_tag_object':commit,'accepted_peeled_commit':commit,'release_id':40,'asset_id':99}
        current={**previous,'accepted_tag':'v1.0.1','release_id':42,'asset_id':101}
        old={'aur':None,'watchers':[previous]};new={'aur':None,'watchers':[current]}
        lock={'sources':[{'id':source_id,'kind':'release','source':asset['browser_download_url'],
                          'url':asset['browser_download_url'],'release_id':42,'asset_id':asset['id']}
                         for source_id,asset in zip(('primary','term'),assets)]}
        policy={'sources':[{'id':'primary','source_template':'https://example.invalid/fonts-{version}.zip'},
                           {'id':'term','source_template':'https://example.invalid/fonts-term-{version}.zip'}]}
        fetch=update.sources.fetch
        def fixture_fetch(url,destination=None):
            self.assertEqual(url,'https://api.github.com/repos/fixture/fonts/releases/tags/v1.0.1')
            return fetch(payload.as_uri(),destination)
        with patch.object(update.sources,'public_url',lambda value:value),patch.object(update.sources,'fetch',fixture_fetch):
            self.assertEqual(update.verify_provenance(old,new,lock,policy),['font-release'])
            for mutation in ('related-id','related-url','mapping','primary-watcher-id'):
                with self.subTest(mutation=mutation):
                    wrong=copy.deepcopy(lock);provenance=copy.deepcopy(new)
                    if mutation=='related-id':
                        wrong['sources'][1]['asset_id']=101
                    elif mutation=='related-url':
                        wrong['sources'][1]['url']=wrong['sources'][0]['url']
                    elif mutation=='mapping':
                        provenance['watchers'][0]['asset_templates']['term']='fonts-{version}.zip'
                    else:
                        provenance['watchers'][0]['asset_id']=999
                    with self.assertRaises(ValueError):
                        update.verify_provenance(old,provenance,wrong,policy)
            payload.write_text(json.dumps({**release,'assets':assets+[assets[1]]}))
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                update.verify_provenance(old,new,lock,policy)

    def copy_trusted_controller(self, destination):
        repository = Path(update.__file__).resolve().parents[1]
        harness = ('tools/build.sh', 'tools/native.py', 'tools/sources.py',
                   'tools/recipe_gate.py', 'tools/github_api.py', 'tools/dependency_repo.py',
                   'keys/arch-packages.asc', 'keys/n3t.asc')
        for name in dict.fromkeys((*recipe_state.CONTROLS, *harness)):
            target = destination/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository/name, target)

    def test_controller_and_image_changes_do_not_select_packages(self):
        old=self.root/'old';new=self.root/'new'
        old.mkdir();new.mkdir()
        for tree in (old,new):
            update.dump(tree/'packages.json',{'schema':1,'packages':[{'pkgbase':'example','outputs':[]}]})
        (new/'tools').mkdir();(new/'tools/controller.py').write_text('new controller')
        (new/'build-image.txt').write_text('new image')
        self.assertEqual(update.affected_packages(old,new,{'example':{}}),([],True))
        update.dump(new/'packages.json',{'schema':1,'packages':[{'pkgbase':'example','outputs':[{'name':'example'}]}]})
        self.assertEqual(update.affected_packages(old,new,{'example':{}}),(['example'],True))

    def test_unapproved_main_tests_and_build_validation_never_execute(self):
        record={'kind':'code','pr_number':1,'base':'a'*40,'head':'b'*40}
        with patch('tools.recipe_candidates.verify_authorization',side_effect=ValueError('unapproved')),patch.object(update,'pr_identity'),patch.object(update,'checkout_data') as checkout,patch.object(update,'run_candidate_tests') as tests,patch.object(update,'validate_build_outputs') as outputs:
            with self.assertRaisesRegex(ValueError,'unapproved'):
                update.run_approved_tests(record,self.root)
            with self.assertRaisesRegex(ValueError,'unapproved'):
                update.validate_build(record,self.root)
            checkout.assert_not_called();tests.assert_not_called();outputs.assert_not_called()

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

    def test_static_human_metadata_is_preserved_without_evaluating_recipe(self):
        recipe=self.root/'submitted';recipe.mkdir()
        sentinel=self.root/'executed'
        code='touch '+str(sentinel)+'\npkgver=$(printf 9.0)\npkgrel=7\n'
        claims=SRCINFO.replace('pkgver = 1.0','pkgver = 9.0').replace('pkgrel = 1','pkgrel = 7')
        (recipe/'PKGBUILD').write_text(code)
        (recipe/'.SRCINFO').write_text(claims)
        lock={'schema':1,'version':'9.0-7','sources':[]}
        result=update.static_metadata(recipe,lock,{},self.root/'work',preserve_pkgrel=True)
        self.assertEqual(result['version'],'9.0-7')
        self.assertEqual(result['srcinfo'],claims)
        self.assertEqual((recipe/'PKGBUILD').read_text(),code)
        self.assertEqual((recipe/'.SRCINFO').read_text(),claims)
        self.assertFalse(sentinel.exists())

    def test_bootstrap_freezes_enrollment_without_executing_recipe(self):
        control=self.root/'control';recipe=control/'recipes/example';recipe.mkdir(parents=True)
        sentinel=self.root/'executed'
        payload=b'enrolled local source'
        digest=hashlib.sha256(payload).hexdigest()
        (recipe/'payload').write_bytes(payload)
        (recipe/'PKGBUILD').write_text('touch '+str(sentinel)+'\npkgver=1.0\npkgrel=7\n')
        claims=SRCINFO.replace('pkgrel = 1','pkgrel = 7').replace('pkgname = example','\tsource = payload\n\tsha256sums = '+digest+'\npkgname = example')
        (recipe/'.SRCINFO').write_text(claims)
        policy={'sources':[{'id':'payload','kind':'local','source_template':'payload','url_template':None,'checksum_algorithm':'sha256','checksum_index':0}], 'automatic':{'version':{'assignment':'pkgver','literal_assignment':'pkgver=1.0','derivation':'literal-upstream-version','template':r'^[0-9]+(?:\.[0-9]+)*$'},'pkgrel':{'assignment':'pkgrel','literal_assignment':'pkgrel=7','value':'1'},'checksums':[]}}
        output=self.root/'bootstrap'
        with patch.object(update,'ROOT',control),patch.object(update,'control_checkout',return_value=('a'*40,{'example':'b'*40})),patch.object(update,'policy_at',return_value={'example':policy}),patch.object(update,'repository',return_value='owner/repo'):
            update.bootstrap(output)
        self.assertFalse(sentinel.exists())
        lock=update.load(output/'example/lock.json')
        self.assertEqual(lock['version'],'1.0-7')
        self.assertEqual(lock['sources'][0]['checksums'],{'sha256':digest})
        self.assertEqual((output/'example/recipe/.SRCINFO').read_text(),claims)
        self.assertEqual(update.load(output/'bootstrap.json')['recipe_pins'],{'example':'b'*40})

    def test_static_discovery_renders_version_sources_and_integrity_claims(self):
        recipe=self.root/'discovery';recipe.mkdir()
        payload=b'new immutable archive'
        digest=hashlib.sha256(payload).hexdigest()
        old='a'*64
        claims=SRCINFO.replace('pkgname = example','\tsource = code-1.0.tar.gz::https://example.invalid/v1.0.tar.gz\n\tsha256sums = '+old+'\npkgname = example')
        code="pkgver=1.1\npkgrel=1\npackage() { touch never-execute; }\n"
        (recipe/'PKGBUILD').write_text(code)
        (recipe/'.SRCINFO').write_text(claims)
        policy={'sources':[{'id':'code','source_template':'code-{version}.tar.gz::https://example.invalid/v{version}.tar.gz','checksum_algorithm':'sha256','checksum_index':0}], 'automatic':{'version':{'assignment':'pkgver','derivation':'literal-upstream-version','template':r'^[0-9]+(?:\.[0-9]+)*$'},'pkgrel':{'value':'1'}}}
        source={key:None for key in update.sources.FIELDS}
        source.update(id='code',kind='archive',source='code-1.1.tar.gz::https://example.invalid/v1.1.tar.gz',url='https://example.invalid/v1.1.tar.gz',checksums={'sha256':digest})
        lock={'schema':1,'version':'1.0-1','sources':[source]}
        with patch.object(update.sources,'fetch',return_value=payload):
            result=update.static_metadata(recipe,lock,policy,self.root/'work')
        self.assertEqual(result['version'],'1.1-1')
        self.assertIn('source = '+source['source'],result['srcinfo'])
        self.assertIn('sha256sums = '+digest,result['srcinfo'])
        self.assertEqual((recipe/'PKGBUILD').read_text(),code)
        self.assertEqual((recipe/'.SRCINFO').read_text(),claims)
        with patch.object(update.sources,'fetch',return_value=b'changed remote bytes'),self.assertRaisesRegex(ValueError,'checksum'):
            update.static_metadata(recipe,lock,policy,self.root/'work')

    def test_split_runtime_equalities_follow_only_automatic_native_version(self):
        recipe=self.root/'split-discovery';recipe.mkdir()
        payload=b'immutable split release'
        digest=hashlib.sha256(payload).hexdigest()
        claims=('pkgbase = sentencepiece\n\tpkgver = 1.0\n\tpkgrel = 3\n\tepoch = 2\n'
                '\tarch = x86_64\n\tsource = code-1.0.tar.gz::https://example.invalid/v1.0.tar.gz\n'
                '\tsha256sums = '+'a'*64+'\n\tmakedepends = sentencepiece=2:1.0-3\n'
                '\tcheckdepends = sentencepiece=2:1.0-3\npkgname = sentencepiece\n'
                'pkgname = python-sentencepiece\n\tdepends = sentencepiece=2:1.0-3\n'
                '\tdepends_x86_64 = sentencepiece=2:1.0-3\n'
                '\tdepends = python-sentencepiece=2:1.0-3\n'
                '\tdepends = unrelated=2:1.0-3\n\tdepends = sentencepiece>=2:1.0-3\n'
                '\tdepends = sentencepiece=2:0.9-1\n\tdepends_aarch64 = sentencepiece=2:1.0-3\n')
        code=('pkgbase=sentencepiece\npkgname=(sentencepiece python-sentencepiece)\n'
              'pkgver=1.1\npkgrel=1\nepoch=2\n'
              'package_python-sentencepiece() { depends=("${pkgbase}=${pkgver}-${pkgrel}"); }\n')
        (recipe/'PKGBUILD').write_text(code)
        (recipe/'.SRCINFO').write_text(claims)
        policy={'sources':[{'id':'code','source_template':'code-{version}.tar.gz::https://example.invalid/v{version}.tar.gz','checksum_algorithm':'sha256','checksum_index':0}],
                'automatic':{'version':{'assignment':'pkgver','derivation':'literal-upstream-version','template':r'[0-9]+\.[0-9]+'},'pkgrel':{'value':'1'}}}
        source={key:None for key in update.sources.FIELDS}
        source.update(id='code',kind='archive',source='code-1.1.tar.gz::https://example.invalid/v1.1.tar.gz',
                      url='https://example.invalid/v1.1.tar.gz',checksums={'sha256':digest})
        lock={'schema':1,'version':'2:1.1-1','sources':[source]}
        expected=claims.replace('pkgver = 1.0','pkgver = 1.1').replace('pkgrel = 3','pkgrel = 1')
        expected=expected.replace('code-1.0.tar.gz','code-1.1.tar.gz').replace('/v1.0.tar.gz','/v1.1.tar.gz').replace('a'*64,digest)
        for name in ('sentencepiece','python-sentencepiece'):
            for key in ('depends','depends_x86_64'):
                expected=expected.replace('\t'+key+' = '+name+'=2:1.0-3\n','\t'+key+' = '+name+'=2:1.1-1\n')
        for render in (update.static_metadata,update.frozen_metadata):
            with self.subTest(renderer=render.__name__),patch.object(update.sources,'fetch',return_value=payload):
                result=render(recipe,lock,policy,self.root/'split-work')
                self.assertEqual(result['srcinfo'],expected)
                self.assertEqual(result['version'],'2:1.1-1')
                submitted=render(recipe,lock,policy,self.root/'split-human',preserve_pkgrel=True)
                self.assertEqual(submitted['srcinfo'],claims)
                self.assertEqual(submitted['version'],'2:1.0-3')
        self.assertEqual((recipe/'PKGBUILD').read_text(),code)
        self.assertEqual((recipe/'.SRCINFO').read_text(),claims)
        with patch.object(update.sources,'fetch',return_value=b'tampered archive'),self.assertRaisesRegex(ValueError,'checksum'):
            update.static_metadata(recipe,lock,policy,self.root/'split-tamper')

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
        record={'schema':1,'repository':'owner/repo','base':'a'*40,'head':'b'*40,'recipe_pins':{'example':'1'*40},'previous_recipe_pins':{'example':'2'*40},'run_id':'1','run_attempt':'1','image':'arch@sha256:'+'c'*64,'harness_sha':'e'*64,'pr_number':1,'kind':'bookkeeping','mechanical':False,'auto_merge':True,'packages':[package]}
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
            update.validate_build_outputs(record,self.root)
        status.assert_not_called()
        evidence['packages'][0]['recipe_commit']=record['packages'][0]['recipe_commit']
        evidence['recipe_pins']={'example':'9'*40}
        update.dump(self.root/'native-evidence.json',evidence)
        with patch.object(update,'pr_identity'),patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'recipe_pins'):
            update.validate_build_outputs(record,self.root)
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
        self.assertEqual(update.affected_packages(old,new,{'example':{},'second':{}}),(['example'],True))

    def test_prepare_classifies_full_pinned_recipe_and_history_not_opaque_sha(self):
        policy={'pkgbase':'example','sources':[],'automatic':{'version':{'assignment':'pkgver'},'pkgrel':{'assignment':'pkgrel'},'checksums':[]}}
        for mutation in ('version','code','hidden','mode','rewrite','modules'):
            with self.subTest(mutation=mutation):
                fixture=self.root/mutation
                for label,version in [('old','1.0'),('new','1.1')]:
                    root=fixture/label;recipe=root/'recipes/example';recipe.mkdir(parents=True)
                    self.copy_trusted_controller(root)
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
                with patch.dict(os.environ,{'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'},clear=True),patch.object(update,'api',return_value={'base':{'ref':'main'}}),patch.object(update.sources,'git',return_value='a'*40),patch('tools.recipe_acceptance.validate_bookkeeping',return_value=None),patch.object(update,'require_review_environment'),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'pr_identity',return_value=({'head':{'repo':{'full_name':'owner/repo'}}},'a'*40,'b'*40)),patch.object(update,'checkout_data',side_effect=checkout),patch.object(update,'harness_digest',return_value='e'*64),patch.object(update,'run_candidate_tests'),patch.object(update,'independent_transition',return_value=evidence),patch.object(update.recipes,'is_ancestor',return_value=mutation!='rewrite'),patch('tools.recipe_gate._compare',return_value=1),patch.object(update,'status'):
                    with patch.object(update,'run_candidate_tests',side_effect=AssertionError('unapproved execution')),patch.object(recipe_state,'load_optional',return_value=None):
                        if mutation == 'rewrite':
                            with self.assertRaisesRegex(ValueError, 'fast-forward'):
                                update.prepare(1,fixture/'prepared')
                            continue
                        record=update.prepare(1,fixture/'prepared')
                self.assertEqual(record['auto_merge'], mutation != 'code')
                self.assertEqual(record['review_environment'], 'recipe-review' if mutation == 'code' else '')
                self.assertEqual(record['packages'][0]['recipe_commit'],'2'*40)
                self.assertEqual(record['packages'][0]['previous_recipe_commit'],'1'*40)
                if mutation not in {'version','modules'}:
                    self.assertNotEqual(record['decisions'][0]['decision'],'mechanical')

    def test_source_only_main_automatic_authority_rejects_external_or_missing_head_repository(self):
        from tools import recipe_candidates
        policy = {'pkgbase': 'example', 'sources': [], 'automatic': {}}
        def checkout(sha, destination, pins):
            self.copy_trusted_controller(destination)
            pins['example'] = '1'*40
            update.dump(destination/'packages.json', {'schema': 1, 'packages': [policy]})
            update.dump(destination/'inputs/example.json', {'schema': 1, 'version': '1.0-1', 'sources': []})
            recipe = destination/'recipes/example'
            recipe.mkdir(parents=True)
            (recipe/'PKGBUILD').write_text('pkgver=1.0\npkgrel=1\n')
            (recipe/'.SRCINFO').write_text(SRCINFO)
            (destination/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'e'*64)
            (destination/'tools/controller-change.py').write_text('accepted' if sha == 'a'*40 else 'new tooling')
            return destination
        same_repo = {'head': {'repo': {'full_name': 'owner/repo'}}}
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1'}), patch.object(update, 'api', return_value={'base': {'ref': 'main'}}), patch.object(update.sources, 'git', return_value='a'*40), patch('tools.recipe_acceptance.validate_bookkeeping', return_value=None), patch.object(update, 'repository', return_value='owner/repo'), patch.object(update, 'pr_identity', return_value=(same_repo, 'a'*40, 'b'*40)) as identity, patch.object(update, 'checkout_data', side_effect=checkout), patch.object(update, 'run_candidate_tests', side_effect=AssertionError('prepare cannot execute source')), patch.object(update, 'status'), patch.object(recipe_state, 'load_optional', return_value=None), patch.object(recipe_state, 'save') as save, patch.object(recipe_state, 'attest'):
            record = update.prepare(1, self.root/'owned-source')
            self.assertEqual(record['packages'], [])
            self.assertEqual(record['review_environment'], '')
            for index, head in enumerate(({'repo': {'full_name': 'outsider/repo'}}, {'repo': None}, {})):
                with self.subTest(head=head):
                    foreign_pr = {'head': head}
                    identity.return_value = (foreign_pr, 'a'*40, 'b'*40)
                    save.reset_mock()
                    with self.assertRaisesRegex(ValueError, 'same-repository'):
                        update.prepare(1, self.root/f'foreign-source-{index}')
                    save.assert_not_called()
                    with patch.object(update, 'api', return_value=foreign_pr), self.assertRaisesRegex(ValueError, 'same-repository'):
                        recipe_candidates._verify_inputs(record, True, update.independent_transition, update.static_metadata)

    def test_retired_enrollment_is_not_built_and_requires_no_review(self):
        kept = {'pkgbase': 'kept', 'sources': [], 'automatic': {}}
        retired = {'pkgbase': 'retired', 'sources': [], 'automatic': {}}
        def checkout(sha, destination, pins):
            destination.mkdir()
            self.copy_trusted_controller(destination)
            is_old = sha == 'a'*40
            pins.update({'kept': '1'*40, **({'retired': '2'*40} if is_old else {})})
            update.dump(destination/'packages.json', {'schema': 1, 'packages': [kept, retired] if is_old else [kept]})
            (destination/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'e'*64)
            update.dump(destination/'inputs/kept.json', {'schema': 1, 'version': '1.0-1', 'sources': []})
            recipe = destination/'recipes/kept'
            recipe.mkdir(parents=True)
            (recipe/'PKGBUILD').write_text('pkgver=1.0\npkgrel=1\n')
            (recipe/'.SRCINFO').write_text(SRCINFO.replace('example', 'kept'))
            return destination
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1'}),patch.object(update,'api',return_value={'base':{'ref':'main'}}),patch.object(update.sources,'git',return_value='a'*40),patch('tools.recipe_acceptance.validate_bookkeeping',return_value=None),patch.object(update,'require_review_environment'), patch.object(update, 'repository', return_value='owner/repo'), patch.object(update, 'pr_identity', return_value=({'head': {'repo': {'full_name': 'owner/repo'}}}, 'a'*40, 'b'*40)), patch.object(update, 'checkout_data', side_effect=checkout), patch.object(update, 'harness_digest', return_value='e'*64), patch.object(update, 'run_candidate_tests'), patch.object(update, 'independent_transition', return_value=None), patch.object(update, 'status'):
            with patch.object(recipe_state, 'load_optional', return_value=None):
                record = update.prepare(1, self.root/'prepared-retirement')
        self.assertEqual(record['packages'], [])
        self.assertFalse(record['mechanical'])
        self.assertTrue(record['auto_merge'])
        self.assertEqual(record['review_environment'], '')
        self.assertIn({'pkgbase': 'retired', 'decision': 'manual', 'reason': 'Package enrollment retired'}, record['decisions'])

    def test_new_enrollment_is_frozen_for_one_authorized_build(self):
        kept={'pkgbase':'kept','sources':[],'automatic':{}}
        added={'pkgbase':'added','sources':[],'automatic':{}}
        def checkout(sha,destination,pins):
            is_old=sha=='a'*40
            policies=[kept] if is_old else [kept,added]
            self.copy_trusted_controller(destination)
            update.dump(destination/'packages.json',{'schema':1,'packages':policies})
            (destination/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'e'*64)
            for policy in policies:
                name=policy['pkgbase'];pins[name]=('1' if name=='kept' else '2')*40
                update.dump(destination/'inputs'/f'{name}.json',{'schema':1,'version':'1.0-1','sources':[]})
                recipe=destination/'recipes'/name;recipe.mkdir(parents=True)
                (recipe/'PKGBUILD').write_text('pkgver=1.0\\npkgrel=1\\n')
                (recipe/'.SRCINFO').write_text(SRCINFO.replace('example',name))
            return destination
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'}),patch.object(update,'api',return_value={'base':{'ref':'main'}}),patch.object(update.sources,'git',return_value='a'*40),patch('tools.recipe_acceptance.validate_bookkeeping',return_value=None),patch.object(update,'require_review_environment'),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'pr_identity',return_value=({'head':{'repo':{'full_name':'owner/repo'}}},'a'*40,'b'*40)),patch.object(update,'checkout_data',side_effect=checkout),patch.object(update,'harness_digest',return_value='e'*64),patch.object(update,'run_candidate_tests',side_effect=AssertionError('unapproved execution')),patch.object(update,'status'):
            with patch.object(recipe_state,'load_optional',return_value=None):
                record=update.prepare(1,self.root/'prepared-enrollment')
        self.assertEqual([package['pkgbase'] for package in record['packages']],['added'])
        self.assertIsNone(record['packages'][0]['previous_recipe_commit'])
        self.assertEqual(record['review_environment'],'recipe-review')
        self.assertFalse(record['auto_merge'])

    def test_pin_lookup_rejects_flattened_or_wrong_gitlink(self):
        for mode,kind,sha in [('100644','blob','1'*40),('040000','tree','1'*40),('160000','commit','not-a-sha')]:
            with self.subTest(mode=mode,sha=sha),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=[{'tree':{'sha':'a'*40}},{'tree':[{'path':'recipes/example','mode':mode,'type':kind,'sha':sha}]}]),self.assertRaisesRegex(ValueError,'gitlink'):
                update.pin_at('b'*40,'example')

    def test_full_outputs_required_before_any_success_status(self):
        record,evidence,path=self.record()
        evidence['packages'][0]['files']=[];update.dump(self.root/'native-evidence.json',evidence)
        with patch.object(update,'pr_identity'),patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'missing/extra'):
            update.validate_build_outputs(record,self.root)
        status.assert_not_called()

    def test_native_metadata_and_bytes_are_bound(self):
        record,evidence,path=self.record()
        path.write_bytes(b'altered')
        with patch.object(update,'pr_identity'),patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'bytes/version'):
            update.validate_build_outputs(record,self.root)
        status.assert_not_called()
        path.write_bytes(b'package bytes');evidence['packages'][0]['metadata']['srcinfo']=SRCINFO.replace('1.0','2.0');update.dump(self.root/'native-evidence.json',evidence)
        with patch.object(update,'pr_identity'),patch.object(update,'status'),self.assertRaisesRegex(ValueError,'metadata'):
            update.validate_build_outputs(record,self.root)

    def test_stale_head_after_human_approval_cannot_finalize(self):
        record,evidence,path=self.record();record['auto_merge']=False
        update.dump(self.root/'candidate.json',record)
        pr={'state':'open','base':{'ref':'main','sha':record['base'],'repo':{'full_name':'owner/repo'}},'head':{'sha':'c'*40}}
        with patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'main_sha',return_value=record['base']),patch.object(update,'api',return_value=pr) as api,patch.object(update,'status') as status,self.assertRaisesRegex(ValueError,'changed'):
            update.finalize(1,record['base'],record['head'],self.root/'candidate.json',self.root)
        status.assert_not_called()
        self.assertTrue(all(call.args[1:] == () for call in api.call_args_list))

    def test_reviewed_candidate_merges_after_full_successful_checks(self):
        record,evidence,path=self.record();record['auto_merge']=False
        update.dump(self.root/'candidate.json',record)
        states=[{'context':name,'state':'success'} for name in ('verify','candidate-build','recipe-policy')]
        with patch.object(update,'pr_identity'),patch.object(update,'validate_build'),patch.object(update,'api',side_effect=[states, {'merged':True,'sha':'f'*40}, None]) as api,patch.object(update,'repository',return_value='owner/repo'):
            result=update.finalize(1,record['base'],record['head'],self.root/'candidate.json',self.root)
        self.assertTrue(result['merged'])
        self.assertEqual(api.call_args_list[1].args[2], {'sha':record['head'],'merge_method':'merge'})

    def test_bookkeeping_merge_requires_successful_exact_head_verification(self):
        record, evidence, path = self.record()
        update.dump(self.root/'candidate.json', record)
        for state in (None, 'pending', 'failure'):
            states = [{'context': name, 'state': 'success'} for name in ('candidate-build', 'recipe-policy')]
            if state is not None:
                states.append({'context': 'verify', 'state': state})
            with self.subTest(state=state), patch.object(update, 'pr_identity'), patch.object(update, 'validate_build'), patch.object(update, 'repository', return_value='owner/repo'), patch.object(update, 'api', side_effect=[states, {'merged': True, 'sha': 'f'*40}, None]) as api:
                with self.assertRaisesRegex(ValueError, 'statuses missing'):
                    update.finalize(1, record['base'], record['head'], self.root/'candidate.json', self.root)
                self.assertFalse(any(call.args[1:2] == ('PUT',) for call in api.call_args_list))

    def test_cleanup_removes_readonly_mirror_without_touching_symlink_target(self):
        mirror = self.root/'verification'/'mirror.git'
        mirror.mkdir(parents=True)
        (mirror/'object').write_bytes(b'git object')
        outside = self.root/'outside'
        outside.mkdir()
        (outside/'keep').write_bytes(b'outside')
        (mirror/'link').symlink_to(outside, target_is_directory=True)
        mirror.chmod(0o555)
        outside.chmod(0o555)
        try:
            update.remove_verification_tree(mirror.parent)
            self.assertFalse(mirror.parent.exists())
            self.assertEqual((outside/'keep').read_bytes(), b'outside')
            self.assertEqual(outside.stat().st_mode & 0o777, 0o555)
        finally:
            outside.chmod(0o755)
            if mirror.exists():
                mirror.chmod(0o755)

    def test_bookkeeping_merge_has_expected_head_and_explicit_dispatch(self):
        record,evidence,path=self.record();update.dump(self.root/'candidate.json',record)
        responses=[[{'context':name,'state':'success'} for name in ('verify','candidate-build','recipe-policy')],{'merged':True,'sha':'f'*40},None]
        with patch.object(update,'pr_identity'),patch.object(update,'validate_build'),patch.object(update,'repository',return_value='owner/repo'),patch.object(update,'api',side_effect=responses) as api:
            update.finalize(1,record['base'],record['head'],self.root/'candidate.json',self.root)
        self.assertEqual(api.call_args_list[1].args[2],{'sha':record['head'],'merge_method':'merge'})
        self.assertEqual(api.call_args_list[2].args[2]['inputs']['accepted_sha'],'f'*40)


if __name__=='__main__':unittest.main()
