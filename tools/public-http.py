#!/usr/bin/env python3
"""Local-only real-package HTTP proofs for the isolated signed consumer."""
import datetime
import http.server
import json
import math
import os
import pathlib
import subprocess
import sys
import threading


def clean_proxy_environment():
    return {key: value for key, value in os.environ.items()
            if key.lower() not in {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}}


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def do_GET(self):
        self.server.requests.append(self.path)
        try:
            response = self.server.respond(self.path)
            if response is None:
                self.server.unexpected.append(self.path)
                self.send_error(404, 'unexpected fixture request')
                return
            content_type, body = response
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            self.server.failures.append(repr(error))
            raise

    def log_message(self, *args):
        pass


def run_fixture(respond, proof):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    server.daemon_threads = True
    server.requests = []
    server.unexpected = []
    server.failures = []
    server.respond = respond
    assert server.server_address[0] == '127.0.0.1', server.server_address
    ready = threading.Event()

    def serve():
        # HTTPServer has already bound and listened before this thread starts.
        ready.set()
        server.serve_forever(poll_interval=0.1)

    worker = threading.Thread(target=serve, daemon=True)
    started = False
    try:
        worker.start()
        started = True
        assert ready.wait(timeout=5), 'local HTTP fixture did not become ready'
        assert worker.is_alive(), 'local HTTP fixture stopped before proof'
        proof(server.server_port, server.requests)
        assert not server.unexpected, server.unexpected
        assert not server.failures, server.failures
    finally:
        if started:
            server.shutdown()
        server.server_close()
        if started:
            worker.join(timeout=5)
            assert not worker.is_alive(), 'local HTTP fixture did not stop'


def blazetest_response(path):
    if path == 'http://www.speedtest.net/speedtest-servers.php':
        return 'text/plain', (
            b'<settings>\n<servers>\n'
            b'<server id="4242" sponsor="Offline Proof" name="Fixture City" '
            b'country="Testland" lat="12.5" lon="-45.25" '
            b'url="http://fixture.invalid/speedtest" />\n'
            b'</servers>\n</settings>\n')
    if path == 'http://fixture.invalid/speedtest/latency.txt':
        return 'text/plain', b'test=test\n'
    return None


def prove_blazetest(port, requests):
    env = clean_proxy_environment()
    proxy = f'http://127.0.0.1:{port}'
    env.update(http_proxy=proxy, HTTP_PROXY=proxy, https_proxy=proxy, HTTPS_PROXY=proxy,
               all_proxy=proxy, ALL_PROXY=proxy, no_proxy='', NO_PROXY='')
    process = subprocess.run(
        ['blazetest-rs', '--server', '4242', '--no-download', '--no-upload', '--json', '--no-color'],
        env=env, capture_output=True, text=True, timeout=15, check=True)
    assert not process.stderr, process
    result = json.loads(process.stdout)
    assert result['server'] == {
        'id': 4242, 'name': 'Fixture City', 'sponsor': 'Offline Proof',
        'country': 'Testland', 'location': 'Fixture City', 'latitude': 12.5,
        'longitude': -45.25, 'distance_km': 0.0, 'url': 'http://fixture.invalid/speedtest'}, result
    for key in ('ping_ms', 'jitter_ms', 'idle_latency_ms'):
        assert isinstance(result[key], (int, float)) and math.isfinite(result[key]) and result[key] >= 0, result
    assert result['idle_latency_ms'] == result['ping_ms'], result
    assert result['download_mbps'] == result['upload_mbps'] == result['test_duration_secs'] == 0, result
    assert datetime.datetime.fromisoformat(result['timestamp']).utcoffset() == datetime.timedelta(0), result
    assert requests == ['http://www.speedtest.net/speedtest-servers.php'] + [
        'http://fixture.invalid/speedtest/latency.txt'] * 3, requests
    print('blazetest parsed local server XML, selected exact server, sampled three local latency requests and serialized real JSON; no external speedtest or throughput traffic')


def gau_response(path):
    pages = {
        '/api/v1/indicators/domain/example.test/url_list?limit=100&page=1': {
            'has_next': True, 'url_list': [
                {'url': 'https://example.test/keep.html?one=1'},
                {'url': 'https://example.test/image.PNG'},
                {'url': 'http://%broken'}]},
        '/api/v1/indicators/domain/example.test/url_list?limit=100&page=2': {
            'has_next': False, 'url_list': [{'url': 'https://example.test/second.txt'}]},
    }
    if path not in pages:
        return None
    return 'application/json', json.dumps(pages[path]).encode()


def prove_gau(port, requests):
    pathlib.Path('gau.toml').write_text(
        'providers = ["otx"]\nthreads = 1\ntimeout = 2\nretries = 0\n'
        f'otx = "http://127.0.0.1:{port}/"\n')
    env = clean_proxy_environment()
    expected = ['https://example.test/keep.html?one=1', 'https://example.test/second.txt']
    command = ['gau', '--config', 'gau.toml', '--providers', 'otx', '--blacklist', '.png']
    plain = subprocess.run(command + ['example.test'], env=env,
                           capture_output=True, text=True, timeout=15, check=True)
    assert plain.stdout.splitlines() == expected and not plain.stderr, plain
    structured = subprocess.run(command + ['--json', 'example.test'], env=env,
                                capture_output=True, text=True, timeout=15, check=True)
    assert [json.loads(line) for line in structured.stdout.splitlines()] == [{'url': url} for url in expected], structured
    assert not structured.stderr, structured
    assert requests == [
        '/api/v1/indicators/domain/example.test/url_list?limit=100&page=1',
        '/api/v1/indicators/domain/example.test/url_list?limit=100&page=2',
    ] * 2, requests
    print('gau fetched two pages only from local OTX fixture; exact text/JSON URLs, case-insensitive extension filter and invalid URL rejection verified')


if __name__ == '__main__':
    proofs = {'blazetest-rs': (blazetest_response, prove_blazetest), 'gau': (gau_response, prove_gau)}
    if len(sys.argv) != 2 or sys.argv[1] not in proofs:
        raise SystemExit('expected an enrolled HTTP proof name')
    run_fixture(*proofs[sys.argv[1]])
