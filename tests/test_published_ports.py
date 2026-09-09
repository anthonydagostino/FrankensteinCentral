"""Only the gateway may be reachable from the network.

WHY THIS FILE EXISTS (SCRUM-116): every one of the seventeen services published
a host port — 8080 through 8099, plus Postgres on 5432 — so the gateway's role
as the single front door was a diagram, not a control. Every "internal" service
contract was one direct request away from anything on the LAN, and the database
holding gym history, captures, calendar events, net worth and budgets was one
`psql -h <box> -U frank` away if the shipped default password was kept.

Binding to 127.0.0.1 keeps every port usable ON the box — which is where the
docs, `scripts/verify.sh` and the Gmail OAuth flow all use them — while
removing them from the network.

WHY A TEST AND NOT JUST THE FIX: this is one character per line in a file that
gains a service every few weeks, and the failure is invisible. A new service
with a bare `"8093:8000"` looks exactly like the sixteen correct ones, works
perfectly, and quietly puts itself back on the LAN. Nothing else in the suite
reads this file.

WHAT THIS CANNOT PROVE: that the box is safe. Docker writes published ports
into its own iptables chain, which bypasses a host `ufw` policy, so the bind
address is the control — but a control in the file is not a control on the
machine until the stack is recreated. It also says nothing about anyone already
on the LAN reaching the gateway itself; that is SCRUM-98.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"

# The gateway is the front door and must answer on the network. It is the only
# one. Adding a name here is a decision to expose a service to the LAN, so it
# should be hard to do by accident and obvious in review.
NETWORK_FACING = {"gateway"}


def published_ports():
    """(service, mapping) for every published port, in file order."""
    svc, out = None, []
    for line in COMPOSE.read_text().split("\n"):
        m = re.match(r"^  ([a-z0-9_-]+):\s*$", line)
        if m:
            svc = m.group(1)
        m2 = re.match(r'^\s+-\s*"([^"]+)"\s*$', line)
        if m2 and svc and re.match(r"^[\d.]+:\d+:\d+$|^\d+:\d+$", m2.group(1)):
            out.append((svc, m2.group(1)))
    return out


PORTS = published_ports()


def test_the_scan_found_the_ports():
    """A scan that silently matches nothing passes forever. This file is the
    only thing reading compose, so an empty result must fail loudly."""
    assert len(PORTS) >= 15, f"only found {len(PORTS)} published ports"
    assert any(s == "gateway" for s, _ in PORTS)
    assert any(s == "db" for s, _ in PORTS)


@pytest.mark.parametrize("svc,mapping", PORTS, ids=[f"{s}:{m}" for s, m in PORTS])
def test_only_the_gateway_is_published_to_the_network(svc, mapping):
    if svc in NETWORK_FACING:
        assert not mapping.startswith("127.0.0.1:"), (
            f"{svc} is the front door and must stay reachable from the LAN")
        return
    assert mapping.startswith("127.0.0.1:"), (
        f"{svc} publishes {mapping!r} — reachable from every device on the "
        f"network. Bind it to loopback: \"127.0.0.1:{mapping}\". If this "
        f"service really must answer on the LAN, add it to NETWORK_FACING "
        f"in this file, which makes that a decision someone made rather than "
        f"a line someone copied.")


def test_postgres_is_published_but_only_to_the_box():
    """Both halves matter, and they pull in opposite directions.

    Not on the LAN: the database holds everything personal in the system.

    Still published: `scripts/backup.sh` runs `pg_dump -h localhost -p 5432`
    from the HOST, not through compose. SCRUM-116 proposed removing this
    publish entirely; that would have broken backup and restore silently while
    closing a security hole. Loopback satisfies both.
    """
    db = [m for s, m in PORTS if s == "db"]
    assert db == ["127.0.0.1:5432:5432"], db
    backup = (ROOT / "scripts" / "backup.sh").read_text()
    assert "POSTGRES_HOST:-localhost" in backup and "pg_dump" in backup, (
        "backup.sh no longer dumps over localhost — if it moved to `docker "
        "compose exec`, this publish can be dropped entirely and this test "
        "should say so")
