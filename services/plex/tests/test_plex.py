"""The Plex sub-app, which until now was the one service with no tests at all.

WHY THIS FILE EXISTS: every other service in this repo has a suite; plex had
none, so nothing stopped a change here from taking out the card silently. It is
also the service with the most upstream it does not control — a server on
SOMEBODY ELSE'S network, discovered through plex.tv, reachable by any of
several advertised connections, most of which will not answer.

So the behaviour worth pinning is mostly about degrading:

  * the account token never reaches the browser — by any route, including the
    error path, which is the one place a leak would hide;
  * a shared (non-owner) token cannot see everything, and a panel that could
    not be read must not come back as a panel that is empty;
  * discovery prefers a direct connection and only falls back to a relay,
    tries each until one answers, and caches the winner;
  * not connected is a STATE, not a failure — it renders an empty card and the
    launch button still works, because Plex is openable whether or not this
    sub-app can read it.

Nothing here reaches the network: httpx.AsyncClient is replaced wholesale, and
`test_a_disconnected_service_makes_no_network_calls_at_all` fails if any code
path tries.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

px = load_service_module("plex_main", "services/plex/app/main.py")

TOKEN = "plex-token-that-must-never-be-rendered"
MACHINE = "abc123machine"


# --- a fake Plex ------------------------------------------------------------

class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _sections(*titles):
    return _Resp(200, {"MediaContainer": {"Directory": [
        {"title": t, "type": "movie", "key": str(i + 1)}
        for i, t in enumerate(titles)]}})


def _metadata(*items):
    return _Resp(200, {"MediaContainer": {"Metadata": list(items)}})


class FakePlex:
    """Answers the handful of paths the service asks for.

    `routes` maps a path fragment to a _Resp or to a callable raising one.
    Anything unrouted 404s, so a test cannot pass by accident on a path the
    service never actually requested.
    """

    def __init__(self, routes=None, fail_hosts=()):
        self.routes = routes or {}
        self.fail_hosts = set(fail_hosts)
        self.asked = []
        self.headers_seen = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):
        self.asked.append(url)
        self.headers_seen.append(headers or {})
        for host in self.fail_hosts:
            if host in url:
                raise ConnectionError(f"{host} refused")
        for fragment, resp in self.routes.items():
            if fragment in url:
                return resp() if callable(resp) else resp
        return _Resp(404, {})


class Exploding:
    """Any network call at all is the failure this asserts against."""

    async def __aenter__(self):
        raise AssertionError("the service made a network call it should not have")

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """The discovered-server cache is a module global; a leak between tests
    would make one test pass because another one ran first."""
    px._SERVER.clear()
    monkeypatch.setattr(px, "PLEX_TOKEN", TOKEN)
    monkeypatch.setattr(px, "PLEX_URL", "")
    monkeypatch.setattr(px, "PLEX_WEB_URL", "")
    monkeypatch.setattr(px, "PLEX_SERVER_NAME", "")
    yield
    px._SERVER.clear()


@pytest.fixture
def plex(monkeypatch):
    def install(fake):
        monkeypatch.setattr(px.httpx, "AsyncClient", lambda *a, **k: fake)
        return fake
    return install


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def client():
    return TestClient(px.app)


def _full_server(base="http://direct:32400"):
    """A fake that answers everything a healthy read needs."""
    return FakePlex({
        "/identity": _Resp(200, {"MediaContainer": {"machineIdentifier": MACHINE}}),
        "/library/sections/1/all": _Resp(200, {"MediaContainer": {"totalSize": 412}}),
        "/library/sections/2/all": _Resp(200, {"MediaContainer": {"totalSize": 77}}),
        "/library/sections": _sections("Movies", "TV"),
        "/library/onDeck": _metadata(
            {"type": "episode", "grandparentTitle": "The Wire", "parentIndex": 3,
             "index": 7, "title": "Back Burners", "viewOffset": 300_000,
             "duration": 1_200_000, "ratingKey": "9001"}),
        "/library/recentlyAdded": _metadata(
            {"type": "movie", "title": "Heat", "ratingKey": "9002"}),
        "resources": _Resp(200, [{
            "name": "Home Server", "provides": "server",
            "clientIdentifier": MACHINE,
            "connections": [{"uri": base, "relay": False, "local": False}]}]),
    })


# --- the token ---------------------------------------------------------------

def test_the_token_never_reaches_the_browser(plex):
    """The promise in the module docstring, asserted on every endpoint rather
    than trusted. The token is what grants access to someone else's server."""
    plex(_full_server())
    c = client()
    for path in ("/summary", "/dashboard", "/health", "/"):
        body = c.get(path).text
        assert TOKEN not in body, path
        assert "X-Plex-Token" not in body, path


