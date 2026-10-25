"""Remote SSH session helper: private socket permissions and stale-socket cleanup."""
import http.client
import json
import os
from pathlib import Path
import signal
import select
import socket
import stat
import sys

DIRECTORY = Path('/root/alfa-tatari/cluster/acme-webroot/.local-inference')
SOCKET = DIRECTORY / 'api.sock'
NGINX_GID = 101


def probe():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(SOCKET))
        connection.sendall(b'GET /v1/models HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status == 401, response.status
    print(json.dumps({'ready': True, 'without_key': 401}), flush=True)


def prepare():
    DIRECTORY.mkdir(exist_ok=True)
    os.chown(DIRECTORY, 0, NGINX_GID)
    os.chmod(DIRECTORY, 0o750)
    try:
        metadata = SOCKET.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError('Refusing to replace a non-socket path')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as test:
        test.settimeout(2)
        try:
            test.connect(str(SOCKET))
        except ConnectionRefusedError:
            SOCKET.unlink()
            return
    raise RuntimeError('An SSH listener already owns the inference socket')


def hold():
    metadata = SOCKET.lstat()
    assert stat.S_ISSOCK(metadata.st_mode)
    os.chown(SOCKET, 0, NGINX_GID)
    os.chmod(SOCKET, 0o660)
    print('Private inference socket ready', flush=True)

    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        # A lease also expires when a dead network leaves sshd holding an old listener.
        while True:
            readable, _, _ = select.select([sys.stdin.fileno()], [], [], 30)
            if not readable:
                print('Inference SSH heartbeat expired; releasing socket', flush=True)
                break
            if not os.read(sys.stdin.fileno(), 1024):
                break
    finally:
        try:
            if SOCKET.lstat().st_ino == metadata.st_ino:
                SOCKET.unlink()
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    {'prepare': prepare, 'hold': hold, 'probe': probe}[sys.argv[1]]()
