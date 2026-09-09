"""The token route asks for a credential now (SCRUM-114).

`/internal/token` returns a live Google OAuth access token carrying
gmail.modify AND calendar.events — read and modify the whole inbox, read and
write the calendar. What protected it was a docstring saying "internal docker
network only". Nothing enforced that, and the gateway forwarded anything to
anything, so an unauthenticated GET to the dashboard's own front door handed
the token over.

The gateway now refuses `internal` segments, which closes the reported route.
This file covers the SECOND lock, and the second lock is the point: gmail is
also directly reachable on its published port 8083, so a fix that lives only
in the proxy leaves the token one `curl` away from anyone on the network.

The rule under test is that an unset secret DISABLES the route rather than
disabling the check. "No secret configured" must never come to mean "no secret
required" — that is the original bug, reintroduced one layer down.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

gm = load_service_module("gmail_main_secret", "services/gmail/app/main.py")

SECRET = "s3cr3t-shared-value"
TOKEN = "ya29.a0-LIVE-ACCESS-TOKEN"


@pytest.fixture
def client(monkeypatch):
    async def fake_token():
        return TOKEN
    monkeypatch.setattr(gm, "_access_token", fake_token)
    monkeypatch.setattr(gm, "granted_scopes", lambda: "gmail.modify calendar.events")
    monkeypatch.setattr(gm, "has_calendar_scope", lambda: True)
    return TestClient(gm.app)


def configured(monkeypatch, secret=SECRET):
    monkeypatch.setattr(gm, "INTERNAL_SECRET", secret)


# ---- the hole ----------------------------------------------------------------

def test_without_the_secret_the_route_hands_out_nothing(client, monkeypatch):
    configured(monkeypatch)
    r = client.get("/internal/token")
    assert r.status_code == 404
    assert TOKEN not in r.text


@pytest.mark.parametrize("wrong", ["", "nope", SECRET + "x", SECRET[:-1],
                                   SECRET.upper(), " " + SECRET])
def test_a_wrong_secret_hands_out_nothing(client, monkeypatch, wrong):
    configured(monkeypatch)
    r = client.get("/internal/token", headers={"X-Internal-Secret": wrong})
    assert r.status_code == 404, wrong
    assert TOKEN not in r.text


def test_the_right_secret_still_works(client, monkeypatch):
    """The schedule service has to keep being able to push to Calendar."""
    configured(monkeypatch)
    r = client.get("/internal/token", headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200
    assert r.json()["access_token"] == TOKEN
    assert r.json()["calendar_scope"] is True


# ---- unset means closed, not open --------------------------------------------

@pytest.mark.parametrize("unset", ["", None])
def test_an_unconfigured_secret_disables_the_route_entirely(client, monkeypatch, unset):
    """The direction that matters. Treating "no secret configured" as "no
    secret required" would reintroduce the whole bug behind a check that looks
    like a fix."""
    monkeypatch.setattr(gm, "INTERNAL_SECRET", unset or "")
    assert client.get("/internal/token").status_code == 404
    # And it cannot be unlocked by guessing the empty value.
    for attempt in ("", " ", "None", "null"):
        r = client.get("/internal/token", headers={"X-Internal-Secret": attempt})
        assert r.status_code == 404, attempt
        assert TOKEN not in r.text


# ---- what a refusal reveals ---------------------------------------------------

def test_a_refusal_says_nothing_about_the_route_or_the_secret(client, monkeypatch):
    configured(monkeypatch)
    r = client.get("/internal/token", headers={"X-Internal-Secret": "wrong"})
    body = r.text.lower()
    for leak in ("secret", "unauthor", "forbidden", "token", SECRET.lower()):
        assert leak not in body, f"the refusal leaks {leak!r}: {r.text}"


def test_the_comparison_is_constant_time(monkeypatch):
    """A plain == leaks the secret's prefix through timing to anyone who can
    call this in a loop, which is everyone on the network. Asserted on the
    source because timing itself is far too noisy to test reliably."""
    src = Path("services/gmail/app/main.py").read_text()
    body = src[src.index("async def internal_token"):]
    body = body[:body.index("\n@app.")] if "\n@app." in body else body
    assert "compare_digest" in body, (
        "the secret is compared with ==, which is timing-attackable")