def test_the_token_does_not_leak_through_the_error_path_either(plex):
    """`/summary` returns the exception text on failure. An upstream library
    that put the token in a message would publish it through that hole, and
    the error path is exactly where nobody looks."""
    plex(FakePlex({"resources": lambda: (_ for _ in ()).throw(
        RuntimeError(f"connect failed for token={TOKEN}"))}))
    r = client().get("/summary")
    assert r.status_code == 502
    assert TOKEN not in r.text


def test_the_token_is_sent_as_a_header_not_in_the_url(plex):
    """A token in a query string lands in access logs and in httpx's own
    exception messages, which this service hands to callers."""
    fake = plex(_full_server())
    run(px._data())
    assert fake.asked, "nothing was requested"
    assert all(TOKEN not in url for url in fake.asked)
    assert all(h.get("X-Plex-Token") == TOKEN for h in fake.headers_seen)


# --- not connected is a state, not a failure --------------------------------

def test_a_disconnected_service_makes_no_network_calls_at_all(plex, monkeypatch):
    monkeypatch.setattr(px, "PLEX_TOKEN", "")
    plex(Exploding())
    c = client()
    assert c.get("/summary").status_code == 200
    assert c.get("/dashboard").status_code == 200
    assert c.get("/health").json()["connected"] is False


def test_a_disconnected_dashboard_is_empty_and_says_so(plex, monkeypatch):
    monkeypatch.setattr(px, "PLEX_TOKEN", "")
    plex(Exploding())
    d = client().get("/dashboard").json()
    assert d["connected"] is False
    assert d["libraries"] == [] and d["continue"] == [] and d["recent"] == []


def test_the_launch_button_works_even_when_the_sub_app_does_not(plex, monkeypatch):
    """Plex is openable whether or not this service can read it, so the button
    must never be missing — a dead button reads as a broken account."""
    monkeypatch.setattr(px, "PLEX_TOKEN", "")
    plex(Exploding())
    assert client().get("/summary").json()["web_url"].startswith("https://app.plex.tv")


def test_a_disconnected_read_never_hands_out_the_shared_empty_dict(plex, monkeypatch):
    """EMPTY is a module-level dict reused by every disconnected response, and
    `dashboard` mutates what `_data` returns (it pops `machine`). Handing back
    the global itself works right up until something pops or appends, and then
    every later response in the life of the container is wrong.

    Asserted by identity, not by comparing values afterwards: today `dashboard`
    only pops a key EMPTY does not have, so a value comparison passes on the
    broken version too — which is exactly how this kind of bug survives a
    test that looks like it covers it.
    """
    monkeypatch.setattr(px, "PLEX_TOKEN", "")
    plex(Exploding())
    first, second = run(px._data()), run(px._data())
    assert first is not px.EMPTY, "callers were handed the module-level dict"
    assert first is not second
    before = {k: list(v) if isinstance(v, list) else v for k, v in px.EMPTY.items()}
    c = client()
    c.get("/dashboard"); c.get("/dashboard"); c.get("/summary")
    assert px.EMPTY == before and "machine" not in px.EMPTY


# --- discovery ---------------------------------------------------------------

def test_an_explicit_url_skips_plex_tv_entirely(plex, monkeypatch):
    monkeypatch.setattr(px, "PLEX_URL", "http://192.168.1.9:32400")
    fake = plex(_full_server())
    base, machine, _ = run(px._discover(fake))
    assert base == "http://192.168.1.9:32400"
    assert machine == MACHINE
    assert not any("plex.tv" in u for u in fake.asked)


