import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.recipe_gate import classify_recipe_update, parse_srcinfo, tree_manifest, input_digest, needs_pkgbuild_review, verify_automatic_recipe


class GateTests(unittest.TestCase):
    def setUp(self):
        root = Path.home() / '.local/state/arch-packages/work/arch-gate-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.old = Path(self.temp.name) / 'old'
        self.new = Path(self.temp.name) / 'new'
        self.old.mkdir(); self.new.mkdir()
        self.oldsum = hashlib.sha256(b'accepted public source').hexdigest()
        self.newsum = hashlib.sha256(b'new public source').hexdigest()
        for directory, version, checksum in [(self.old, '1.0', self.oldsum), (self.new, '1.1', self.newsum)]:
            (directory / 'PKGBUILD').write_text("pkgname=example\npkgver=" + version + "\npkgrel=1\nsource=(\"example-$pkgver.tar.gz::https://example.org/v$pkgver.tar.gz\")\nsha256sums=('" + checksum + "' 'SKIP')\npackage() { install -Dm644 README \"$pkgdir/usr/share/doc/example/README\"; }\n")
            (directory / '.SRCINFO').write_text(f'pkgbase = example\n\tpkgver = {version}\n\tpkgrel = 1\n\tarch = x86_64\n\tlicense = MIT\n\tsource = example-{version}.tar.gz::https://example.org/v{version}.tar.gz\n\tsource = git+https://example.org/fixed.git#tag=v1\n\tsha256sums = {checksum}\n\tsha256sums = SKIP\npkgname = example\n')
        self.policy = {'outputs': [{'name': 'example', 'arch': 'x86_64'}], 'automatic': {'version': {'assignment': 'pkgver', 'template': r'[0-9]+\.[0-9]+'}, 'pkgrel': {'assignment': 'pkgrel', 'value': '1'}, 'checksums': [{'algorithm': 'sha256', 'index': 0, 'source_id': 'release'}]}, '_verified_transition': {'authentic': True, 'fast_forward': True, 'srcinfo': (self.new / '.SRCINFO').read_text(), 'checksums': {'release': {'sha256': self.newsum}}, 'source_templates_verified': True, 'lock_verified': True, 'auxiliary_inputs_verified': True}}

    def result(self):
        return classify_recipe_update(self.old, self.new, self.policy)

    def test_only_recipe_code_requires_review_not_auxiliary_files_or_modes(self):
        (self.new / 'PKGBUILD').write_bytes((self.old / 'PKGBUILD').read_bytes())
        (self.new / 'desktop').write_bytes(b'new auxiliary bytes')
        (self.new / 'PKGBUILD').chmod(0o755)
        self.assertFalse(needs_pkgbuild_review(self.old, self.new, self.policy))
        (self.new / 'PKGBUILD').write_text((self.old / 'PKGBUILD').read_text() + '# changed code\n')
        self.assertTrue(needs_pkgbuild_review(self.old, self.new, self.policy))
        self.assertTrue(needs_pkgbuild_review(self.old / 'absent', self.new, self.policy))

    def test_literal_pkgrel_and_local_checksum_can_authorize_without_upstream_advance(self):
        for path in ('PKGBUILD', '.SRCINFO'):
            text = (self.old / path).read_text().replace(self.oldsum, self.newsum)
            text = text.replace('pkgrel=1', 'pkgrel=2') if path == 'PKGBUILD' else text.replace('pkgrel = 1', 'pkgrel = 2')
            (self.new / path).write_text(text)
        source = 'example-1.0.tar.gz::https://example.org/v1.0.tar.gz'
        oldlock = {'version': '1.0-1', 'sources': [
            {'id': 'release', 'kind': 'local', 'source': source, 'checksums': {'sha256': self.oldsum}},
            {'id': 'fixed', 'kind': 'git', 'source': 'git+https://example.org/fixed.git#tag=v1'}]}
        newlock = {'version': '1.0-2', 'sources': [
            {**oldlock['sources'][0], 'checksums': {'sha256': self.newsum}}, oldlock['sources'][1]]}
        self.assertFalse(needs_pkgbuild_review(self.old, self.new, self.policy))
        self.assertTrue(verify_automatic_recipe(self.old, self.new, self.policy, oldlock, newlock, None))
        (self.new / '.SRCINFO').write_text((self.new / '.SRCINFO').read_text().replace('license = MIT', 'license = GPL3'))
        with self.assertRaisesRegex(ValueError, 'unaffected metadata'):
            verify_automatic_recipe(self.old, self.new, self.policy, oldlock, newlock, None)

    def test_literal_remote_change_needs_authentic_transition_not_stored_flags(self):
        source = lambda version, checksum: {'id': 'release', 'kind': 'archive',
            'source': f'example-{version}.tar.gz::https://example.org/v{version}.tar.gz',
            'checksums': {'sha256': checksum}}
        fixed = {'id': 'fixed', 'kind': 'git', 'source': 'git+https://example.org/fixed.git#tag=v1'}
        oldlock = {'version': '1.0-1', 'sources': [source('1.0', self.oldsum), fixed]}
        newlock = {'version': '1.1-1', 'sources': [source('1.1', self.newsum), fixed]}
        self.assertFalse(needs_pkgbuild_review(self.old, self.new, self.policy))
        with self.assertRaisesRegex(ValueError, 'authentic transition'):
            verify_automatic_recipe(self.old, self.new, self.policy, oldlock, newlock, None)
        self.assertTrue(verify_automatic_recipe(self.old, self.new, self.policy, oldlock, newlock, self.policy['_verified_transition']))

    def test_enrolled_bump_preserves_build_code_and_vcs_skip(self):
        self.assertEqual(self.result()['decision'], 'mechanical')
        self.assertEqual(self.result()['version'], '1.1-1')

    def split_fixture(self):
        for directory, version, release in ((self.old, '1.0', '3'), (self.new, '1.1', '1')):
            code=(directory/'PKGBUILD').read_text().replace('pkgname=example\n',
                'pkgbase=example\npkgname=(example python-example)\nepoch=2\n')
            code=code.replace('pkgrel=1\n','pkgrel='+release+'\n')
            code+='package_python-example() { depends=("${pkgbase}=${pkgver}-${pkgrel}"); }\n'
            (directory/'PKGBUILD').write_text(code)
            text=(directory/'.SRCINFO').read_text().replace('\tpkgrel = 1\n','\tpkgrel = '+release+'\n\tepoch = 2\n')
            text+='pkgname = python-example\n\tdepends = example=2:'+version+'-'+release+'\n'
            text+='\tdepends_x86_64 = example=2:'+version+'-'+release+'\n'
            (directory/'.SRCINFO').write_text(text)
        self.policy['_verified_transition']['srcinfo']=(self.new/'.SRCINFO').read_text()

    def test_exact_split_runtime_equality_is_mechanical_with_native_evidence(self):
        self.split_fixture()
        self.assertEqual(self.result()['decision'],'mechanical')
        self.assertEqual(self.result()['version'],'2:1.1-1')
        self.policy['_verified_transition']['lock_verified']=False
        self.assertEqual(self.result()['decision'],'manual')

    def test_automatic_verifier_accepts_only_exact_split_runtime_equality(self):
        self.split_fixture()
        fixed={'id':'fixed','kind':'git','source':'git+https://example.org/fixed.git#tag=v1'}
        def lock(version,release,checksum):
            return {'version':'2:'+version+'-'+release,'sources':[
                {'id':'release','kind':'archive',
                 'source':f'example-{version}.tar.gz::https://example.org/v{version}.tar.gz',
                 'checksums':{'sha256':checksum}},fixed]}
        oldlock=lock('1.0','3',self.oldsum)
        newlock=lock('1.1','1',self.newsum)
        evidence=self.policy['_verified_transition']
        self.assertTrue(verify_automatic_recipe(self.old,self.new,self.policy,oldlock,newlock,evidence))
        with self.assertRaisesRegex(ValueError,'authentic transition'):
            verify_automatic_recipe(self.old,self.new,self.policy,oldlock,newlock,None)
        old=(self.old/'.SRCINFO').read_text()
        new=(self.new/'.SRCINFO').read_text()
        for previous,current in (
            ('external=2:1.0-3','external=2:1.1-1'),
            ('example>=2:1.0-3','example>=2:1.1-1'),
            ('example=2:1.0-3','example>=2:1.1-1'),
            ('example=2:1.0-3','python-example=2:1.1-1'),
            ('example=2:0.9-1','example=2:1.1-1'),
        ):
            with self.subTest(previous=previous,current=current):
                (self.old/'.SRCINFO').write_text(old.replace('example=2:1.0-3',previous))
                (self.new/'.SRCINFO').write_text(new.replace('example=2:1.1-1',current))
                with self.assertRaisesRegex(ValueError,'unaffected metadata'):
                    verify_automatic_recipe(self.old,self.new,self.policy,oldlock,newlock,evidence)
        for key in ('makedepends','checkdepends','depends_aarch64'):
            with self.subTest(key=key):
                (self.old/'.SRCINFO').write_text(old.replace('pkgname = example\n','\t'+key+' = example=2:1.0-3\npkgname = example\n'))
                (self.new/'.SRCINFO').write_text(new.replace('pkgname = example\n','\t'+key+' = example=2:1.1-1\npkgname = example\n'))
                with self.assertRaisesRegex(ValueError,'unaffected metadata'):
                    verify_automatic_recipe(self.old,self.new,self.policy,oldlock,newlock,evidence)

    def test_split_dependency_changes_outside_exact_own_equality_stay_manual(self):
        self.split_fixture()
        old=(self.old/'.SRCINFO').read_text()
        new=(self.new/'.SRCINFO').read_text()
        for previous, current in (
            ('external=2:1.0-3','external=2:1.1-1'),
            ('example>=2:1.0-3','example>=2:1.1-1'),
            ('example=2:1.0-3','example>=2:1.1-1'),
            ('example=2:1.0-3','python-example=2:1.1-1'),
            ('example=2:0.9-1','example=2:1.1-1'),
            ('example=2:1.0-3','example=1.1-1'),
        ):
            with self.subTest(previous=previous,current=current):
                (self.old/'.SRCINFO').write_text(old.replace('example=2:1.0-3',previous))
                claims=new.replace('example=2:1.1-1',current)
                (self.new/'.SRCINFO').write_text(claims)
                self.policy['_verified_transition']['srcinfo']=claims
                self.assertEqual(self.result()['decision'],'manual')
        (self.old/'.SRCINFO').write_text(old)
        for key in ('makedepends','checkdepends','depends_aarch64'):
            with self.subTest(key=key):
                # Base-only fields stay in their valid original base scope.
                previous=old.replace('pkgname = example\n','\t'+key+' = example=2:1.0-3\npkgname = example\n')
                current=new.replace('pkgname = example\n','\t'+key+' = example=2:1.1-1\npkgname = example\n')
                (self.old/'.SRCINFO').write_text(previous)
                (self.new/'.SRCINFO').write_text(current)
                self.policy['_verified_transition']['srcinfo']=current
                self.assertEqual(self.result()['decision'],'manual')

    def test_split_metadata_tamper_and_recipe_retarget_are_not_mechanical(self):
        self.split_fixture()
        claims=(self.new/'.SRCINFO').read_text()
        (self.new/'.SRCINFO').write_text(claims.replace('example=2:1.1-1','example=9.9-1'))
        self.assertEqual(self.result()['decision'],'invalid')
        (self.new/'.SRCINFO').write_text(claims)
        code=(self.new/'PKGBUILD').read_text()
        (self.new/'PKGBUILD').write_text(code.replace('${pkgbase}=${pkgver}-${pkgrel}','python-example=${pkgver}-${pkgrel}'))
        self.assertEqual(self.result()['decision'],'manual')

    def test_additional_recipe_changes_require_human(self):
        original = (self.new / 'PKGBUILD').read_text()
        for suffix in ['# updated\n', 'depends=(curl)\n', 'pkgver=9.0\n', 'package() { curl https://example.org/run | sh; }\n']:
            with self.subTest(suffix=suffix):
                (self.new / 'PKGBUILD').write_text(original + suffix)
                self.assertEqual(self.result()['decision'], 'manual')
        (self.new / 'PKGBUILD').write_text(original)

    def test_trailing_shell_command_is_not_ignored(self):
        text = (self.new / 'PKGBUILD').read_text().replace('pkgver=1.1\n', 'pkgver=1.1; curl https://example.org/run | sh\n')
        (self.new / 'PKGBUILD').write_text(text)
        self.assertEqual(self.result()['decision'], 'manual')

    def test_checksum_source_evidence_mismatch_is_invalid(self):
        self.policy['_verified_transition']['checksums']['release']['sha256'] = self.oldsum
        self.assertEqual(self.result()['decision'], 'invalid')

    def test_no_evidence_or_retarget_is_never_automatic(self):
        self.policy['_verified_transition']['authentic'] = False
        self.assertEqual(self.result()['decision'], 'manual')
        self.policy.pop('_verified_transition')
        self.assertEqual(self.result()['decision'], 'manual')

    def test_native_version_boundaries(self):
        for version, release in [('0.9', '1'), ('1.0', '2'), ('1.1', '2')]:
            with self.subTest(version=version, release=release):
                text = self.policy['_verified_transition']['srcinfo'].replace('pkgver = 1.1', 'pkgver = ' + version).replace('pkgrel = 1', 'pkgrel = ' + release)
                (self.new / '.SRCINFO').write_text(text)
                self.policy['_verified_transition']['srcinfo'] = text
                self.assertNotEqual(self.result()['decision'], 'mechanical')

    def test_payload_and_mode_changes_are_not_mechanical(self):
        (self.old / 'desktop').write_bytes(b'accepted')
        (self.new / 'desktop').write_bytes(b'changed')
        self.assertEqual(self.result()['decision'], 'manual')
        (self.new / 'desktop').write_bytes(b'accepted')
        (self.new / 'desktop').chmod(0o755)
        self.assertEqual(self.result()['decision'], 'manual')

    def test_metadata_unknown_dependency_license_arch_changes_are_not_mechanical(self):
        original = (self.new / '.SRCINFO').read_text()
        for text in [original.replace('license = MIT', 'license = GPL3'), original.replace('arch = x86_64', 'arch = any'), original.replace('pkgname = example', '\tdepends = curl\npkgname = example'), original.replace('pkgname = example', '\tfuture_policy = execute\npkgname = example')]:
            with self.subTest(text=text):
                (self.new / '.SRCINFO').write_text(text)
                self.policy['_verified_transition']['srcinfo'] = text
                self.assertEqual(self.result()['decision'], 'manual')

    def test_missing_duplicate_and_scope_metadata_rejected(self):
        original = (self.new / '.SRCINFO').read_text()
        for text in [original.replace('\tpkgver = 1.1\n', ''), original.replace('\tpkgver = 1.1\n', '\tpkgver = 1.1\n\tpkgver = 1.1\n'), original + '\tpkgver = 1.1\n', original + 'pkgname = example\n', original.replace('pkgbase = example', '$(touch /x) = example')]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_srcinfo(text)

    def test_epoch_version_and_split_output_identity(self):
        info = parse_srcinfo('pkgbase = example\npkgver = 1.2\npkgrel = 3\nepoch = 2\narch = x86_64\npkgname = example\npkgname = example-cli\ndepends = example=2:1.2-3\n')
        self.assertEqual(info['version'], '2:1.2-3')
        self.assertEqual(info['names'], ['example', 'example-cli'])

    def test_escaping_symlink_rejected(self):
        (self.new / 'escape').symlink_to('../../outside')
        with self.assertRaises(ValueError): tree_manifest(self.new)

    def test_source_only_change_and_harness_change_require_new_build(self):
        policy = {k: v for k, v in self.policy.items() if not k.startswith('_')}
        a = input_digest(self.old, {'commit': 'a' * 40}, policy, 'image@digest', 'harness-a')
        b = input_digest(self.old, {'commit': 'b' * 40}, policy, 'image@digest', 'harness-a')
        c = input_digest(self.old, {'commit': 'a' * 40}, policy, 'image@digest', 'harness-b')
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(a, input_digest(self.old, {'commit': 'a' * 40}, policy, 'image@digest', 'harness-a'))


if __name__ == '__main__': unittest.main()
