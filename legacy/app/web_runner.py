"""Uvicorn 0.34 supervisor adapter: distinguish watchdog kills from real exits.

Keep the pinned Uvicorn process lifecycle, signal handling and restart behavior.
Only add observations before its watchdog kill and after join obtains exitcode.
"""
import faulthandler
import os
import signal
import time

import uvicorn
from uvicorn.supervisors import multiprocess

from app.diagnostics import emit, resources

OriginalProcess = multiprocess.Process


def request_worker_stack(pid):
    """A bounded diagnostic window before Uvicorn's existing watchdog kill."""
    if hasattr(signal, 'SIGUSR1'):
        try:
            os.kill(pid, signal.SIGUSR1)
            time.sleep(.1)
        except ProcessLookupError:
            pass


class DiagnosticProcess(OriginalProcess):
    failure_reason = None

    def target(self, sockets=None):
        faulthandler.enable(all_threads=True)
        if hasattr(signal, 'SIGUSR1'):
            faulthandler.register(signal.SIGUSR1, all_threads=True)
        emit('worker_boot', parent_pid=os.getppid(), resource=resources())
        return super().target(sockets)

    def is_alive(self, timeout=5):
        if not self.process.is_alive():
            self.failure_reason = 'process_exit'
            return False
        if self.ping(timeout):
            return True
        self.failure_reason = 'heartbeat_timeout'
        emit('worker_unresponsive', worker_pid=self.pid, timeout_seconds=timeout,
             resource=resources(self.pid))
        request_worker_stack(self.pid)
        return False

    def join(self):
        super().join()
        code = self.process.exitcode
        name = None
        if code is not None and code < 0:
            try:
                name = signal.Signals(-code).name
            except ValueError:
                pass
        emit('worker_exit', worker_pid=self.pid, exitcode=code, signal=name,
             reason=self.failure_reason or 'supervisor_shutdown', resource=resources())


def main(*, host='0.0.0.0', port=8000):
    if uvicorn.__version__ != '0.34.0':
        raise RuntimeError('Revalidate diagnostic supervisor adapter before changing Uvicorn version')
    multiprocess.Process = DiagnosticProcess
    uvicorn.run('app.main:app', host=host, port=port, workers=2)


if __name__ == '__main__':
    main()
