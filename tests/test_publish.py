import base64
from contextlib import ExitStack
import os
import re
import copy
import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from tools import publish


class HTTPFixture:
    def __init__(self):
        self.assets = {}
        self.draft = True
        self.omit = None
        self.version = 'old-version'
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path == '/assets':
                    self.respond([{'name':name} for name in owner.assets if name != owner.omit])
                else:
                    name = self.path.rsplit('/',1)[-1]
                    if owner.draft or name not in owner.assets:
                        self.send_error(404); return
                    body = owner.assets[name]
                    self.send_response(200); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length','0')))
                if self.path == '/release':
                    owner.draft = True; self.respond({'id':1})
                else:
                    name = self.path.rsplit('/',1)[-1]
                    owner.assets[name] = body; self.respond({'name':name})
            def do_PATCH(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.draft = payload.get('draft', owner.draft)
                if not owner.draft and payload.get('make_latest') != 'false':
                    owner.version = 'new-version'
                self.respond({'id':1})
            def respond(self, value):
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(value).encode())
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
    def request(self, path, method='GET', data=None):
        request = urllib.request.Request(self.base+path, data=data, method=method)
        with urllib.request.urlopen(request,timeout=5) as response: return json.load(response)
    def api(self, path, method='GET', data=None):
        if '/git/ref/tags/' in path:
            return {'object':{'type':'commit','sha':'a'*40}}
        return self.request('/release',method,json.dumps(data).encode() if data else None)
    def upload(self, repository, release_id, path):
        return self.request('/upload/'+path.name,'POST',path.read_bytes())
    def download(self, url, path, maximum=publish.MAXIMUM):
        with urllib.request.urlopen(self.base+'/public/'+url.rsplit('/',1)[-1],timeout=5) as response:
            data = response.read(maximum+1)
        if len(data)>maximum: raise ValueError('fixture download exceeds bound')
        Path(path).write_bytes(data); return Path(path)
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()


