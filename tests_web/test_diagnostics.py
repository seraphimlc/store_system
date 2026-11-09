import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import diagnostics
from app.diagnostics import RequestDiagnostics
from app.web_runner import DiagnosticProcess, OriginalProcess


def invoke(app, *, identity=b'from-nginx', path='/zones', query=b'token=SECRET'):
    scope = {'type': 'http', 'path': path, 'method': 'GET', 'query_string': query,
             'headers': [(b'x-request-id', identity), (b'authorization', b'Bearer SECRET')], 'state': {}}
    messages = []
    async def receive():
        return {'type': 'http.request', 'body': b'SECRET', 'more_body': False}
    async def send(message):
        messages.append(message)
    asyncio.run(app(scope, receive, send))
    return scope, messages


def test_request_id_and_stream_end_include_pid_status_size_but_no_secrets(monkeypatch):
    records = []
    monkeypatch.setattr(diagnostics, 'emit', lambda event, **fields: records.append((event, fields)))
    async def stream(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'x-request-id', b'replace-me')]})
        await send({'type': 'http.response.body', 'body': b'one', 'more_body': True})
        assert not any(event=='request_end' for event, _ in records)
        await send({'type': 'http.response.body', 'body': b'two', 'more_body': False})
    middleware = RequestDiagnostics(stream, monitor=False)
    scope, messages = invoke(middleware)
    assert scope['state']['request_id']=='from-nginx'
    assert messages[0]['headers']==[(b'x-request-id', b'from-nginx')]
    assert [event for event, _ in records]==['request_start', 'request_end']
    assert records[-1][1]['complete'] and records[-1][1]['status']==200
    assert records[-1][1]['bytes']==6
    assert not middleware.active
    assert 'SECRET' not in json.dumps(records)


def test_exception_is_logged_without_message_or_locals_and_propagates(monkeypatch):
    records = []
    monkeypatch.setattr(diagnostics, 'emit', lambda event, **fields: records.append((event, fields)))
    async def broken(scope, receive, send):
        secret_key='PRIVATE'
        raise RuntimeError(secret_key)
    middleware = RequestDiagnostics(broken, monitor=False)
    with pytest.raises(RuntimeError):
        invoke(middleware, identity=b'bad id\n')
    start = records[0][1]
    assert diagnostics.REQUEST_ID.fullmatch(start['request_id'])
    assert any(event=='request_exception' for event, _ in records)
    assert records[-1][1]['exception_type']=='RuntimeError'
    assert not records[-1][1]['complete']
    assert 'PRIVATE' not in json.dumps(records) and not middleware.active


def test_slow_request_reports_stack_and_resources_once(monkeypatch):
    records = []
    monkeypatch.setattr(diagnostics, 'emit', lambda event, **fields: records.append((event, fields)))
    monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: 20)
    middleware = RequestDiagnostics(None, slow_seconds=10, monitor=False)
    middleware.active[object()]={'request_id':'abc', 'method':'GET','path':'/zones',
                                 'started':0, 'reported':False}
    middleware.report_slow()
    middleware.report_slow()
    assert len(records)==1 and records[0][0]=='requests_stalled'
    assert records[0][1]['requests'][0]['elapsed_ms']==20000
    assert records[0][1]['threads']


def test_watchdog_timeout_is_distinguished_from_already_dead_process(monkeypatch):
    from app import web_runner
    records = []
    monkeypatch.setattr(web_runner, 'emit', lambda event, **fields: records.append((event, fields)))
    stack_request = Mock()
    monkeypatch.setattr(web_runner, 'request_worker_stack', stack_request)
    process=DiagnosticProcess.__new__(DiagnosticProcess)
    process.process=SimpleNamespace(is_alive=lambda: True, pid=42, exitcode=-9)
    process.ping=Mock(return_value=False)
    assert not process.is_alive()
    assert process.failure_reason=='heartbeat_timeout'
    stack_request.assert_called_once_with(42)
    assert records[0][0]=='worker_unresponsive' and records[0][1]['worker_pid']==42
    monkeypatch.setattr(OriginalProcess, 'join', lambda self: None)
    process.join()
    assert records[-1][1]['reason']=='heartbeat_timeout'
    assert records[-1][1]['exitcode']==-9 and records[-1][1]['signal']=='SIGKILL'
    process.process.is_alive=lambda: False
    assert not process.is_alive()
    process.join()
    assert records[-1][1]['reason']=='process_exit'


def test_nginx_timing_and_request_id_cover_every_proxied_location():
    config=Path('deploy/nginx.store-settle.conf').read_text()
    assert config.count('proxy_set_header X-Request-ID $request_id;')==config.count('proxy_pass ')
    for field in ('$request_time','$upstream_connect_time','$upstream_header_time',
                  '$upstream_response_time','$connection'):
        assert field in config
    assert '$request_uri' not in config.split("log_format store_diagnostics",1)[1].split(';',1)[0]


def test_real_worker_exit_reports_signal_and_replacement(tmp_path):
    """Exercise our owned supervisor only; never signal an existing dev service."""
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time
    import httpx

    if sys.platform == 'win32':
        pytest.skip('Unix process signals required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    launcher = tmp_path / 'run_diagnostic_web.py'
    launcher.write_text('from app.web_runner import main\n'
                        f'if __name__ == "__main__": main(host="127.0.0.1", port={port})\n')
    env = {**os.environ, 'DATABASE_URL': 'sqlite://', 'PYTHONPATH': str(Path.cwd())}
    log = tmp_path / 'worker.log'
    with log.open('w') as output:
        process = subprocess.Popen([sys.executable, str(launcher)], env=env,
                                   stdout=output, stderr=subprocess.STDOUT)
    def records():
        results = []
        for line in log.read_text().splitlines():
            if '{"event":' in line:
                try:
                    results.append(json.loads(line[line.index('{"event":'):]))
                except ValueError:
                    pass
        return results
    def until(predicate):
        deadline = time.monotonic()+25
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            assert process.poll() is None, log.read_text()[-2500:]
            time.sleep(.05)
        pytest.fail(log.read_text()[-2500:])
    try:
        boots = until(lambda: [r for r in records() if r['event']=='worker_boot']
                      if len([r for r in records() if r['event']=='worker_boot'])>=2 else [])
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f'http://127.0.0.1:{port}/login', headers={'X-Request-ID':'integration-test'})
        assert response.status_code==200 and response.headers['X-Request-ID']=='integration-test'
        victim = boots[0]['pid']
        os.kill(victim, signal.SIGKILL)
        exits = until(lambda: [r for r in records() if r['event']=='worker_exit' and r['worker_pid']==victim])
        assert exits[0]['reason']=='process_exit' and exits[0]['signal']=='SIGKILL'
        until(lambda: len([r for r in records() if r['event']=='worker_boot'])>=3)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
