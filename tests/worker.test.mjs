import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const url = (snapshot, name) => `https://github.com/vith/arch-packages/releases/download/${snapshot}/${name}`;
const file = (snapshot, name) => ({url: url(snapshot, name), sha256: 'a'.repeat(64)});
const catalog = {
  schema: 1, repository: 'vith/arch-packages', snapshot: 'snapshot-new', key_fingerprint: 'A'.repeat(40),
  accepted_sha: 'a'.repeat(40), recipes: {pkg: {version: '2-1'}},
  files: {'n3t-arch.db': file('snapshot-new', 'n3t-arch.db'), 'n3t-arch.db.sig': file('snapshot-new', 'n3t-arch.db.sig'), 'n3t-arch.asc': file('snapshot-new', 'n3t-arch.asc'), 'pkg-2.pkg.tar.zst': file('snapshot-new', 'pkg-2.pkg.tar.zst')},
  retained_packages: {'pkg-1.pkg.tar.zst': file('snapshot-old', 'pkg-1.pkg.tar.zst'), 'pkg-1.pkg.tar.zst.sig': file('snapshot-old', 'pkg-1.pkg.tar.zst.sig')},
  source_assets: {'pkg-1-source.tar.gz': file('snapshot-old', 'pkg-1-source.tar.gz')},
  retained_snapshots: {'snapshot-old': {files: {'n3t-arch.db': file('snapshot-old', 'n3t-arch.db'), 'n3t-arch.db.sig': file('snapshot-old', 'n3t-arch.db.sig')}}}
};
async function load(value) {
  const module = execFileSync('python3', ['-c', 'import json,sys; from tools.cloudflare import generate_module; c=json.load(sys.stdin); print(generate_module(c,c["repository"],c["key_fingerprint"]))'], {
    input: JSON.stringify(value), cwd: new URL('..', import.meta.url), encoding: 'utf8'
  });
  return (await import('data:text/javascript;base64,' + Buffer.from(module).toString('base64'))).default;
}
for (const name of ['n3t-arch.files', 'n3t-arch.files.sig', 'pkg-2.pkg.tar.zst.sig']) catalog.files[name] = file('snapshot-new', name);
const worker = await load(catalog);
globalThis.fetch = () => { throw new Error('Runtime fetch forbidden'); };
const get = (path, method = 'GET') => worker.fetch(new Request('https://arch.packages.n3t.work' + path, {method}));
test('known assets redirect exactly and never forward query or credentials', async () => {
  for (const [path, destination] of [
    ['/repo/n3t-arch.db', url('snapshot-new', 'n3t-arch.db')],
    ['/repo/pkg-1.pkg.tar.zst', url('snapshot-old', 'pkg-1.pkg.tar.zst')],
    ['/repo/pkg-1.pkg.tar.zst.sig', url('snapshot-old', 'pkg-1.pkg.tar.zst.sig')],
    ['/sources/pkg-1-source.tar.gz', url('snapshot-old', 'pkg-1-source.tar.gz')],
    ['/catalog.json', url('snapshot-new', 'catalog.json')],
    ['/catalog.json.sig', url('snapshot-new', 'catalog.json.sig')]
  ]) {
    for (const method of ['GET', 'HEAD']) {
      const response = get(path + '?url=https://evil.example/&token=secret', method);
      assert.equal(response.status, 302);
      assert.equal(response.headers.get('location'), destination);
      assert.equal(response.headers.get('cache-control'), 'no-store');
      assert.equal(await response.text(), '');
    }
  }
});
test('unknown, encoded and traversal-looking names fail closed', () => {
  for (const path of ['/repo/unknown.pkg.tar.zst', '/repo/%252e%252e/secret', '/repo/%2fsecret', '/repo/../../evil', '/sources/https://evil.example', '/snapshots/unknown/n3t-arch.db', '/repo/__proto__']) {
    assert.equal(get(path).status, 404, path);
  }
  const response = get('/repo/n3t-arch.db', 'POST');
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('allow'), 'GET, HEAD');
  assert.equal(response.headers.get('cache-control'), 'no-store');
});
test('setup and health support HEAD, explain strict signature race recovery', async () => {
  assert.deepEqual(await get('/healthz').json(), {snapshot: 'snapshot-new'});
  const body = await get('/').text();
  assert.match(body, /SigLevel = Required/);
  assert.match(body, /straddle an activation/);
  assert.match(body, /snapshots\/snapshot-new/);
  assert.match(body, /pkg-1-source.tar.gz/);
  for (const path of ['/', '/healthz', '/unknown']) assert.equal(await get(path, 'HEAD').text(), '');
});
test('pinned old generation stays exact across activation; changing pointers can straddle', async () => {
  const nextFiles = {...catalog.files};
  for (const name of ['n3t-arch.db', 'n3t-arch.db.sig', 'n3t-arch.files', 'n3t-arch.files.sig', 'n3t-arch.asc']) nextFiles[name] = file('snapshot-next', name);
  const newer = await load({...catalog, snapshot: 'snapshot-next', files: nextFiles});
  const request = path => new Request('https://arch.packages.n3t.work' + path);
  assert.notEqual(get('/repo/n3t-arch.db').headers.get('location').split('/').at(-2), newer.fetch(request('/repo/n3t-arch.db.sig')).headers.get('location').split('/').at(-2));
  for (const name of ['n3t-arch.db', 'n3t-arch.db.sig']) {
    assert.equal(newer.fetch(request('/snapshots/snapshot-old/' + name)).headers.get('location'), get('/snapshots/snapshot-old/' + name).headers.get('location'));
  }
});