class PublicationBoundaries(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / '.local/state/arch-packages/work'
        scratch.mkdir(parents=True, exist_ok=True)
        self.work = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.work.name)
        self.http = HTTPFixture()
    def tearDown(self):
        self.http.close(); self.work.cleanup()
    def upload(self, names):
        with patch.object(publish.github_api,'api',self.http.api), patch.object(publish.github_api,'upload_asset',self.http.upload), patch.object(publish.github_api,'pages',lambda path:iter(self.http.request('/assets'))):
            return publish.upload_release('snapshot-fixture','a'*40,self.root,names)
    def test_partial_upload_stays_private(self):
        for name in ('package','signature'): (self.root/name).write_bytes(name.encode())
        self.http.omit = 'signature'
        with self.assertRaisesRegex(ValueError,'partial/extra'):
            self.upload({'package','signature'})
        self.assertTrue(self.http.draft)
        self.assertEqual(self.http.version,'old-version')
    def test_complete_uploaded_release_is_public_but_not_latest(self):
        for name in ('package','signature'):
            (self.root/name).write_bytes(name.encode())
        self.upload({'package','signature'})
        self.assertFalse(self.http.draft)
        self.assertEqual(self.http.version,'old-version')
        self.assertEqual(self.http.assets,{'package':b'package','signature':b'signature'})

    def readback_fixture(self):
        assets = self.root/'assets'; assets.mkdir()
        bodies = {'example-1-1-any.pkg.tar.zst':b'package bytes','example-1-1-any.pkg.tar.zst.sig':b'signature bytes','catalog.json':b'catalog','catalog.json.sig':b'catalog signature'}
        for name, body in bodies.items(): (assets/name).write_bytes(body)
        self.http.assets.update(bodies); self.http.draft=False
        catalog = {'snapshot':'snapshot-fixture','files':{n:{'url':publish.release_url(publish.REPOSITORY,'snapshot-fixture',n),'sha256':hashlib.sha256(b).hexdigest()} for n,b in bodies.items() if not n.startswith('catalog.')},'source_assets':{}}
        return assets,catalog
    def test_anonymous_readback_hash_failure_prevents_activation(self):
        assets,catalog=self.readback_fixture()
        self.http.assets['example-1-1-any.pkg.tar.zst']=b'tampered package'
        with patch.object(publish.github_api,'download',self.http.download), self.assertRaisesRegex(ValueError,'hash mismatch'):
            publish.public_readback(catalog,assets,self.root/'readback',self.root/'ring')
        self.assertEqual(self.http.version,'old-version')
    def test_signature_failure_prevents_activation_even_with_matching_hash(self):
        assets,catalog=self.readback_fixture()
        # Hashes alone cannot establish authenticity: cryptographic verifier must pass.
        with patch.object(publish.github_api,'download',self.http.download), patch.object(publish,'verify',side_effect=ValueError('invalid detached signature')), self.assertRaisesRegex(ValueError,'invalid detached'):
            publish.public_readback(catalog,assets,self.root/'readback',self.root/'ring')
        self.assertEqual(self.http.version,'old-version')
    def test_activation_lost_response_reconciles_without_rollback(self):
        old = {'id':1,'tag':'snapshot-old'}
        target = {'id':2,'tag':'snapshot-new'}
        catalog = {'snapshot':'snapshot-new'}
        state = [old]
        def lost_response(*args, **kwargs):
            state[0] = target
            raise TimeoutError('response lost after commit')
        with patch.object(publish,'latest_release',side_effect=lambda:state[0]), patch.object(publish.github_api,'api',side_effect=lost_response) as api, patch.object(publish,'validate_catalog',return_value=catalog), patch.object(publish,'latest_catalog',return_value=catalog):
            publish.promote(target,old,catalog,self.root/'ring',self.root/'observed')
        self.assertEqual(state[0],target)
        self.assertEqual(api.call_count,1)

    def test_failed_activation_never_rolls_back_unrelated_latest(self):
        old = {'id':1,'tag':'snapshot-old'}
        target = {'id':2,'tag':'snapshot-new'}
        unrelated = {'id':3,'tag':'snapshot-unrelated'}
        with patch.object(publish,'latest_release',side_effect=[old,unrelated]), patch.object(publish.github_api,'api',side_effect=TimeoutError('lost')) as api, self.assertRaisesRegex(RuntimeError,'ambiguous'):
            publish.promote(target,old,{},self.root/'ring',self.root/'observed')
        self.assertEqual(api.call_count,1)

    def test_promotion_waits_for_public_pointer_propagation(self):
        old = {'id': 1, 'tag': 'snapshot-old'}
        target = {'id': 2, 'tag': 'snapshot-new'}
        catalog = {'snapshot': 'snapshot-new'}
        with patch.object(publish, 'latest_release', side_effect=[old, old, target, target]), patch.object(publish.github_api, 'api'), patch.object(publish, 'validate_catalog', return_value=catalog), patch.object(publish, 'latest_catalog', return_value=catalog), patch.object(publish.time, 'sleep') as sleep:
            publish.promote(target, old, catalog, self.root / 'ring', self.root / 'observed')
        sleep.assert_called_once_with(5)

    def test_changed_latest_prevents_any_promotion(self):
        with patch.object(publish,'latest_release',return_value={'id':3,'tag':'snapshot-unrelated'}), patch.object(publish.github_api,'api') as api, self.assertRaisesRegex(ValueError,'changed'):
            publish.promote({'id':2,'tag':'snapshot-new'},{'id':1,'tag':'snapshot-old'},{},self.root/'ring',self.root/'observed')
        api.assert_not_called()

    def test_rollback_rejects_invalid_retained_signatures_before_promotion(self):
        from types import SimpleNamespace
        old = {'id':1,'tag':'snapshot-old'}
        target = {'id':2,'tag':'snapshot-target'}
        active = {'retained_snapshots':{'snapshot-target':{'files':{}}}}
        args = SimpleNamespace(work_dir=self.root/'rollback',expected_release_id=1,expected_tag='snapshot-old',target_tag='snapshot-target')
        with patch.object(publish,'current_main'), patch.object(publish,'run',return_value=b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'), patch.object(publish,'keyring',return_value=self.root/'ring'), patch.object(publish,'latest_release',return_value=old), patch.object(publish,'verified_snapshot',side_effect=[active,ValueError('invalid signature')]), patch.object(publish.github_api,'api',return_value={'id':2,'tag_name':target['tag'],'draft':False,'prerelease':False}), patch.object(publish,'promote') as promote, self.assertRaisesRegex(ValueError,'invalid signature'):
            publish.rollback(args)
        promote.assert_not_called()

    def test_rollback_promotes_only_verified_matching_retained_catalog(self):
        from types import SimpleNamespace
        old = {'id':1,'tag':'snapshot-old'}
        target = {'id':2,'tag':'snapshot-target'}
        files = {'package':{'sha256':'a'*64}}
        catalog = {'files':files}
        active = {'retained_snapshots':{'snapshot-target':{'files':files}}}
        args = SimpleNamespace(work_dir=self.root/'rollback',expected_release_id=1,expected_tag='snapshot-old',target_tag='snapshot-target')
        with patch.object(publish,'current_main'), patch.object(publish,'run',return_value=b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'), patch.object(publish,'keyring',return_value=self.root/'ring'), patch.object(publish,'latest_release',return_value=old), patch.object(publish,'verified_snapshot',side_effect=[active,catalog]), patch.object(publish.github_api,'api',return_value={'id':2,'tag_name':target['tag'],'draft':False,'prerelease':False}), patch.object(publish,'promote') as promote:
            result = publish.rollback(args)
        self.assertEqual(result['activated_release'],target)
        self.assertEqual(result['previous_release'],old)
        self.assertEqual(promote.call_args.args[:3],(target,old,catalog))

    def test_observed_signed_latest_catalog_mismatch_fails_without_rollback(self):
        old = {'id':1,'tag':'snapshot-old'}
        target = {'id':2,'tag':'snapshot-new'}
        catalog = {'snapshot':'snapshot-new'}
        with patch.object(publish,'latest_release',side_effect=[old,target,target]), patch.object(publish.github_api,'api') as api, patch.object(publish,'validate_catalog',return_value=catalog), patch.object(publish,'latest_catalog',return_value={'snapshot':'snapshot-other'}), self.assertRaisesRegex(ValueError,'active catalog'):
            publish.promote(target,old,catalog,self.root/'ring',self.root/'observed')
        self.assertEqual(api.call_count,1)

    def catalog_fixture(self, repository, snapshot='snapshot-fixture'):
        databases = {'arch-packages.asc'}
        databases.update(repository + suffix for suffix in (
            '.db', '.db.sig', '.db.tar.gz', '.db.tar.gz.sig',
            '.files', '.files.sig', '.files.tar.gz', '.files.tar.gz.sig'))
        files = {name:{'url':publish.release_url(publish.REPOSITORY,snapshot,name),'sha256':'a'*64} for name in databases}
        for name in ('example-1-1-any.pkg.tar.zst','example-1-1-any.pkg.tar.zst.sig'):
            files[name] = {'url':publish.release_url(publish.REPOSITORY,snapshot,name),'sha256':'b'*64}
        return {'schema':1,'repository':publish.REPOSITORY,'snapshot':snapshot,'accepted_sha':'a'*40,'key_fingerprint':'A'*40,'recipes':{'example':{'recipe_commit':'e'*40}},'files':files,'source_assets':{},'retained_packages':{},'retained_snapshots':{}}

    def test_complete_catalog_requires_exact_pacman_aliases_and_canonical_archives(self):
        for repository in ('vith-gh', 'arch-packages'):
            catalog = self.catalog_fixture(repository)
            publish.validate_catalog(catalog,publish.REPOSITORY,'A'*40)
            for name in catalog['files']:
                if '.pkg.tar.' in name:
                    continue
                broken = copy.deepcopy(catalog)
                del broken['files'][name]
                with self.subTest(repository=repository,asset=name), self.assertRaises(ValueError):
                    publish.validate_catalog(broken,publish.REPOSITORY,'A'*40)

    def test_renamed_catalog_retains_immutable_historical_database_urls(self):
        catalog = self.catalog_fixture('vith-gh')
        old = self.catalog_fixture('arch-packages','snapshot-old')
        catalog['retained_snapshots']['snapshot-old'] = {'files':old['files']}
        publish.validate_catalog(catalog,publish.REPOSITORY,'A'*40)
        entry = catalog['retained_snapshots']['snapshot-old']['files']['arch-packages.db']
        entry['url'] = publish.release_url(publish.REPOSITORY,'snapshot-fixture','arch-packages.db')
        with self.assertRaisesRegex(ValueError,'Wrong asset snapshot'):
            publish.validate_catalog(catalog,publish.REPOSITORY,'A'*40)

    def test_catalog_rejects_mixed_repository_names_and_database_retargeting(self):
        catalog = self.catalog_fixture('vith-gh')
        old = self.catalog_fixture('arch-packages')
        catalog['files'].update(old['files'])
        with self.assertRaises(ValueError):
            publish.validate_catalog(catalog,publish.REPOSITORY,'A'*40)
        catalog = self.catalog_fixture('vith-gh')
        catalog['files']['vith-gh.db']['url'] = publish.release_url(publish.REPOSITORY,'snapshot-old','vith-gh.db')
        with self.assertRaisesRegex(ValueError,'Wrong asset snapshot'):
            publish.validate_catalog(catalog,publish.REPOSITORY,'A'*40)

    def test_stale_main_is_rejected_before_checkout_or_activation(self):
        with patch.object(publish.github_api,'api',return_value={'object':{'sha':'b'*40}}), self.assertRaisesRegex(ValueError,'stale'):
            publish.current_main('a'*40)
        self.assertEqual(self.http.version,'old-version')
    def test_old_filename_cannot_receive_different_bytes(self):
        for previous in ({'files':{'example.pkg.tar.zst':{'sha256':'a'*64}},'retained_packages':{}}, {'files':{},'retained_packages':{'example.pkg.tar.zst':{'sha256':'a'*64}},'retained_snapshots':{'snapshot-older':{'files':{'example.pkg.tar.zst':{'sha256':'a'*64}}}}}):
            for digest,reused in [('b'*64,True),('a'*64,False)]:
                with self.subTest(previous=previous,digest=digest,reused=reused), self.assertRaisesRegex(ValueError,'collision'):
                    publish.check_collisions(previous,'example.pkg.tar.zst',digest,reused)
            publish.check_collisions(previous,'example.pkg.tar.zst','a'*64,True)
    def test_epoch_preserved_in_metadata_not_filename(self):
        package={'lock':{'version':'2:1.4-3'},'policy':{'outputs':[{'name':'example','arch':'any'}]}}
        self.assertEqual(publish.filenames(package),{'example-1.4-3-any.pkg.tar.zst':{'pkgname':'example','pkgver':'2:1.4-3','arch':'any'}})
    def test_original_archive_rejects_traversal_links_and_duplicates(self):
        for kind in ('traversal', 'symlink', 'duplicate'):
            path = self.root / (kind + '.tar')
            with tarfile.open(path, 'w') as archive:
                member = tarfile.TarInfo('../escape' if kind == 'traversal' else 'package')
                if kind == 'symlink':
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                archive.addfile(member)
                if kind == 'duplicate':
                    archive.addfile(member)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'unsafe'):
                publish.safe_extract(path, self.root / kind)

    def test_original_archive_member_bound_precedes_extraction(self):
        archive_path = self.root / 'many-members.tar'
        with tarfile.open(archive_path, 'w') as archive:
            for index in range(1001):
                archive.addfile(tarfile.TarInfo('member-' + str(index)))
        destination = self.root / 'bounded-output'
        with self.assertRaisesRegex(ValueError, 'too many'):
            publish.safe_extract(archive_path, destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_native_archive_with_empty_optional_metadata_is_readable(self):
        path = self.root / 'example-1:2.0-3-any.pkg.tar.zst'
        data = (b'pkgname = example\npkgver = 1:2.0-3\narch = any\n'
                b'url = \npkgdesc = \n')
        with tarfile.open(path, 'w') as archive:
            member = tarfile.TarInfo('.PKGINFO')
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        self.assertEqual(publish.pkginfo(path),
                         {'pkgname': 'example', 'pkgver': '1:2.0-3', 'arch': 'any'})

    def test_shell_shaped_and_duplicate_pkginfo_keys_rejected(self):
        for content in (b'$(touch owned) = value\n',b'pkgname = x\npkgname = y\npkgver = 1-1\narch = any\n'):
            with self.subTest(content=content), patch.object(publish,'bounded_metadata',return_value=content), self.assertRaises(ValueError):
                publish.pkginfo(self.root/'unused')
    def test_full_input_digest_reuses_signed_content_but_not_changed_sources(self):
        checkout = self.root / 'checkout'; checkout.mkdir()
        (checkout / 'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:' + 'a'*64)
        policies = []
        for index in range(5):
            name = 'example' + str(index)
            recipe = checkout / 'recipes' / name; recipe.mkdir(parents=True)
            (recipe / 'PKGBUILD').write_text('pkgname=' + name + '\n')
            (recipe / '.SRCINFO').write_text('pkgbase = ' + name + '\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = any\npkgname = ' + name + '\n')
            (checkout / 'inputs').mkdir(exist_ok=True)
            (checkout / 'inputs' / (name+'.json')).write_text(json.dumps({'schema':1,'version':'1-1','sources':[]}))
            (checkout / 'upstream').mkdir(exist_ok=True)
            (checkout / 'upstream' / (name+'.json')).write_text(json.dumps({'aur':None}))
            policies.append({'pkgbase':name,'outputs':[{'name':name,'arch':'any'}]})
        (checkout / 'packages.json').write_text(json.dumps({'schema':1,'packages':policies}))
        pins = {p['pkgbase']:'e'*40 for p in policies}
        with patch.object(publish,'ROOT',checkout), patch.object(publish,'harness_digest',return_value='b'*64):
            first = publish.expectations('a'*40,1,1,None,pins)
            self.assertEqual({p['pkgbase'] for p in first['packages'] if not p['reuse']},{'example'+str(i) for i in range(5)})
            previous = {'recipes':{p['pkgbase']:{'input_digest':p['input_digest'], 'content_digest':p['content_digest']} for p in first['packages']}}
            unchanged = publish.expectations('c'*40,2,1,previous,pins)
            self.assertTrue(all(p['reuse'] for p in unchanged['packages']))
            self.assertTrue(all(p['aur'] is None for p in unchanged['packages']))
            with patch.object(publish, 'harness_digest', return_value='f'*64):
                upgraded = publish.expectations('c'*40, 2, 1, previous, pins)
            self.assertTrue(all(package['reuse'] for package in upgraded['packages']))
            (checkout / 'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:' + 'f'*64)
            upgraded_image = publish.expectations('c'*40, 2, 1, previous, pins)
            self.assertTrue(all(package['reuse'] for package in upgraded_image['packages']))
            legacy = {'accepted_sha': 'a'*40, 'recipes': {package['pkgbase']: {'input_digest': package['input_digest']} for package in first['packages']}}
            def extract_previous(archive, destination):
                destination.mkdir()
                (destination / 'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:' + 'a'*64)
            with patch.object(publish.github_api, 'download') as download, patch.object(publish, 'extract_tree', side_effect=extract_previous):
                migrated = publish.expectations('c'*40, 2, 1, legacy, pins)
            self.assertTrue(all(package['reuse'] for package in migrated['packages']))
            download.assert_called_once()
            # An accepted local input byte changes without changing the package version.
            (checkout / 'recipes/example2/PKGBUILD').write_text('pkgname=example2\n# changed accepted payload\n')
            changed = publish.expectations('d'*40,3,1,previous,pins)
            self.assertEqual([p['pkgbase'] for p in changed['packages'] if not p['reuse']],['example2'])
            from tools.sources import FIELDS
            source = {field:None for field in FIELDS}
            source.update({'id':'local-payload','kind':'local','source':'PKGBUILD','checksums':{'sha256':'f'*64}})
            (checkout / 'inputs/example3.json').write_text(json.dumps({'schema':1,'version':'1-1','sources':[source]}))
            source_changed = publish.expectations('d'*40,3,1,previous,pins)
            self.assertEqual([p['pkgbase'] for p in source_changed['packages'] if not p['reuse']],['example2','example3'])
            upstream = checkout / 'upstream/example1.json'
            upstream.write_text(json.dumps({'aur':{'url':'https://github.com/archlinux/aur.git','commit':'e'*40}}))
            with_aur = publish.expectations('d'*40,3,1,previous,pins)
            self.assertEqual(with_aur['packages'][1]['aur']['commit'],'e'*40)
            upstream.write_text(json.dumps({'aur': {
                'package': 'example1-bin',
                'url': 'https://aur.archlinux.org/example1-bin.git',
                'ref': 'refs/heads/master',
                'commit': 'e' * 40}}))
            alias_plan = publish.expectations('d' * 40, 3, 1, previous, pins)
            self.assertTrue(alias_plan['packages'][1]['reuse'])
            for package, url in (
                    ('example1-bin', 'https://aur.archlinux.org/example1.git'),
                    ('../example1-bin', 'https://github.com/archlinux/aur.git'),
                    (None, 'https://github.com/archlinux/aur.git')):
                upstream.write_text(json.dumps({'aur': {
                    'package': package, 'url': url, 'commit': 'e' * 40}}))
                with self.subTest(package=package, url=url), self.assertRaises(ValueError):
                    publish.expectations('d' * 40, 3, 1, previous, pins)
    def approved_output(self, pkgbase='example', version='1-1'):
        output = self.root / 'unsigned'; output.mkdir()
        text = f'pkgbase = {pkgbase}\n\tpkgver = {version.rsplit("-", 1)[0]}\n\tpkgrel = {version.rsplit("-", 1)[1]}\n\tarch = any\npkgname = {pkgbase}\n'
        metadata = publish.parse_srcinfo(text)
        package = {'recipe_commit':'e'*40,'pkgbase':pkgbase,'lock':{'version':version,'sources':[]},
                   'input_digest':'a'*64,'tree_sha':'b'*64,'metadata':metadata,'reuse':False,
                   'policy':{'outputs':[{'name':pkgbase,'arch':'any'}]}}
        original_record = {'head':'d'*40,'base':'c'*40,'run_id':'1','run_attempt':'2',
                           'image':'original-image','harness_sha':'c'*64,'packages':[copy.deepcopy(package)]}
        original_record['packages'][0]['recipe_commit'] = 'd'*40
        # Publication has different controls and identity; it cannot relabel the producer.
        plan = {'head':'f'*40,'run_id':'99','run_attempt':'3','image':'new-image',
                'harness_sha':'f'*64,'packages':[package]}
        descriptor = {'record':original_record,'producer':{'run_id':'7','run_attempt':'2'}}
        package['build'] = descriptor
        package['acceptance'] = {'head':'d'*40,'accepted':'e'*40,'recipe_tree':'b'*40}
        filename = next(iter(publish.filenames(package)))
        (output / filename).write_bytes(b'original bytes')
        receipt = {'recipe_commit':'d'*40,'pkgbase':pkgbase,'source_lock':package['lock'],
                   'input_digest':package['input_digest'],'tree_sha':package['tree_sha'],
                   'metadata':metadata | {'srcinfo':text},
                   **{k:original_record[k] for k in ('run_id','run_attempt','image','harness_sha')},
                   'files':[{'filename':filename, 'name':pkgbase,'version':version,'arch':'any'}]}
        native = {'head':'d'*40,'run_id':'1','run_attempt':'2','packages':[receipt]}
        def materialize(descriptor, destination):
            destination.mkdir()
            (destination / filename).write_bytes(b'original bytes')
            (destination / 'native-evidence.json').write_text(json.dumps(native))
            return destination
        (output / 'native-evidence.json').write_text(json.dumps(native))
        return output, plan, native, materialize

    def test_collection_carries_only_bound_metadata(self):
        plan = {'packages':[{'pkgbase':'example', 'reuse':False, 'build':{'request_id':'request-1'}}]}
        destination = self.root / 'collection'
        with patch.object(publish, 'current_main'), patch.object(publish.recipes, 'materialize', return_value={}), patch.object(publish, 'keyring'), patch.object(publish, 'latest_release', return_value=None), patch.object(publish, 'previous_catalog', return_value=None), patch.object(publish, 'expectations', return_value=plan), patch.object(publish, 'bind_accepted_builds'):
            publish.prepare('a' * 40, 1, 1, destination)
        self.assertEqual({p.name for p in destination.iterdir()}, {'publication-plan.json'})
        self.assertEqual(json.loads((destination / 'publication-plan.json').read_text())['expected'], plan)

    def test_inclusion_keeps_original_evidence_and_fetches_once(self):
        output, plan, native, materialize = self.approved_output()
        from tools import build_store, recipe_acceptance
        mapping = plan['packages'][0]['acceptance']
        with patch.object(build_store, 'materialize', side_effect=materialize) as fetch, patch.object(recipe_acceptance, 'verify_accepted_build', return_value={'mapping':mapping}):
            proof = publish.materialize_builds(self.root / 'inclusion', plan)
        self.assertEqual(proof['example'], native)
        self.assertEqual((self.root / 'inclusion/example-1-1-any.pkg.tar.zst').read_bytes(), b'original bytes')
        fetch.assert_called_once_with(plan['packages'][0]['build'], self.root / 'original-example')

    def test_inclusion_rejects_changed_acceptance_before_fetching(self):
        output, plan, native, materialize = self.approved_output()
        from tools import build_store, recipe_acceptance
        with patch.object(build_store, 'materialize') as fetch, patch.object(recipe_acceptance, 'verify_accepted_build', return_value={'mapping':{}}), self.assertRaisesRegex(ValueError, 'merge mapping'):
            publish.materialize_builds(self.root / 'inclusion', plan)
        fetch.assert_not_called()

    def test_signed_catalog_rejects_original_producer_source_and_package_hash_laundering(self):
        catalog = self.catalog_fixture('vith-gh')
        original = {'pkgbase':'example','recipe_commit':'d'*40,'input_digest':'c'*64,
                    'lock':{'sources':[]}}
        record = {'repository':publish.REPOSITORY,'base':'b'*40,'head':'d'*40,
                  'run_id':'4','run_attempt':'2','image':'original-image',
                  'harness_sha':'f'*64,'recipe_tree':'a'*40,'packages':[original]}
        native = {k:v for k,v in record.items() if k != 'packages'}
        native['packages'] = [{'recipe_commit':'d'*40,'input_digest':'c'*64,
                               'pkgbase':'example','source_lock':original['lock'],
                               'files':[{'filename':'example-1-1-any.pkg.tar.zst','sha256':'b'*64}]}]
        catalog['recipes']['example'] = {
            'recipe_commit':'d'*40,'accepted_recipe_commit':'e'*40,'input_digest':'c'*64,
            'sources':[], 'accepted_mapping':{'head':'d'*40,'accepted':'e'*40,'recipe_tree':'a'*40},
            'build_provenance':{'pkgbase':'example','input_digest':'c'*64,'record':record},
            'native_evidence':native,
        }
        moved = copy.deepcopy(catalog)
        recipe = moved['recipes']['example']
        evidence = recipe['native_evidence']
        evidence['harness_sha'] = 'a' * 64
        evidence['packages'][0]['request_input_digest'] = 'c' * 64
        evidence['packages'][0]['input_digest'] = 'd' * 64
        recipe['input_digest'] = 'd' * 64
        recipe['build_provenance'].update({'schema':3, 'producer':{'run_id':'4', 'run_attempt':'2', 'head_sha':'e' * 40}, 'evidence':copy.deepcopy(evidence)})
        publish.validate_catalog(moved, publish.REPOSITORY, 'A' * 40)
        recipe['native_evidence']['harness_sha'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'changes original native evidence'):
            publish.validate_catalog(moved, publish.REPOSITORY, 'A' * 40)
        publish.validate_catalog(catalog,publish.REPOSITORY,'A'*40)
        for field, bad in [('recipe_commit','e'*40),('input_digest','a'*64),('sources',[{}])]:
            broken = copy.deepcopy(catalog)
            broken['recipes']['example'][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                publish.validate_catalog(broken,publish.REPOSITORY,'A'*40)
        broken = copy.deepcopy(catalog)
        broken['recipes']['example']['native_evidence']['run_attempt'] = '3'
        with self.assertRaisesRegex(ValueError,'evidence identity'):
            publish.validate_catalog(broken,publish.REPOSITORY,'A'*40)
        broken = copy.deepcopy(catalog)
        broken['files']['example-1-1-any.pkg.tar.zst']['sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError,'original build hash'):
            publish.validate_catalog(broken,publish.REPOSITORY,'A'*40)

    def test_signed_catalog_destination_cannot_redirect_to_arbitrary_host(self):
        with self.assertRaisesRegex(ValueError,'immutable repository'):
            publish.previous_catalog('https://attacker.example/catalog.json',self.root,self.root/'ring')


class AcceptedDescriptorPlanning(unittest.TestCase):
    """Exercise the real acceptance/state/worker contract at publication planning."""
    def setUp(self):
        from tools import build_store, recipe_acceptance, recipe_candidates, recipe_gate, recipe_state, update
        self.acceptance = recipe_acceptance
        self.state = recipe_state
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            'GITHUB_REPOSITORY': 'owner/repo',
            'GITHUB_RUN_ID': '100', 'GITHUB_RUN_ATTEMPT': '1'}))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(publish, 'ROOT', self.root))
        self.main, self.accepted, base, head, tree = (c * 40 for c in 'acdef')
        expected = 'pkgbase = demo\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = x86_64\npkgname = demo\n'
        actual = expected.replace('pkgver = 1', 'pkgver = 2')
        policy = {'pkgbase': 'demo', 'outputs': [{'name': 'demo', 'arch': 'x86_64'}]}
        planned_lock = {'schema': 1, 'version': '1-1', 'sources': []}
        actual_lock = {'schema': 1, 'version': '2-1', 'sources': []}
        frozen = {'pkgbase': 'demo', 'recipe_commit': head, 'tree_sha': '1' * 64,
                  'input_digest': '2' * 64, 'expected_srcinfo': expected,
                  'policy': policy, 'lock': planned_lock}
        self.record = {
            'schema': 1, 'repository': 'owner/repo', 'pkgbase': 'demo',
            'base': base, 'head': head, 'recipe_base': '9' * 40, 'recipe_tree': tree,
            'pr_number': 7, 'image': 'ghcr.io/archlinux/archlinux@sha256:' + '3' * 64,
            'recipe_pins': {'demo': head}, 'previous_recipe_pins': {'demo': '9' * 40},
            'provenance': {'watchers': []}, 'packages': [frozen]}
        self.record['request_id'] = recipe_candidates.request_id(self.record)
        producer = {'run_id': '200', 'run_attempt': '1', 'head_sha': base,
                    'path': build_store.WORKFLOW, 'pkgbase': 'demo'}
        native = {'pkgbase': 'demo', 'recipe_commit': head, 'tree_sha': frozen['tree_sha'],
                  'request_input_digest': frozen['input_digest'], 'input_digest': '4' * 64,
                  'harness_sha': '5' * 64, 'run_id': '200', 'run_attempt': '1',
                  'metadata': {**recipe_gate.parse_srcinfo(actual), 'srcinfo': actual,
                               'dynamic_pkgver': True},
                  'source_lock': actual_lock,
                  'files': [{'name': 'demo', 'arch': 'x86_64', 'version': '2-1',
                             'filename': 'demo-2-1-x86_64.pkg.tar.zst', 'sha256': '6' * 64}]}
        evidence = {key: copy.deepcopy(self.record[key]) for key in (
            'schema', 'repository', 'base', 'head', 'request_id', 'image',
            'recipe_pins', 'previous_recipe_pins')}
        evidence.update({'producer': producer, 'run_id': '200', 'run_attempt': '1',
                         'harness_sha': native['harness_sha'], 'packages': [native]})
        self.descriptor = {
            'schema': 3, 'request_id': self.record['request_id'], 'pkgbase': 'demo',
            'input_digest': frozen['input_digest'], 'record': self.record, 'producer': producer,
            'release': {'id': 10, 'tag_name': 'build-' + self.record['request_id'],
                        'target_commitish': base},
            'asset': {'id': 11, 'name': 'unsigned.tar', 'size': 10240, 'sha256': '7' * 64},
            'attestation': {'id': 12, 'name': 'attestation.jsonl', 'size': 100,
                            'sha256': '8' * 64,
                            'signer': {key: producer[key] for key in (
                                'run_id', 'run_attempt', 'head_sha', 'path')}},
            'evidence_sha256': recipe_acceptance.digest(evidence), 'evidence': evidence}
        self.receipt = {
            'schema': 1, 'kind': 'recipe-acceptance', 'repository': 'owner/repo',
            'pkgbase': 'demo', 'candidate_base': base, 'accepted': self.accepted,
            'candidate_digest': recipe_candidates.candidate_digest(self.record),
            'build': self.descriptor, 'lock': actual_lock, 'policy': policy,
            'provenance': self.record['provenance'],
            **{key: self.record[key] for key in ('recipe_base', 'head', 'recipe_tree', 'pr_number')}}
        self.plan = {'head': self.main, 'packages': [{
            'pkgbase': 'demo', 'recipe_commit': self.accepted, 'recipe_dir': 'recipes/demo',
            'input_digest': 'a' * 64, 'lock': actual_lock, 'policy': policy,
            'metadata': recipe_gate.parse_srcinfo(expected), 'reuse': False}]}
        recipe = self.root / 'recipes/demo'
        recipe.mkdir(parents=True)
        (recipe / '.SRCINFO').write_text(expected)
        self.worker_conclusion = 'success'
        self.stack.enter_context(patch.object(update, 'ref_head', return_value='b' * 40))
        self.stack.enter_context(patch.object(update, 'api', side_effect=self.api))
        forbidden = AssertionError('planning must not download worker/package bytes')
        self.stack.enter_context(patch.object(build_store, 'download_asset', side_effect=forbidden))
        self.stack.enter_context(patch.object(build_store, 'download_api', side_effect=forbidden))
        self.stack.enter_context(patch.object(publish.github_api, 'download', side_effect=forbidden))

    def api(self, endpoint, method='GET', data=None, **kwargs):
        if endpoint == 'graphql':
            rows = {}
            for alias, path in re.findall(r'(r\d+): object\(expression: "[^":]+:([^"]+)"\)', data['query']):
                namespace, key = path.removesuffix('.json').split('/')
                if key != self.record['request_id']:
                    raise AssertionError('unexpected historical state lookup')
                value = {'build-request': self.record, 'build-result': self.descriptor}[namespace]
                raw = json.dumps({'schema': 1, 'digest': self.state.digest(value), 'value': value})
                rows[alias] = {'__typename': 'Blob', 'byteSize': len(raw.encode()),
                               'isBinary': False, 'text': raw}
            return {'data': {'repository': rows}}
        prefix = '/repos/owner/repo'
        if endpoint == prefix + '/git/commits/' + self.main:
            return {'tree': {'sha': 'c' * 40}}
        if endpoint == prefix + '/git/trees/' + 'c' * 40 + '?recursive=1':
            leaves = {
                'recipes/demo': ('160000', 'commit', self.accepted),
                **{path: ('100644', 'blob', self.acceptance.blob_sha(value))
                   for path, value in [('acceptance/demo.json', self.receipt),
                                       ('inputs/demo.json', self.receipt['lock']),
                                       ('upstream/demo.json', self.receipt['provenance'])]}}
            return {'tree': [{'path': path, 'mode': row[0], 'type': row[1], 'sha': row[2]}
                             for path, row in leaves.items()], 'truncated': False}
        if endpoint == prefix + '/git/blobs/' + self.acceptance.blob_sha(self.receipt):
            return {'encoding': 'base64', 'content': base64.b64encode(
                publish.github_api.canonical(self.receipt)).decode()}
        if endpoint == prefix + '/git/commits/' + self.accepted:
            return {'sha': self.accepted, 'tree': {'sha': self.record['recipe_tree']},
                    'parents': [{'sha': self.record['recipe_base']}, {'sha': self.record['head']}]}
        if endpoint == prefix + '/actions/runs/200/attempts/1':
            if self.descriptor['schema'] != 3:
                raise AssertionError('unsupported descriptors must not consult worker history')
            return {'id': 200, 'run_attempt': 1, 'status': 'completed',
                    'conclusion': self.worker_conclusion, 'head_sha': self.record['base'],
                    'path': '.github/workflows/build-package.yml', 'head_branch': 'main',
                    'event': 'workflow_dispatch', 'repository': {'full_name': 'owner/repo'}}
        raise AssertionError('unexpected transport: ' + endpoint)

    def test_current_descriptor_preserves_original_producer_and_actual_native_version(self):
        original = copy.deepcopy(self.descriptor)
        publish.bind_accepted_builds(self.plan)
        package = self.plan['packages'][0]
        self.assertEqual(package['build'], original)
        self.assertEqual(package['metadata']['version'], '2-1')
        self.assertEqual(package['acceptance'], {
            'head': self.record['head'], 'accepted': self.accepted,
            'recipe_tree': self.record['recipe_tree']})
        self.assertEqual(self.descriptor, original)

    def test_unsupported_embedded_descriptor_rejects_before_worker_or_download(self):
        self.descriptor['schema'] = 2
        del self.descriptor['evidence']
        del self.descriptor['request_id']
        with self.assertRaises(ValueError):
            publish.bind_accepted_builds(self.plan)
        self.assertNotIn('build', self.plan['packages'][0])
        self.assertNotIn('acceptance', self.plan['packages'][0])

    def test_failed_entire_worker_rejects_even_with_valid_native_output(self):
        self.worker_conclusion = 'failure'
        with self.assertRaises(ValueError):
            publish.bind_accepted_builds(self.plan)
        package = self.plan['packages'][0]
        self.assertNotIn('build', package)
        self.assertNotIn('acceptance', package)
        self.assertEqual(package['metadata']['version'], '1-1')


if __name__=='__main__':
    unittest.main()
