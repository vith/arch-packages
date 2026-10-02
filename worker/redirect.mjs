// Generation prepends a JSON.parse catalog binding; this template performs no I/O.
const routes = new Map();
const active = CATALOG.snapshot;
const asset = (name) => `https://github.com/${CATALOG.repository}/releases/download/${encodeURIComponent(active)}/${encodeURIComponent(name)}`;
for (const [name, entry] of Object.entries(CATALOG.retained_packages)) routes.set(`/repo/${name}`, entry.url);
for (const [name, entry] of Object.entries(CATALOG.files)) routes.set(`/repo/${name}`, entry.url);
for (const [name, entry] of Object.entries(CATALOG.source_assets)) routes.set(`/sources/${name}`, entry.url);
for (const [snapshot, entry] of Object.entries(CATALOG.retained_snapshots)) {
  for (const [name, file] of Object.entries(entry.files)) routes.set(`/snapshots/${snapshot}/${name}`, file.url);
}
for (const [name, entry] of Object.entries(CATALOG.files)) routes.set(`/snapshots/${active}/${name}`, entry.url);
for (const name of ['catalog.json', 'catalog.json.sig']) {
  routes.set(`/${name}`, asset(name));
  routes.set(`/snapshots/${active}/${name}`, asset(name));
}
const html = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const page = `<!doctype html><meta charset="utf-8"><title>n3t Arch packages</title><h1>n3t Arch packages</h1>
<pre>[n3t-arch]\nSigLevel = Required\nServer = https://arch.packages.n3t.work/repo</pre>
<p>Import and locally trust the dedicated <a href="/repo/n3t-arch.asc">public key</a> only after comparing fingerprint <code>${html(CATALOG.key_fingerprint)}</code>.</p>
<p>Active snapshot: <a href="/snapshots/${encodeURIComponent(active)}/n3t-arch.db">${html(active)}</a>. For a fixed generation use <code>Server = https://arch.packages.n3t.work/snapshots/${html(active)}</code>.</p>
<p>Database and signature requests can straddle an activation. Required signatures fail closed; retry a fresh sync or use the fixed snapshot URL. No unsigned fallback exists.</p>
<p><a href="/catalog.json">Signed catalog and corresponding-source manifest</a> · <a href="/catalog.json.sig">Catalog signature</a></p>
<ul>${Object.keys(CATALOG.source_assets).map(name => `<li><a href="/sources/${encodeURIComponent(name)}">${html(name)}</a></li>`).join('')}</ul>`;
export default {
  fetch(request) {
    const head = request.method === 'HEAD';
    const respond = (body, status, headers = {}) => new Response(head ? null : body, {status, headers: {'Cache-Control': 'no-store', ...headers}});
    if (request.method !== 'GET' && !head) return respond('Method not allowed\n', 405, {'Allow': 'GET, HEAD'});
    // Only '+' has an escaped spelling among the allowed manifest filename bytes.
    // Never decode separators, dots or repeated escapes into route aliases.
    const path = new URL(request.url).pathname.replace(/%2b/gi, '+');
    if (path.includes('%') || path.includes('\\')) return respond('Not found\n', 404);
    if (path === '/') return respond(page, 200, {'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': "default-src 'none'; base-uri 'none'; frame-ancestors 'none'", 'X-Content-Type-Options': 'nosniff'});
    if (path === '/healthz') return respond(JSON.stringify({snapshot: active}), 200, {'Content-Type': 'application/json'});
    const destination = routes.get(path);
    return destination ? respond(null, 302, {'Location': destination}) : respond('Not found\n', 404);
  }
};
