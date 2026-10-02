import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import sources


class SourceContracts(unittest.TestCase):
    def setUp(self):
        root=Path.home()/'.local/state/omp/work/arch-package-tests'
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

    def test_full_ref_context_changes_even_with_same_commit(self):
        frozen=self.freeze(self.root/'first')
        sources.git('tag','v1.0.1',cwd=self.repo)
        with patch.object(sources,'public_url',lambda value:value):
            transition=sources.discover_git({'url':self.url,'ref':'refs/heads/integration','work_dir':str(self.root/'discovery')},frozen)
        self.assertEqual(transition['commit'],self.head)
        self.assertNotEqual(transition['refs_digest'],frozen['bundle']['refs_digest'])

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

    def test_bundle_tamper_and_manifest_tamper_fail(self):
        frozen=self.freeze(self.root/'first');frozen['bundle']['url']='https://github.com/vith/arch-packages/releases/download/source-x/x.bundle'
        lock={'schema':1,'version':'1.0.0-1','sources':[frozen]}
        def malicious(url,destination=None):
            Path(destination).write_bytes(b'tampered')
            return b'tampered'
        with patch.object(sources,'public_url',lambda value:value),patch.object(sources,'fetch',malicious),self.assertRaisesRegex(ValueError,'tampered'):
            sources.materialize_sources(lock,self.root/'materialized')
        frozen['bundle']['refs'][0]['object']='f'*40
        with patch.object(sources,'public_url',lambda value:value),self.assertRaisesRegex(ValueError,'manifest'):
            sources.validate_lock(lock)

    def test_bundle_materializes_exact_readonly_mirror(self):
        frozen=self.freeze(self.root/'first');asset=self.root/'first/source.bundle'
        frozen['bundle']['url']='https://github.com/vith/arch-packages/releases/download/source-x/x.bundle'
        def local(url,destination=None):
            data=asset.read_bytes()
            if destination:Path(destination).write_bytes(data)
            return data
        with patch.object(sources,'public_url',lambda value:value),patch.object(sources,'fetch',local):
            mapping=sources.materialize_sources({'schema':1,'version':'1.0.0-1','sources':[frozen]},self.root/'materialized')
        mirror=mapping[self.url]
        self.assertEqual(sources.git('rev-parse','refs/heads/integration',cwd=mirror),self.head)
        self.assertFalse(mirror.stat().st_mode&0o222)
        # TemporaryDirectory needs writable directories for cleanup.
        for path in mirror.rglob('*'):
            if path.is_dir():path.chmod(0o755)
        mirror.chmod(0o755)

    def test_public_sources_reject_credentials_and_legacy_origins(self):
        for url in ['http://github.com/a/b','https://user:secret@github.com/a/b','https://git.n3t.work/vith/oh-my-pi.git']:
            with self.subTest(url=url),self.assertRaises(ValueError):sources.public_url(url)


if __name__=='__main__':unittest.main()
