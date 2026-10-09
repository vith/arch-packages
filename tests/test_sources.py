import hashlib
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

    def test_public_sources_reject_credentials_and_localhost(self):
        for url in ['http://github.com/a/b','https://user:secret@github.com/a/b','https://localhost/repo.git']:
            with self.subTest(url=url),self.assertRaises(ValueError):sources.public_url(url)


if __name__=='__main__':unittest.main()
