import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from tools import publish, cloudflare


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
                if self.path.endswith('/deployments'):
                    self.respond({'success':True,'result':[{'versions':[{'version_id':owner.version,'percentage':100}]}]})
                elif self.path == '/assets':
                    self.respond([{'name':name} for name in owner.assets if name != owner.omit])
                else:
                    name = self.path.rsplit('/',1)[-1]
                    if owner.draft or name not in owner.assets:
                        self.send_error(404); return
                    body = owner.assets[name]
                    self.send_response(200); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length','0')))
                if self.path.endswith('/deployments'):
                    owner.version = json.loads(body)['versions'][0]['version_id']
                    self.respond({'success':True,'result':{}})
                elif self.path == '/release':
                    owner.draft = True; self.respond({'id':1})
                else:
                    name = self.path.rsplit('/',1)[-1]
                    owner.assets[name] = body; self.respond({'name':name})
            def do_PATCH(self):
                owner.draft = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['draft']
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
        self.work = tempfile.TemporaryDirectory(dir=Path.cwd())
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
    def readback_fixture(self):
        assets = self.root/'assets'; assets.mkdir()
        bodies = {'example-1-1-any.pkg.tar.zst':b'package bytes','example-1-1-any.pkg.tar.zst.sig':b'signature bytes','catalog.json':b'catalog','catalog.json.sig':b'catalog signature'}
        for name, body in bodies.items(): (assets/name).write_bytes(body)
        self.http.assets.update(bodies); self.http.draft=False
        catalog = {'snapshot':'snapshot-fixture','files':{n:{'url':cloudflare.release_url(publish.REPOSITORY,'snapshot-fixture',n),'sha256':hashlib.sha256(b).hexdigest()} for n,b in bodies.items() if not n.startswith('catalog.')},'source_assets':{}}
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
        client=cloudflare.Cloudflare('a'*32,'fixture-token',base=self.http.base)
        request = client.request
        def lost_response(path, method='GET', data=None, content_type='application/json'):
            result = request(path, method, data, content_type)
            if method == 'POST':
                raise TimeoutError('activation response lost after server committed')
            return result
        with patch.object(client, 'request', side_effect=lost_response):
            self.assertEqual(client.activate('new-version'),'new-version')
        self.assertEqual(self.http.version,'new-version')
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
    def test_artifact_rejects_traversal_links_duplicates_and_extras(self):
        for kind in ('traversal','symlink','duplicate'):
            path=self.root/(kind+'.tar')
            with tarfile.open(path,'w') as archive:
                member=tarfile.TarInfo('../escape' if kind=='traversal' else 'package')
                if kind=='symlink': member.type=tarfile.SYMTYPE; member.linkname='/etc/passwd'
                archive.addfile(member)
                if kind=='duplicate': archive.addfile(member)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError,'unsafe'):
                publish.safe_extract(path,self.root/kind)
        output=self.root/'unsigned'; output.mkdir()
        (output/'surprise.pkg.tar.zst').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError,'extra/missing'):
            publish.validate_unsigned(output,{'packages':[]})
    def test_shell_shaped_and_duplicate_pkginfo_keys_rejected(self):
        for content in (b'$(touch owned) = value\n',b'pkgname = x\npkgname = y\npkgver = 1-1\narch = any\n'):
            with self.subTest(content=content), patch.object(publish,'bounded_metadata',return_value=content), self.assertRaises(ValueError):
                publish.pkginfo(self.root/'unused')
    def test_full_input_digest_reuses_unchanged_and_rebuilds_source_only_change(self):
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
        with patch.object(publish,'ROOT',checkout), patch.object(publish,'harness_digest',return_value='b'*64):
            first = publish.expectations('a'*40,1,1,None)
            self.assertEqual({p['pkgbase'] for p in first['packages'] if not p['reuse']},{'example'+str(i) for i in range(5)})
            previous = {'recipes':{p['pkgbase']:{'input_digest':p['input_digest']} for p in first['packages']}}
            unchanged = publish.expectations('c'*40,2,1,previous)
            self.assertTrue(all(p['reuse'] for p in unchanged['packages']))
            self.assertTrue(all(p['aur'] is None for p in unchanged['packages']))
            # An accepted local input byte changes without changing the package version.
            (checkout / 'recipes/example2/PKGBUILD').write_text('pkgname=example2\n# changed accepted payload\n')
            changed = publish.expectations('d'*40,3,1,previous)
            self.assertEqual([p['pkgbase'] for p in changed['packages'] if not p['reuse']],['example2'])
            from tools.sources import FIELDS
            source = {field:None for field in FIELDS}
            source.update({'id':'local-payload','kind':'local','source':'PKGBUILD','checksums':{'sha256':'f'*64}})
            (checkout / 'inputs/example3.json').write_text(json.dumps({'schema':1,'version':'1-1','sources':[source]}))
            source_changed = publish.expectations('d'*40,3,1,previous)
            self.assertEqual([p['pkgbase'] for p in source_changed['packages'] if not p['reuse']],['example2','example3'])
            upstream = checkout / 'upstream/example1.json'
            upstream.write_text(json.dumps({'aur':{'url':'https://github.com/archlinux/aur.git','commit':'e'*40}}))
            with_aur = publish.expectations('d'*40,3,1,previous)
            self.assertEqual(with_aur['packages'][1]['aur']['commit'],'e'*40)
    def test_receipt_hash_and_run_identity_are_not_authority(self):
        output = self.root / 'unsigned'; output.mkdir()
        metadata = publish.parse_srcinfo('pkgbase = example\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = any\npkgname = example\n')
        text = 'pkgbase = example\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = any\npkgname = example\n'
        package = {'pkgbase':'example','lock':{'version':'1-1','sources':[]},'input_digest':'a'*64,'tree_sha':'b'*64,'metadata':metadata,'reuse':False,'policy':{'outputs':[{'name':'example','arch':'any'}]}}
        plan = {'schema':1,'repository':publish.REPOSITORY,'base':'a'*40,'head':'a'*40,'run_id':'1','run_attempt':'1','image':'image','harness_sha':'c'*64,'packages':[package]}
        filename = 'example-1-1-any.pkg.tar.zst'
        (output / filename).write_bytes(b'original bytes')
        receipt = {'pkgbase':'example','source_lock':package['lock'],'input_digest':package['input_digest'],'tree_sha':package['tree_sha'],'metadata':metadata | {'srcinfo':text},**{k:plan[k] for k in ('run_id','run_attempt','image','harness_sha')},'files':[{'filename':filename,'sha256':publish.sha(output/filename),'name':'example','version':'1-1','arch':'any'}]}
        evidence = {k:v for k,v in plan.items() if k != 'packages'} | {'packages':[receipt]}
        evidence_path = output / 'native-evidence.json'
        info = {'pkgname':'example','pkgver':'1-1','arch':'any'}
        with patch.object(publish,'pkginfo',return_value=info):
            evidence_path.write_text(json.dumps(evidence))
            publish.validate_unsigned(output,plan)
            (output / filename).write_bytes(b'tampered bytes')
            with self.assertRaisesRegex(ValueError,'metadata/hash'):
                publish.validate_unsigned(output,plan)
            evidence['run_attempt']='2'; evidence_path.write_text(json.dumps(evidence))
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                publish.validate_unsigned(output,plan)
    def test_signed_catalog_destination_cannot_redirect_to_arbitrary_host(self):
        with self.assertRaisesRegex(ValueError,'immutable repository'):
            publish.previous_catalog('https://attacker.example/catalog.json',self.root,self.root/'ring')


if __name__=='__main__':
    unittest.main()
