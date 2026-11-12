"""Request/worker diagnostics without bodies, query strings, credentials or locals."""
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

LOGGER = logging.getLogger('uvicorn.error')
REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def emit(event, **fields):
    LOGGER.info(json.dumps({'event': event, 'at': datetime.now(timezone.utc).isoformat(),
                           'pid': os.getpid(), **fields}, ensure_ascii=True))


def resources(pid=None):
    """Best effort on Linux; diagnostic failures must never break a request."""
    result = {}
    try:
        status = Path(f'/proc/{pid or os.getpid()}/status').read_text()
        result.update({key: int(value) for key, value in re.findall(
            r'^(VmRSS|VmHWM):\s+(\d+) kB$', status, re.M)})
    except (OSError, ValueError):
        pass
    for name in ('memory.events', 'memory.current', 'memory.max'):
        try:
            result[name] = Path('/sys/fs/cgroup', name).read_text().strip()[:512]
        except OSError:
            pass
    return result


def stacks():
    # FrameSummary contains filenames/functions/line numbers, never variable values.
    return {str(identity): [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                           for frame in traceback.extract_stack(stack, limit=24)]
            for identity, stack in list(sys._current_frames().items())[:32]}


class RequestDiagnostics:
    def __init__(self, app, *, slow_seconds=10, poll_seconds=2, monitor=True):
        self.app = app
        self.slow_seconds = slow_seconds
        self.poll_seconds = poll_seconds
        self.monitor = monitor
        self.active = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        if not self.monitor:
            return
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self.watch, name='request-diagnostics', daemon=True)
                self.thread.start()

    def watch(self):
        while not self.stop.wait(self.poll_seconds):
            self.report_slow()

    def report_slow(self):
        now = time.monotonic()
        with self.lock:
            overdue = [dict(row) for row in self.active.values()
                       if not row['reported'] and now - row['started'] >= self.slow_seconds]
            for row in self.active.values():
                if now - row['started'] >= self.slow_seconds:
                    row['reported'] = True
            active_count = len(self.active)
        if overdue:
            emit('requests_stalled', active_count=active_count,
                 requests=[{key: row[key] for key in ('request_id', 'method', 'path')} |
                           {'elapsed_ms': round((now-row['started'])*1000)} for row in overdue[:8]],
                 resource=resources(), threads=stacks())

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            try:
                await self.app(scope, receive, send)
            finally:
                self.stop.set()
            return
        if scope['type'] != 'http' or scope.get('path', '').startswith('/static/'):
            await self.app(scope, receive, send)
            return
        self.start()
        raw = next((value for key, value in scope.get('headers', []) if key.lower()==b'x-request-id'), b'')
        candidate = raw.decode('ascii', errors='replace')
        identity = candidate if REQUEST_ID.fullmatch(candidate) else uuid.uuid4().hex
        fields = {'request_id': identity, 'method': scope['method'], 'path': scope.get('path', '')[:256]}
        scope.setdefault('state', {})['request_id'] = identity
        started = time.monotonic()
        # Separate invocation key: simultaneous requests may carry the same incoming ID.
        key = object()
        with self.lock:
            self.active[key] = {**fields, 'started': started, 'reported': False}
        emit('request_start', **fields)
        status, size, finished, failure = None, 0, False, None

        async def traced_send(message):
            nonlocal status, size, finished
            if message['type'] == 'http.response.start':
                status = message['status']
                message = {**message, 'headers': [(key, value) for key, value in message.get('headers', [])
                           if key.lower()!=b'x-request-id'] + [(b'x-request-id', identity.encode())]}
            elif message['type'] == 'http.response.body':
                size += len(message.get('body', b''))
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                finished = True

        try:
            await self.app(scope, receive, traced_send)
        except BaseException as exc:
            failure = type(exc).__name__
            emit('request_exception', **fields, exception_type=failure,
                 frames=[{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                         for frame in traceback.extract_tb(exc.__traceback__, limit=24)])
            raise
        finally:
            with self.lock:
                self.active.pop(key, None)
            duration = round((time.monotonic()-started)*1000)
            emit('request_end', **fields, status=status, bytes=size, elapsed_ms=duration,
                 complete=finished, exception_type=failure,
                 resource=resources() if duration>=self.slow_seconds*1000 or failure else None)
