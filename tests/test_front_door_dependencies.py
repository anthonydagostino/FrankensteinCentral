"""What the front door is allowed to wait for.

WHY THIS FILE EXISTS. On 2026-09-16 the whole dashboard went down because a
statement-credits tracker would not start. Nothing was wrong with the gateway:
`gateway` depends on `assistant`, and `assistant` had just been given
`depends_on: amex`, so a service that had never once started on that box sat
between the browser and every other feature. Compose starts dependencies
first; the new one failed; the gateway never came back.

The dashboard already knew how to survive this. `amex_brief` returns
`state: "unreachable"` and the card says so — the UI's answer to a dead service
is one honest card, not a blank site. `depends_on` overrode that and turned a
degraded card into an outage.

THE RULE. Everything in the front door's dependency closure is something the
site cannot serve without. That is a real category — the gateway proxies to
`assistant`, which is the page — but it is a category you join deliberately,
not by adding a line that looks like the fifteen lines above it.

WHAT THIS CAN AND CANNOT PROVE. It reads docker-compose.yml, so it proves what
the stack will WAIT for. It cannot prove any service starts: nothing in this
suite can, because none of it runs a container, and that is exactly the gap the
outage fell through. A service can be absent from this closure and still be
broken — it will just be broken by itself.

No yaml import on purpose: scripts/test.sh installs pytest, fastapi, httpx and
uvicorn, and a guard that vanishes when an optional dependency is missing is
worse than no guard. Same reason tests/test_published_ports.py scans by hand.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"

# Services the site genuinely cannot serve without. `assistant` IS the page the
# gateway renders; `db` and the sub-apps below were in this closure before the
# outage and stay by inspection, not by endorsement.
#
# Adding a name here says: if this service does not start, the dashboard should
# not serve at all. That is almost never true of a feature. If the UI can render
# an "unreachable" state for it — and for most of these it can — it belongs
# outside this set, reachable by URL and absent from depends_on.
FRONT_DOOR_CLOSURE = {
    "assistant", "db", "core", "fitness", "tasks", "gmail", "schedule",
    "powerbuy", "finance", "budget", "deals", "networth", "vault", "plex",
    "firefly", "stocks",
}


def depends_on_graph():
    """{service: {dependency, ...}} for both depends_on spellings.

    Compose accepts a list (`- powerbuy`) and a mapping with conditions
    (`powerbuy:\\n  condition: service_started`). The gateway uses the first
    and the assistant the second, so a scan that understood only one would
    have read the gateway as depending on nothing at all.
    """
    graph, svc, in_deps = {}, None, False
    for line in COMPOSE.read_text().split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = re.match(r"^  ([a-z0-9_-]+):\s*$", line)
        if top:
            svc, in_deps = top.group(1), False
            graph.setdefault(svc, set())
            continue
        if svc is None:
            continue
        if re.match(r"^    depends_on:\s*$", line):
            in_deps = True
            continue
        if in_deps:
            item = (re.match(r"^      -\s*([a-z0-9_-]+)\s*$", line)
                    or re.match(r"^      ([a-z0-9_-]+):\s*$", line))
            if item:
                graph[svc].add(item.group(1))
            elif not line.startswith("        "):
                in_deps = False
    return graph


GRAPH = depends_on_graph()


def closure(start):
    seen, stack = set(), [start]
    while stack:
        for dep in GRAPH.get(stack.pop(), set()):
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def test_the_scan_understood_the_file():
    """A parser that silently matches nothing turns every assertion below into
    a tautology — and this one has two spellings to get wrong."""
    assert len(GRAPH) >= 15, f"only found {len(GRAPH)} services: {sorted(GRAPH)}"
    assert "assistant" in GRAPH["gateway"], "list-form depends_on went unread"
    assert "db" in GRAPH["assistant"], "mapping-form depends_on went unread"


def test_the_front_door_waits_for_nothing_new_by_accident():
    added = closure("gateway") - FRONT_DOOR_CLOSURE
    assert added == set(), (
        f"{sorted(added)} joined the gateway's dependency chain, so the site "
        "will not serve unless each one starts. If the dashboard can render an "
        "'unreachable' state for it, drop it from depends_on and reach it by "
        "URL instead — that is one honest card rather than a blank page. If it "
        "really is load-bearing, add it to FRONT_DOOR_CLOSURE and say why.")


def test_amex_is_not_load_bearing():
    """The specific regression, pinned. Credits expiring is a card; it is not
    a reason to stop serving the calendar, the money section and the stocks."""
    assert "amex" not in closure("gateway"), (
        "the gateway can no longer start without the Amex tracker — this is "
        "the exact shape of the 2026-09-16 outage")
    assert "amex" in GRAPH, "amex left the compose file entirely"
