import argparse
import logging
import socket
import os
import re
import struct
import subprocess
import sys
import time

import eventlet
import eventlet.wsgi

from os_win import utilsfactory as os_win_utilsfactory

# TODO: ditch pywin32
import win32api
import win32file
import win32pipe
import win32security

eventlet.monkey_patch(os=False)

LOG = logging.getLogger()

EVENTLET_DEBUG = False
BIND_ADDR = "127.0.0.1"
BIND_PORT = 1234
WORKER_COUNT = 4

parser = argparse.ArgumentParser(
    description='Helper publishing subunit test results.')
parser.add_argument('--pipe-handle', required=False)

args = parser.parse_args()


def configure_logging(debug=True):
    log_level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler()
    handler.setLevel(log_level)

    log_fmt = '[%(asctime)s] %(levelname)s - %(message)s'
    formatter = logging.Formatter(log_fmt)
    handler.setFormatter(formatter)

    LOG.addHandler(handler)
    LOG.setLevel(log_level)


def app(env, start_response):
    LOG.info("Handling request.")
    # time.sleep(10)
    # import ctypes
    # ctypes.windll.Kernel32.Sleep(10000)
    start_response('200 OK', [('Content-Type', 'text/plain')])

    response = ['Test WS.\r\nWorker: %s' % os.getpid()]
    LOG.info("Returning response: %s", response)
    return response


class Win32ProcessLauncher(object):
    def __init__(self):
        self._processutils = os_win_utilsfactory.get_processutils()

        self._workers = []
        self._worker_job_handles = []
        # TODO: should we add signal handlers?

    def add_process(self, cmd):
        LOG.info("Starting subprocess: %s", cmd)

        worker = subprocess.Popen(cmd, close_fds=False)
        try:
            job_handle = self._processutils.kill_process_on_job_close(
                worker.pid)
        except Exception:
            LOG.exception("Could not associate child process "
                          "with a job, killing it.")
            worker.kill()
            raise

        self._worker_job_handles.append(job_handle)
        self._workers.append(worker)

        return worker

    def wait(self):
        pids = [worker.pid for worker in self._workers]
        if pids:
            self._processutils.wait_for_multiple_processes(pids,
                                                           wait_all=True)
        # By sleeping here, we allow signal handlers to be executed.
        time.sleep(0)


class Server(object):
    _py_script_re = re.compile(r'.*\.py\w?$')

    def __init__(self, app, worker_count=0, sock=None):
        self._app = app
        self._worker_count = worker_count

        self._launcher = Win32ProcessLauncher()

        if sock:
            self._sock = sock
        else:
            self._config_socket()

    def _config_socket(self, fromfd=None, family=socket.AF_INET,
                       type_=socket.SOCK_STREAM):
        addr = (BIND_ADDR, BIND_PORT)
        self._sock = eventlet.listen(addr, family)

    def serve(self):
        eventlet.wsgi.server(self._sock, self._app, log=LOG,
                             debug=EVENTLET_DEBUG)

    def start(self):
        if not self._worker_count:
            self.serve()
        else:
            for idx in range(self._worker_count):
                LOG.info("Starting worker: %s", idx)
                rfd, wfd = create_pipe()

                cmd = sys.argv + ['--pipe-handle=%s' % int(rfd)]
                # Recent setuptools versions will trim '-script.py' and '.exe'
                # extensions from sys.argv[0].
                if self._py_script_re.match(sys.argv[0]):
                    cmd = [sys.executable] + cmd
                    worker = self._launcher.add_process(cmd)
                    win32file.CloseHandle(rfd)
                    # Python 3 makes it easier to share sockets.
                    handle = None
                    try:
                        share_sock_buff = self._sock.share(worker.pid)
                    finally:
                        if handle:
                            handle.close()
                    win32file.WriteFile(wfd,
                                        struct.pack('<I', len(share_sock_buff)))
                    win32file.WriteFile(wfd, share_sock_buff)

            self._launcher.wait()


def create_pipe(sAttrs=-1, nSize=None):
    # Default values if parameters are not passed
    if sAttrs == -1:
        sAttrs = win32security.SECURITY_ATTRIBUTES()
        sAttrs.bInheritHandle = 1
    if nSize is None:
        # If this parameter is zero, the system uses the default buffer size.
        nSize = 0

    return win32pipe.CreatePipe(sAttrs, nSize)


if __name__ == '__main__':
    configure_logging()

    if args.pipe_handle:
        pipe_handle = int(args.pipe_handle)
        (error, socket_buff_sz) = win32file.ReadFile(pipe_handle, 4)
        socket_buff_sz = struct.unpack('<I', socket_buff_sz)[0]
        (error, socket_buff_data) = win32file.ReadFile(pipe_handle, socket_buff_sz)
        sock = socket.fromshare(socket_buff_data)
        worker_count = 0
    else:
        sock = None
        worker_count = WORKER_COUNT

    sv = Server(app, worker_count, sock)
    sv.start()