def test_an_explicit_url_that_does_not_answer_is_not_papered_over(plex, monkeypatch):
    """Falling back to discovery here would silently ignore the operator's
    override and connect to something they did not ask for."""
    monkeypatch.setattr(px, "PLEX_URL", "http://192.168.1.9:32400")
    fake = plex(FakePlex(fail_hosts={"192.168.1.9"}))
    assert run(px._discover(fake)) is None
    assert not any("plex.tv" in u for u in fake.asked)


def test_a_direct_connection_is_tried_before_a_relay(plex):
    """A relay is Plex's slow last resort — correct as a fallback, wrong as a
    first choice, and both answer, so only the ORDER distinguishes them."""
    fake = plex(FakePlex({
        "/identity": _Resp(200, {"MediaContainer": {"machineIdentifier": MACHINE}}),
        "resources": _Resp(200, [{
            "name": "Home Server", "provides": "server",
            "connections": [
                {"uri": "http://relay", "relay": True, "local": False},
                {"uri": "http://direct", "relay": False, "local": False},
            ]}]),
    }))
    base, _, _ = run(px._discover(fake))
    assert base == "http://direct"


def test_the_first_connection_that_answers_wins(plex):
    """Advertised connections are mostly unreachable from any given network —
    a dead one must cost a try, not the whole discovery."""
    fake = plex(FakePlex({
        "/identity": _Resp(200, {"MediaContainer": {"machineIdentifier": MACHINE}}),
        "resources": _Resp(200, [{
            "name": "Home Server", "provides": "server",
            "connections": [
                {"uri": "http://dead-one", "relay": False, "local": False},
                {"uri": "http://dead-two", "relay": False, "local": False},
                {"uri": "http://alive", "relay": False, "local": False},
            ]}]),
    }, fail_hosts={"dead-one", "dead-two"}))
    base, _, _ = run(px._discover(fake))
    assert base == "http://alive"


def test_nothing_reachable_is_reported_as_nothing(plex):
    fake = plex(FakePlex({
        "resources": _Resp(200, [{
            "name": "Home Server", "provides": "server",
            "connections": [{"uri": "http://dead", "relay": False, "local": False}]}]),
    }, fail_hosts={"dead"}))
    assert run(px._discover(fake)) is None


def test_plex_tv_being_unreachable_is_reported_as_nothing(plex):
    fake = plex(FakePlex(fail_hosts={"plex.tv"}))
    assert run(px._discover(fake)) is None


def test_things_that_are_not_servers_are_ignored(plex):
    """plex.tv lists every resource on the account — players and controllers
    included. Asking a phone for a library listing gets nowhere."""
    fake = plex(FakePlex({
        "/identity": _Resp(200, {"MediaContainer": {"machineIdentifier": MACHINE}}),
        "resources": _Resp(200, [
            {"name": "Anthony's iPhone", "provides": "client,player",
             "connections": [{"uri": "http://phone", "relay": False, "local": True}]},
            {"name": "Home Server", "provides": "server",
             "connections": [{"uri": "http://server", "relay": False, "local": False}]},
        ]),
    }))
    base, _, name = run(px._discover(fake))
    assert base == "http://server" and name == "Home Server"


def test_a_named_server_is_picked_out_of_several(plex, monkeypatch):
    monkeypatch.setattr(px, "PLEX_SERVER_NAME", "Second")
    fake = plex(FakePlex({
        "/identity": _Resp(200, {"MediaContainer": {"machineIdentifier": MACHINE}}),
        "resources": _Resp(200, [
            {"name": "First", "provides": "server",
             "connections": [{"uri": "http://first", "relay": False, "local": False}]},
            {"name": "Second", "provides": "server",
             "connections": [{"uri": "http://second", "relay": False, "local": False}]},
        ]),
    }))
    base, _, name = run(px._discover(fake))
    assert base == "http://second" and name == "Second"


