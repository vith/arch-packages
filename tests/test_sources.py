import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import sources


class SourceContracts(unittest.TestCase):
    def setUp(self):
        root=Path.home()/'.local/state/arch-packages/work/arch-package-tests'
        root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.repo=self.root/'remote'
        sources.git('init',self.repo)
        sources.git('config','user.email','fixture@example.invalid',cwd=self.repo)
        sources.git('config','user.name','Fixture',cwd=self.repo)
        (self.repo/'file').write_text('first')
        sources.git('add','file',cwd=self.repo)
        sources.git('commit','-m','first',cwd=self.repo)
        sources.git('branch','-M','integration',cwd=self.repo)
        sources.git('tag','v1.0.0',cwd=self.repo)
        self.head=sources.git('rev-parse','HEAD',cwd=self.repo)
        self.url=str(self.repo)

    def freeze(self,directory):
        with patch.object(sources,'public_url',lambda value:value):
            return sources.freeze_source({'id':'code','kind':'git','source':'git+'+self.url+'#branch=integration','url':self.url,'ref':'refs/heads/integration','commit':self.head,'checksums':{'sha256':'SKIP'},'work_dir':str(directory)})

    def test_fetch_does_not_recurse_into_exported_recipe_directories(self):
        (self.repo / '.gitmodules').write_text('[submodule "recipes/example"]\npath = recipes/example\nurl = ' + self.url + '\n')
        sources.git('add', '.gitmodules', cwd=self.repo)
        sources.git('update-index', '--add', '--cacheinfo', '160000,' + self.head + ',recipes/example', cwd=self.repo)
        sources.git('commit', '-m', 'control', cwd=self.repo)
        checkout = self.root / 'checkout'
        sources.git('clone', self.repo, checkout)
        (checkout / 'recipes/example/PKGBUILD').write_text('exported recipe')
        sources.git('update-index', '--cacheinfo', '160000,' + 'a'*40 + ',recipes/example', cwd=self.repo)
        sources.git('commit', '-m', 'new pin', cwd=self.repo)
        sources.git('fetch', self.repo, 'HEAD', cwd=checkout)
        self.assertEqual(sources.git('rev-parse', 'FETCH_HEAD', cwd=checkout), sources.git('rev-parse', 'HEAD', cwd=self.repo))

    def test_frozen_aur_uses_exact_commits_after_branch_advance(self):
        watcher={'package':'example','url':self.url,'ref':'refs/heads/integration','work_dir':str(self.root/'frozen-aur')}
        old=self.head
        (self.repo/'file').write_text('second');sources.git('add','file',cwd=self.repo);sources.git('commit','-m','second',cwd=self.repo)
        selected=sources.git('rev-parse','HEAD',cwd=self.repo)
        (self.repo/'file').write_text('third');sources.git('add','file',cwd=self.repo);sources.git('commit','-m','third',cwd=self.repo)
        with patch.object(sources,'public_url',lambda value:value):
            transition=sources.frozen_aur(watcher,{'commit':old},{'commit':selected})
            live=sources.discover_aur({**watcher,'work_dir':str(self.root/'live-aur')},{'commit':old})
        self.assertEqual(transition['files'],[{'path':'file','mode':'100644','content':'second'}])
        self.assertEqual(transition['previous_files'],[{'path':'file','mode':'100644','content':'first'}])
        self.assertTrue(transition['fast_forward'])
        self.assertNotEqual(live['commit'],transition['commit'])
        with self.assertRaisesRegex(ValueError,'invalid frozen AUR'):
            sources.frozen_aur({**watcher,'work_dir':str(self.root/'bad-aur')},{'commit':old},{'commit':'--upload-pack=bad'})

    def test_frozen_ancestry_survives_new_tags_and_checks_immutable_identity(self):
        frozen=self.freeze(self.root/'ancestry')
        template=r'^([0-9]+(?:\.[0-9]+)+)\.fork\.r([0-9]+)\.g([0-9a-f]{12})$'
        (self.repo/'file').write_text('second');sources.git('add','file',cwd=self.repo);sources.git('commit','-m','second',cwd=self.repo)
        sources.git('tag','-f','v1.0.0',cwd=self.repo)
        self.assertEqual(sources.frozen_ancestry_version(frozen,self.root/'ancestry/freeze.git',template),'1.0.0.fork.r0.g'+self.head[:12])
        frozen['git_context']['version_tag']['commit']='f'*40
        with self.assertRaisesRegex(ValueError,'version tag'):
            sources.frozen_ancestry_version(frozen,self.root/'ancestry/freeze.git',template)


    def test_revision_count_uses_locked_full_history_without_remote_access(self):
        sources.git('tag','-d','v1.0.0',cwd=self.repo)
        frozen=self.freeze(self.root/'count-old')
        rules={'derivation':'frozen-git-revision-count','template':r'^r[0-9]+\.[0-9a-f]{7,40}$'}
        for value in ('second','third'):
            (self.repo/'file').write_text(value)
            sources.git('add','file',cwd=self.repo)
            sources.git('commit','-m',value,cwd=self.repo)
        self.head=sources.git('rev-parse','HEAD',cwd=self.repo)
        advanced=self.freeze(self.root/'count-new')
        git=sources.git
        def offline_git(*args,**kwargs):
            if args[0] in {'fetch','ls-remote','clone'}:
                raise AssertionError('frozen derivation reached remote')
            return git(*args,**kwargs)
        with patch.object(sources,'git',offline_git):
            self.assertEqual(sources._frozen_git_version(frozen,self.root/'count-old/freeze.git',rules),
                             'r1.'+frozen['commit'][:7])
            self.assertEqual(sources._frozen_git_version(advanced,self.root/'count-new/freeze.git',rules),
                             'r3.'+advanced['commit'][:7])
            advanced['commit']=frozen['commit']
            with self.assertRaisesRegex(ValueError,'commit/ref mismatch'):
                sources._frozen_git_version(advanced,self.root/'count-new/freeze.git',rules)

    def test_revision_count_rejects_shallow_history(self):
        sources.git('tag','-d','v1.0.0',cwd=self.repo)
        (self.repo/'file').write_text('second')
        sources.git('add','file',cwd=self.repo)
        sources.git('commit','-m','second',cwd=self.repo)
        self.head=sources.git('rev-parse','HEAD',cwd=self.repo)
        frozen=self.freeze(self.root/'count-full')
        shallow=self.root/'shallow.git'
        sources.git('clone','--bare','--depth','1','--branch','integration',self.repo.as_uri(),shallow)
        rules={'derivation':'frozen-git-revision-count','template':r'^r[0-9]+\.[0-9a-f]{7,40}$'}
        with self.assertRaisesRegex(ValueError,'full Git history'):
            sources._frozen_git_version(frozen,shallow,rules)

    def test_version_tag_context_changes_even_with_same_commit(self):
        frozen=self.freeze(self.root/'first')
        sources.git('tag','-a','v1.0.1','-m','new version',cwd=self.repo)
        with patch.object(sources,'public_url',lambda value:value):
            transition=sources.discover_git({'url':self.url,'ref':'refs/heads/integration','work_dir':str(self.root/'discovery')},frozen)
        self.assertEqual(transition['commit'],self.head)
        self.assertNotEqual(transition['git_context'],frozen['git_context'])

    def test_retag_is_not_unchanged(self):
        frozen=self.freeze(self.root/'first')
        (self.repo/'file').write_text('second')
        sources.git('add','file',cwd=self.repo);sources.git('commit','-m','second',cwd=self.repo)
        sources.git('tag','-f','v1.0.0',cwd=self.repo)
        with patch.object(sources,'public_url',lambda value:value),self.assertRaisesRegex(ValueError,'authentic tag'):
            sources.discover_git({'url':self.url,'ref':'refs/heads/integration','work_dir':str(self.root/'discovery')},frozen)

    def test_non_fast_forward_is_human_transition(self):
        frozen=self.freeze(self.root/'first')
        sources.git('checkout','--orphan','replacement',cwd=self.repo)
        (self.repo/'file').write_text('replacement');sources.git('add','file',cwd=self.repo);sources.git('commit','-m','replacement',cwd=self.repo)
        sources.git('branch','-f','integration','HEAD',cwd=self.repo)
        with patch.object(sources,'public_url',lambda value:value):
            transition=sources.discover_git({'url':self.url,'ref':'refs/heads/integration','work_dir':str(self.root/'discovery')},frozen)
        self.assertFalse(transition['fast_forward'])

    def test_exact_unchanged_is_idempotent(self):
        frozen=self.freeze(self.root/'first')
        with patch.object(sources,'public_url',lambda value:value):
            result=sources.discover_git({'url':self.url,'ref':'refs/heads/integration','work_dir':str(self.root/'discovery')},frozen)
        self.assertIsNone(result)

    def test_static_ancestry_uses_authenticated_tag_count_and_twelve_digit_sha(self):
        for value in ('second','third'):
            (self.repo/'file').write_text(value)
            sources.git('add','file',cwd=self.repo)
            sources.git('commit','-m',value,cwd=self.repo)
        self.head=sources.git('rev-parse','HEAD',cwd=self.repo)
        frozen=self.freeze(self.root/'freeze')
        template=r'^([0-9]+(?:\.[0-9]+)+)\.fork\.r([0-9]+)\.g([0-9a-f]{12})$'
        version=sources.ancestry_version(frozen,self.root/'freeze/freeze.git',template)
        self.assertEqual(version,'1.0.0.fork.r2.g'+self.head[:12])
        sources.git('tag','-f','v1.0.0',cwd=self.repo)
        with self.assertRaisesRegex(ValueError,'authentic tag'):
            sources.ancestry_version(frozen,self.root/'freeze/freeze.git',template)

    def test_frozen_archive_rejects_changed_transport_bytes(self):
        archive=self.root/'release.tar'
        archive.write_bytes(b'original immutable archive')
        spec={'id':'archive','kind':'archive','source':'release.tar','url':archive.as_uri(),
              'checksums':{'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}}
        with patch.object(sources,'public_url',lambda value:value):
            frozen=sources.freeze_source(spec)
            self.assertEqual(frozen['checksums'],spec['checksums'])
            archive.write_bytes(b'changed release bytes')
            with self.assertRaisesRegex(ValueError,'source checksum mismatch'):
                sources.freeze_source(frozen)


    def test_static_git_integrity_matches_sanitized_native_archive(self):
        (self.repo/'.gitattributes').write_text('file export-ignore\n')
        sources.git('add','.gitattributes',cwd=self.repo)
        sources.git('commit','-m','attributes',cwd=self.repo)
        sources.git('tag','v1.0.1',cwd=self.repo)
        self.head=sources.git('rev-parse','HEAD',cwd=self.repo)
        spec={'id':'code','kind':'git','source':'git+'+self.url+'#tag=v1.0.1','url':self.url,'ref':'refs/tags/v1.0.1','checksums':{'sha256':'a'*64},'work_dir':str(self.root/'freeze')}
        with patch.object(sources,'public_url',lambda value:value):
            frozen=sources.freeze_source(spec)
        mirror=self.root/'freeze/freeze.git'
        archive=subprocess.run(['git','-c','core.abbrev=no','archive','--format','tar','refs/tags/v1.0.1'],cwd=mirror,check=True,capture_output=True).stdout
        self.assertEqual(sources.git_checksums(frozen,mirror),{'sha256':hashlib.sha256(archive).hexdigest()})
        # Attribute-controlled omission must not silently alter the pinned bytes.
        (mirror/'info/attributes').write_text('')
        with self.assertRaisesRegex(ValueError,'sanitized'):
            sources.git_checksums(frozen,mirror)

    def test_version_tag_tamper_fails(self):
        frozen=self.freeze(self.root/'first')
        frozen['git_context']['version_tag']['commit']='f'*40
        lock={'schema':1,'version':'1.0.0-1','sources':[frozen]}
        with patch.object(sources,'public_url',lambda value:value),self.assertRaisesRegex(ValueError,'version tag'):
            sources.materialize_sources(lock,self.root/'materialized')

    def test_direct_fetch_materializes_pinned_readonly_mirror(self):
        frozen=self.freeze(self.root/'first')
        (self.repo/'file').write_text('second')
        sources.git('add','file',cwd=self.repo)
        sources.git('commit','-m','second',cwd=self.repo)
        sources.git('tag','v2.0.0',cwd=self.repo)
        with patch.object(sources,'public_url',lambda value:value),patch.object(sources,'fetch',side_effect=AssertionError('no archive download')):
            mapping=sources.materialize_sources({'schema':1,'version':'1.0.0-1','sources':[frozen]},self.root/'materialized')
        mirror=mapping[self.url]
        self.assertEqual(sources.git('rev-parse','refs/heads/integration',cwd=mirror),self.head)
        self.assertEqual(sources.git('describe','--tags',self.head,cwd=mirror),'v1.0.0')
        self.assertFalse(mirror.stat().st_mode&0o222)
        for path in mirror.rglob('*'):
            if path.is_dir():path.chmod(0o755)
        mirror.chmod(0o755)

    @unittest.skipUnless(Path('/etc/arch-release').is_file(), 'native probe requires Arch runtime')
    def test_native_probe_rejects_divergent_or_disabled_config_authority(self):
        lock={'schema':1,'version':'1.0.0-1','sources':[self.freeze(self.root/'freeze')]}
        recipe=self.root/'recipe'
        recipe.mkdir()
        (recipe/'.SRCINFO').write_text('pkgbase = example\npkgver = 1.0.0\npkgrel = 1\narch = x86_64\npkgname = example\n')
        config=str(self.root/'gitconfig')
        env={'MAKEPKG_GIT_CONFIG':config,'GIT_CONFIG_SYSTEM':config,'GIT_CONFIG_GLOBAL':'/dev/null'}
        for change in ({'MAKEPKG_GIT_CONFIG':config+'-other'},{'GIT_CONFIG_NOSYSTEM':'1'},{'GIT_CONFIG_GLOBAL':config}):
            with self.subTest(change=change),patch.dict(os.environ,{**env,**change},clear=True),patch.object(sources.os,'geteuid',return_value=1000),patch.object(sources,'public_url',lambda value:value):
                with self.assertRaisesRegex(ValueError,'same frozen system configuration'):
                    sources.probe_recipe(recipe,lock,{})

    @unittest.skipUnless(Path('/usr/share/makepkg/source/git.sh').is_file(), 'installed makepkg Git library required')
    @unittest.skipUnless(os.geteuid()==0, 'root required for root-owned readonly frozen fixture')
    def test_makepkg_git_library_preserves_frozen_system_rewrites(self):
        sources.git('tag','-f','-a','v1.0.0','-m','authentic version',cwd=self.repo)
        tag=sources.git('rev-parse','refs/tags/v1.0.0',cwd=self.repo)
        frozen=self.freeze(self.root/'freeze')
        with patch.object(sources,'public_url',lambda value:value):
            mirror=sources.materialize_sources({'schema':1,'version':'1.0.0-1','sources':[frozen]},self.root/'materialized')[self.url]
        def writable_mirror():
            mirror.chmod(0o755)
            for path in mirror.rglob('*'):
                if path.is_dir():path.chmod(0o755)
        self.addCleanup(writable_mirror)
        config=self.root/'gitconfig'
        config.write_text('[core]\n hooksPath = /dev/null\n[credential]\n helper =\n[protocol "file"]\n allow = always\n')
        sources.git('config','--file',config,'--add',f'url.file://{mirror}.insteadOf',self.url)
        sources.git('config','--file',config,'--add','safe.directory',mirror)
        config.chmod(0o444)
        self.assertEqual(config.stat().st_uid,0)
        self.assertFalse(config.stat().st_mode&0o222)
        self.assertEqual(mirror.stat().st_uid,0)
        self.assertFalse(mirror.stat().st_mode&0o222)
        (self.repo/'file').write_text('moving integration')
        sources.git('add','file',cwd=self.repo)
        sources.git('commit','-m','advance live integration',cwd=self.repo)
        live=sources.git('rev-parse','HEAD',cwd=self.repo)
        sources.git('tag','-f','-a','v1.0.0','-m','changed live version',cwd=self.repo)
        live_tag=sources.git('rev-parse','refs/tags/v1.0.0',cwd=self.repo)
        base={'PATH':'/usr/bin:/bin','HOME':str(self.root),'LANG':'C.UTF-8','LIBRARY':'/usr/share/makepkg','MAKEPKG_LIBRARY':'/usr/share/makepkg'}
        old={**base,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_SYSTEM':'/dev/null','GIT_CONFIG_GLOBAL':str(config)}
        fixed={**base,'MAKEPKG_GIT_CONFIG':str(config),'GIT_CONFIG_SYSTEM':str(config),'GIT_CONFIG_GLOBAL':'/dev/null'}
        for mode,env in [('old',old),('fixed',fixed)]:
            for library in (False,True):
                with self.subTest(mode=mode,makepkg_library=library):
                    checkout=self.root/f'{mode}-{library}.git'
                    script='set -eo pipefail\n'
                    if library:
                        script+='source "$LIBRARY/source/git.sh"\n'
                    script+='git clone --mirror "$1" "$2"\ngit -C "$2" rev-parse refs/heads/integration refs/tags/v1.0.0 "refs/tags/v1.0.0^{commit}"\n'
                    result=subprocess.run(['bash','-c',script,'fixture',self.url,str(checkout)],cwd=self.root,env=env,check=True,capture_output=True,text=True)
                    expected=[live,live_tag,live] if mode=='old' and library else [self.head,tag,self.head]
                    self.assertEqual(result.stdout.splitlines(),expected)

    def test_prefixed_tag_selection_orders_numeric_versions_and_rejects_retarget(self):
        for tag in ('B5.0.1','B7','B10'):
            sources.git('tag',tag,cwd=self.repo)
        watcher={'kind':'tag','repository':'fixture/looking-glass','tag_pattern':r'^B([0-9]+(?:\.[0-9]+)*)$','version_prefix':'B'}
        git=sources.git
        def fixture_git(*args,**kwargs):
            args=tuple(self.url if value=='https://github.com/fixture/looking-glass.git' else value for value in args)
            return git(*args,**kwargs)
        with patch.object(sources,'git',fixture_git):
            dotted={**watcher,'tag_pattern':r'^B(5\.[0-9]+\.[0-9]+)$'}
            self.assertEqual(sources.discover_release_tag(dotted,{})['version'],'B5.0.1')
            self.assertEqual(sources.discover_release_tag({**dotted,'version_prefix':''},{})['version'],'5.0.1')
            discovered=sources.discover_release_tag(watcher,{'tag':'B7','tag_object':self.head})
            self.assertEqual((discovered['tag'],discovered['version']),('B10','B10'))
            self.assertIsNone(sources.discover_release_tag(watcher,{'tag':'B10','tag_object':self.head}))
            (self.repo/'file').write_text('retargeted')
            git('add','file',cwd=self.repo);git('commit','-m','retarget',cwd=self.repo)
            git('tag','-f','B10',cwd=self.repo)
            with self.assertRaisesRegex(ValueError,'retargeted or missing'):
                sources.discover_release_tag(watcher,{'tag':'B10','tag_object':self.head})

    def test_prefixed_tag_grammar_rejects_invalid_prefix_and_nonnumeric_capture(self):
        watcher={'kind':'tag','repository':'fixture/looking-glass','tag_pattern':r'^B([0-9]+(?:\.[0-9]+)*)$'}
        for prefix in ('B7','B-','B'*17,None):
            with self.subTest(prefix=prefix),self.assertRaisesRegex(ValueError,'version prefix'):
                sources.discover_release_tag({**watcher,'version_prefix':prefix},{})
        sources.git('tag','Bbad',cwd=self.repo)
        git=sources.git
        def fixture_git(*args,**kwargs):
            args=tuple(self.url if value=='https://github.com/fixture/looking-glass.git' else value for value in args)
            return git(*args,**kwargs)
        with patch.object(sources,'git',fixture_git),self.assertRaisesRegex(ValueError,'numeric version groups'):
            sources.discover_release_tag({**watcher,'version_prefix':'B','tag_pattern':r'^B(.+)$'},{})

    def test_release_asset_templates_select_per_source_and_reject_unsafe_names(self):
        watcher={'source_id':'primary','asset':'legacy.zip','asset_templates':{'primary':'fresh-{version}-source.tar.gz','related':'fonts.zip'}}
        self.assertEqual(sources.release_asset_name(watcher,'1.2.3'),'fresh-1.2.3-source.tar.gz')
        self.assertEqual(sources.release_asset_name(watcher,'1.2.3','related'),'fonts.zip')
        self.assertEqual(sources.release_asset_name({'asset':'legacy.zip'},'1.2.3'),'legacy.zip')
        with self.assertRaisesRegex(ValueError,'lacks enrolled asset'):
            sources.release_asset_name(watcher,'1.2.3','missing')
        for template in ('../{version}.zip','a/{version}.zip','{version}@host','{version.__class__}','{unknown}','x'*256):
            with self.subTest(template=template),self.assertRaises(ValueError):
                sources.release_asset_name({'source_id':'primary','asset_templates':{'primary':template}},'1.2.3')

    def test_release_discovery_authenticates_versioned_and_related_assets(self):
        release={'id':42,'tag_name':'v1.0.0','draft':False,'prerelease':False,
                 'assets':[{'id':101,'name':'fresh-1.0.0-source.tar.gz'},{'id':102,'name':'fonts.zip'}]}
        payload=self.root/'releases.json';payload.write_text(json.dumps([release]))
        watcher={'kind':'release','repository':'fixture/releases','source_id':'primary',
                 'related_source_ids':['related','license-archive'],
                 'asset_templates':{'primary':'fresh-{version}-source.tar.gz','related':'fonts.zip'}}
        git=sources.git;fetch=sources.fetch
        def fixture_git(*args,**kwargs):
            args=tuple(self.url if value=='https://github.com/fixture/releases.git' else value for value in args)
            return git(*args,**kwargs)
        def fixture_fetch(url,destination=None):
            self.assertEqual(url,'https://api.github.com/repos/fixture/releases/releases?per_page=100')
            return fetch(payload.as_uri(),destination)
        with patch.object(sources,'public_url',lambda value:value),patch.object(sources,'git',fixture_git),patch.object(sources,'fetch',fixture_fetch):
            selected=sources.discover_release_tag(watcher,{})
            self.assertEqual((selected['version'],selected['release_id']),('1.0.0',42))
            accepted={'tag':'v1.0.0','tag_object':self.head,'release_id':42,'asset_id':101}
            self.assertIsNone(sources.discover_release_tag(watcher,accepted))
            replaced=json.loads(json.dumps(release));replaced['assets'][0]['id']=999
            payload.write_text(json.dumps([replaced]))
            with self.assertRaisesRegex(ValueError,'replaced or missing'):
                sources.discover_release_tag(watcher,accepted)
            for assets in ([release['assets'][0]],release['assets']+[release['assets'][1]]):
                payload.write_text(json.dumps([{**release,'assets':assets}]))
                with self.assertRaisesRegex(ValueError,'missing or duplicated'):
                    sources.discover_release_tag(watcher,{})
            payload.write_text(json.dumps([release]))
            same={**watcher,'asset_templates':{'primary':'fonts.zip','related':'fonts.zip'}}
            with self.assertRaisesRegex(ValueError,'share an enrolled asset'):
                sources.discover_release_tag(same,{})

    def test_public_sources_reject_credentials_and_localhost(self):
        for url in ['http://github.com/a/b','https://user:secret@github.com/a/b','https://localhost/repo.git']:
            with self.subTest(url=url),self.assertRaises(ValueError):sources.public_url(url)


if __name__=='__main__':unittest.main()
