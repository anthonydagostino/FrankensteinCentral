#!/usr/bin/env python3
"""Host side of the worker's narrow egress channel.

The Claude child runs in a private network namespace with no route anywhere,
so its only way out is a UNIX socket bind-mounted into its scratch home. This
process owns the other end of that socket and speaks the HTTP CONNECT protocol
over it, refusing anything that is not on the allowlist.

Two independent checks, because a name is not an address:

  * the requested host:port must be on the allowlist, and
  * every address that host resolves to must be a global unicast address.

The second is what stops a DNS answer pointing at 127.0.0.1, the OptiPlex's own
LAN address, a link-local/metadata range, or an IPv6 loopback/ULA/link-local
address from becoming a tunnel into the house.

Nothing here is reachable from the network: it listens on a UNIX socket only.
"""
import argparse
import ipaddress
import os
import selectors
import socket
import stat
import sys
import threading

BUFSIZE = 65536


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def address_is_global(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
        return False
    if ip.is_reserved or ip.is_unspecified:
        return False
    # IPv6 unique-local (fc00::/7) is covered by is_private; be explicit anyway
    if ip.version == 6 and ip.packed[0] & 0xFE == 0xFC:
        return False
    return ip.is_global


def resolve_global(host: str, port: int):
    """Every resolved address must be global, or the target is refused."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    addrs = [i[4][0] for i in infos]
    if not addrs:
        raise ValueError("no addresses")
    bad = [a for a in addrs if not address_is_global(a)]
    if bad:
        raise ValueError("resolves to non-global address(es): %s" % ", ".join(bad))
    return infos


def handle(conn, allow):
    conn.settimeout(30)
    try:
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = conn.recv(BUFSIZE)
            if not chunk:
                return
            header += chunk
            if len(header) > 16384:
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
        request = header.split(b"\r\n", 1)[0].decode("latin-1")
        parts = request.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            log("egress: refused non-CONNECT request %r" % request[:120])
            return
        target = parts[1]
        if target.count(":") != 1:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        host, _, port_s = target.partition(":")
        host = host.strip("[]").lower()
        try:
            port = int(port_s)
        except ValueError:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        if "%s:%d" % (host, port) not in allow:
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            log("egress: REFUSED %s:%d (not on the allowlist)" % (host, port))
            return
        try:
            infos = resolve_global(host, port)
        except Exception as exc:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            log("egress: REFUSED %s:%d — %s" % (host, port, exc))
            return
        upstream = None
        for family, socktype, proto, _canon, sockaddr in infos:
            try:
                upstream = socket.socket(family, socktype, proto)
                upstream.settimeout(30)
                upstream.connect(sockaddr)
                break
            except OSError:
                if upstream:
                    upstream.close()
                upstream = None
        if upstream is None:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        log("egress: allowed %s:%d" % (host, port))
        pump(conn, upstream)
    except (OSError, ValueError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def pump(a, b):
    a.settimeout(None)
    b.settimeout(None)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--allow", required=True,
                    help="comma-separated host:port targets")
    args = ap.parse_args()
    allow = {t.strip().lower() for t in args.allow.split(",") if t.strip()}
    if not allow:
        log("egress: empty allowlist — nothing would be permitted")
        return 2
    # AF_UNIX paths are capped near 108 bytes, and an agent directory can sit
    # deeper than that. Bind relative to the socket's own directory instead.
    directory, name = os.path.split(os.path.abspath(args.socket))
    os.chdir(directory)
    try:
        os.unlink(name)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(name)
    os.chmod(name, stat.S_IRUSR | stat.S_IWUSR)
    srv.listen(16)
    log("egress: ready on %s; allowlist: %s" % (args.socket, ", ".join(sorted(allow))))
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=handle, args=(conn, allow), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
