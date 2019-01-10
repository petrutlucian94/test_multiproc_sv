import logging
import socket
import os
import sys

import multiprocessing

import eventlet
import eventlet.wsgi

eventlet.monkey_patch(os=False)

multiprocessing.allow_connection_pickling()

LOG = logging.getLogger()

EVENTLET_DEBUG = False
BIND_ADDR = "127.0.0.1"
BIND_PORT = 1234
WORKER_COUNT = 2

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
    start_response('200 OK', [('Content-Type', 'text/plain')])

    response = ['Test WSGI.\r\nWorker: %s' % os.getpid()]
    LOG.info("Returning response: %s", response)
    return response

def listen(addr, family=socket.AF_INET, backlog=50):
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform[:3] != "win":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        # NOTE(zhengwei): linux kernel >= 3.9
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(addr)
    sock.listen(backlog)
    return sock


class Server(object):
    def __init__(self, app, worker_count=0):
        self._app = app
        self._worker_count = worker_count
        self._workers = []

        self._config_socket()

    def _config_socket(self):
        addr = (BIND_ADDR, BIND_PORT)
        self._sock = listen(addr, family=socket.AF_INET)

    def serve(self):
        eventlet.wsgi.server(self._sock, self._app, log=LOG,
                             debug=EVENTLET_DEBUG)

    def start(self):
        if not self._worker_count:
            self.serve()
        else:
            for idx in range(self._worker_count):
                LOG.info("Starting worker: %s", idx)
                worker = multiprocessing.Process(target=self.serve)
                worker.start()
                self._workers.append(worker)
            for worker in self._workers:
                worker.join()


if __name__ == '__main__':
    configure_logging()
    sv = Server(app, WORKER_COUNT)
    sv.start()
