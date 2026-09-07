#!/usr/bin/env python3
"""In-namespace half of the worker's narrow egress channel.

The child's network namespace has no route anywhere. This brings its PRIVATE
loopback up — isolated from the host's 127.0.0.1 — and listens there, pumping
each connection to the UNIX socket the host proxy owns. The child points
HTTPS_PROXY at it, so it can reach exactly what the proxy allows and nothing
else: not the OptiPlex's own services, not the LAN, not link-local.

`ip` is not assumed to exist; the interface is brought up with the ioctl
directly.
"""
import argparse
import fcntl
import os
import selectors
import socket
import struct
import sys
import threading

SIOCGIFFLAGS, SIOCSIFFLAGS, IFF_UP = 0x8913, 0x8914, 0x1
BUFSIZE = 65536


def bring_loopback_up():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        flags = struct.unpack('16sh', fcntl.ioctl(
            s, SIOCGIFFLAGS, struct.pack('16sh', b'lo', 0)))[1]
        fcntl.ioctl(s, SIOCSIFFLAGS, struct.pack('16sh', b'lo', flags | IFF_UP))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pump(a, b):
    sel = selectors.DefaultSelector()
    sel.register(a, selectors.EVENT_READ, b)
    sel.register(b, selectors.EVENT_READ, a)
    try:
        while True:
            events = sel.select(timeout=300)
            if not events:
                return
            for key, _ in events:
                try:
                    data = key.fileobj.recv(BUFSIZE)
                except OSError:
                    return
                if not data:
                    return
                try:
                    key.data.sendall(data)
                except OSError:
                    return
    finally:
        sel.close()
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def handle(conn, unix_name):
    try:
        up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        up.connect(unix_name)          # relative; cwd is the socket directory
    except OSError:
        conn.close()
        return
    pump(conn, up)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket")
    ap.add_argument("--port", type=int)
    ap.add_argument("--loopback-only", action="store_true",
                    help="bring the private loopback up and exit; used by the "
                         "invocations that get no egress at all")
    args = ap.parse_args()
    if not bring_loopback_up():
        print("relay: could not bring the private loopback up", file=sys.stderr)
        return 1
    if args.loopback_only:
        return 0
    if not args.socket or not args.port:
        print("relay: --socket and --port are required", file=sys.stderr)
        return 2
    # AF_UNIX paths are capped near 108 bytes; connect relative to the
    # socket's own directory so a deep agent directory still works.
    directory, name = os.path.split(os.path.abspath(args.socket))
    os.chdir(directory)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(16)
    print("relay: listening on 127.0.0.1:%d" % args.port, file=sys.stderr, flush=True)
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=handle, args=(conn, name), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