def test_the_name_filter_is_not_case_sensitive(monkeypatch, plex):
    """It is typed into .env by hand; "plex server" and "Plex Server" are the
    same server, and a silent empty dashboard is a poor way to say otherwise."""
    monkeypatch.setattr(px, "PLEX_SERVER_NAME", "hOmE sErVeR")
    fake = plex(_full_server())
    assert run(px._discover(fake))[2] == "Home Server"


def test_a_name_that_matches_nothing_still_finds_a_server(monkeypatch, plex):
    """Deliberate: a typo'd or renamed server should not take the card down
    when exactly one server is shared with you anyway."""
    monkeypatch.setattr(px, "PLEX_SERVER_NAME", "server-that-was-renamed")
    fake = plex(_full_server())
    assert run(px._discover(fake))[2] == "Home Server"


# --- the cache ---------------------------------------------------------------

def test_discovery_is_not_repeated_on_every_request(plex):
    """Discovery is two round trips through plex.tv before anything is read.
    Paying that per dashboard refresh would be most of the page's latency."""
    fake = plex(_full_server())
    run(px._data())
    first = len([u for u in fake.asked if "plex.tv" in u])
    run(px._data())
    assert first == 1
    assert len([u for u in fake.asked if "plex.tv" in u]) == 1


def test_the_cache_expires(plex, monkeypatch):
    """A shared server's address changes — the owner's IP moves, a relay is
    withdrawn — so a cached answer must not be permanent."""
    fake = plex(_full_server())
    run(px._data())
    px._SERVER["at"] = time.time() - px._SERVER_TTL - 1
    run(px._data())
    assert len([u for u in fake.asked if "plex.tv" in u]) == 2


# --- degrading ---------------------------------------------------------------

def test_a_panel_that_could_not_be_read_comes_back_empty_not_missing(plex):
    """A shared (non-owner) token is refused for some reads. The card shows
    what it could get; the rest of the dashboard must not 502 over it."""
    fake = _full_server()
    fake.routes["/library/onDeck"] = _Resp(401, {})
    fake.routes["/library/recentlyAdded"] = _Resp(403, {})
    plex(fake)
    d = client().get("/dashboard").json()
    assert d["continue"] == [] and d["recent"] == []
    assert [lib["title"] for lib in d["libraries"]] == ["Movies", "TV"]


def test_one_library_refusing_a_count_does_not_cost_the_others(plex):
    """`count: None` is the honest answer for a library that would not say —
    and it is not zero, which would read as an empty library.

    The refusal carries a totalSize of its own, because Plex answers a refused
    read with a MediaContainer too (`size: 0`, the size of the error). Reading
    the body without checking the status turns "I was not allowed to count
    this" into a confident zero — so the stub hands back a number that must
    NOT appear.
    """
    fake = _full_server()
    fake.routes["/library/sections/2/all"] = _Resp(
        401, {"MediaContainer": {"size": 0, "totalSize": 0}})
    plex(fake)
    libs = client().get("/dashboard").json()["libraries"]
    assert [lib["count"] for lib in libs] == [412, None]


def test_a_library_that_could_not_be_counted_is_unknown_and_not_zero(plex):
    """Same rule the money layer is built on: zero and unknown are different
    facts, and only one of them is a reason to go and look at the library."""
    fake = _full_server()
    fake.routes["/library/sections/1/all"] = _Resp(
        503, {"MediaContainer": {"totalSize": 0}})
    plex(fake)
    counts = [lib["count"] for lib in client().get("/dashboard").json()["libraries"]]
    assert counts[0] is None and counts[0] != 0


def test_a_library_listing_that_fails_takes_the_endpoint_down(plex):
    """Deliberately NOT degraded: the section list is the spine of the payload.
    An empty library list would render as "this server has no media"."""
    fake = _full_server()
    fake.routes["/library/sections"] = _Resp(500, {})
    plex(fake)
    assert client().get("/dashboard").status_code == 502


def test_an_unreachable_server_is_502_and_not_an_empty_card(plex):
    """The distinction this repo keeps making: nothing found is not the same
    answer as nothing there."""
    plex(FakePlex(fail_hosts={"plex.tv"}))
    for path in ("/summary", "/dashboard"):
        r = client().get(path)
        assert r.status_code == 502, path
        assert "unreachable" in r.json()["error"], path


