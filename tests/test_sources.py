import hashlib
from pathlib import Path
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

    def test_public_sources_reject_credentials_and_localhost(self):
        for url in ['http://github.com/a/b','https://user:secret@github.com/a/b','https://localhost/repo.git']:
            with self.subTest(url=url),self.assertRaises(ValueError):sources.public_url(url)


if __name__=='__main__':unittest.main()
