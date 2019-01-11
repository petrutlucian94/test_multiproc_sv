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

        self._ioutils = os_win_utilsfactory.get_ioutils()
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
                rfd, wfd = self._ioutils.create_pipe(inherit_handle=True)

                cmd = sys.argv + ['--pipe-handle=%s' % int(rfd)]
                # Recent setuptools versions will trim '-script.py' and '.exe'
                # extensions from sys.argv[0].
                if self._py_script_re.match(sys.argv[0]):
                    cmd = [sys.executable] + cmd
                    worker = self._launcher.add_process(cmd)
                    self._ioutils.close_handle(rfd)

                    share_sock_buff = self._sock.share(worker.pid)
                    self._ioutils.write_file(
                        wfd,
                        struct.pack('<I', len(share_sock_buff)),
                        4)
                    self._ioutils.write_file(
                        wfd, share_sock_buff, len(share_sock_buff))

            self._launcher.wait()


if __name__ == '__main__':
    configure_logging()

    if args.pipe_handle:
        pipe_handle = int(args.pipe_handle)
        ioutils = os_win_utilsfactory.get_ioutils()
        buff = ioutils.get_buffer(4)
        ioutils.read_file(pipe_handle, buff, 4)
        socket_buff_sz = struct.unpack('<I', buff)[0]
        socket_buff = ioutils.get_buffer(socket_buff_sz)
        ioutils.read_file(pipe_handle, socket_buff, socket_buff_sz)
        ioutils.close_handle(pipe_handle)

        sock = socket.fromshare(bytes(socket_buff[:]))
        worker_count = 0
    else:
        sock = None
        worker_count = WORKER_COUNT

    sv = Server(app, worker_count, sock)
    sv.start()