# --- what the cards actually render -----------------------------------------

@pytest.mark.parametrize("item, expected", [
    ({"type": "episode", "grandparentTitle": "The Wire", "parentIndex": 3,
      "index": 7, "title": "Back Burners"}, 'The Wire — S3E7 “Back Burners”'),
    # Season 0 is the specials season and episode numbering can start at 0.
    # A truthiness check here would drop the tag from exactly those.
    ({"type": "episode", "grandparentTitle": "Firefly", "parentIndex": 0,
      "index": 0, "title": "Pilot"}, 'Firefly — S0E0 “Pilot”'),
    ({"type": "episode", "grandparentTitle": "Show", "title": "No numbers"},
     'Show “No numbers”'),
    ({"type": "movie", "title": "Heat"}, "Heat"),
    ({"type": "movie"}, ""),
])
def test_an_item_is_labelled_the_way_you_would_say_it(item, expected):
    assert px._label(item) == expected


@pytest.mark.parametrize("offset, duration, expected", [
    (300_000, 1_200_000, 25),
    (1_200_000, 1_200_000, 100),
    (2_000_000, 1_200_000, 100),   # past the end: clamped, never 167%
    (None, 1_200_000, 0),
    (300_000, None, 0),
    (0, 1_200_000, 0),
])
def test_progress_is_a_percentage_that_stays_one(offset, duration, expected):
    assert px._pct({"viewOffset": offset, "duration": duration}) == expected


# --- the launch button -------------------------------------------------------

def test_the_button_deep_links_to_the_discovered_server(plex):
    plex(_full_server())
    assert client().get("/summary").json()["web_url"] == (
        f"https://app.plex.tv/desktop/#!/server/{MACHINE}")


def test_an_explicit_web_url_wins(monkeypatch, plex):
    """Same override firefly has: the internal address is usually not one a
    browser on the couch can reach."""
    monkeypatch.setattr(px, "PLEX_WEB_URL", "http://192.168.1.9:32400/web")
    plex(_full_server())
    assert client().get("/summary").json()["web_url"] == "http://192.168.1.9:32400/web"


def test_without_a_machine_id_the_button_still_opens_plex():
    assert px._web_url(None) == "https://app.plex.tv/desktop"
    assert px._web_url("") == "https://app.plex.tv/desktop"


def test_the_machine_id_is_not_published_in_the_dashboard(plex):
    """It is an internal identifier; the browser needs the link built FROM it,
    not the id itself."""
    plex(_full_server())
    d = client().get("/dashboard").json()
    assert "machine" not in d
    assert MACHINE in d["web_url"]


# --- the payload shape the dashboard reads ----------------------------------

def test_the_summary_keys_the_home_card_reads_are_all_there(plex):
    plex(_full_server())
    s = client().get("/summary").json()
    assert set(s) == {"server", "libraries", "continue_count", "web_url",
                      "mode", "connected"}
    assert s["server"] == "Home Server"
    assert s["libraries"] == 2 and s["continue_count"] == 1
    assert s["mode"] == "live" and s["connected"] is True


def test_the_dashboard_carries_the_items_not_just_counts(plex):
    plex(_full_server())
    d = client().get("/dashboard").json()
    assert d["continue"] == [
        {"name": 'The Wire — S3E7 “Back Burners”', "percent": 25, "id": "9001"}]
    assert d["recent"] == [{"name": "Heat", "type": "movie", "id": "9002"}]


def test_health_answers_without_touching_the_server(plex):
    """The gateway probes /health across every sub-app on one page load; a
    probe that hit plex.tv would cost the whole grid."""
    plex(Exploding())
    h = client().get("/health").json()
    assert h == {"service": "plex", "mode": "live", "connected": True}


def test_the_root_lists_what_this_service_serves(plex):
    plex(Exploding())
    r = client().get("/").json()
    assert set(r["endpoints"]) == {"/summary", "/dashboard", "/health"}
