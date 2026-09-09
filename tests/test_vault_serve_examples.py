"""Every `bw serve` example in the repo must point at the docker bridge.

WHY THIS FILE EXISTS: three places tell you how to set BW_SERVE_URL — the
service docstring, `.env.example`, and `docs/SETUP-VAULT.md`. Only the doc ever
had it right. The other two each shipped an example that was the exact shape the
doc exists to warn against, written by two different authors who never saw each
other's work, and nothing in the suite noticed either one.

WHAT THE WRONG EXAMPLE COSTS. `bw serve` is the Bitwarden CLI's local REST API
and, while unlocked, it answers `/list/object/items` with real credentials to
ANYONE who can reach the port — no auth. Bound to a LAN address it publishes
the whole vault to every device on the network; bound to 0.0.0.0 behind a
forwarded port, to the internet. It belongs on the docker bridge, where the host
and the hub's containers can reach it and nothing else can.

The port is the mundane half: 8087 is the CLI's default and Tasks already binds
`8087:8000` in docker-compose.yml, so copying the default gives you a clash.

This checks the INSTRUCTIONS, not the running system — a correct example is not
a correctly configured box. It is here because a wrong example is copied once
and lives for years, and because the failure is silent: an over-exposed vault
looks exactly like a working one.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BRIDGE = "172.17.0.1"
PORT = "8200"

# Text files a human might read an example out of. Binary, vendored and
# generated trees are skipped; this file is skipped because it necessarily
# contains the wrong values it exists to ban.
SUFFIXES = {".py", ".md", ".yml", ".yaml", ".sh", ".js", ".example", ".txt", ""}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "vw-data"}


def documents():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.resolve() == Path(__file__).resolve():
            continue
        if SKIP_DIRS & set(p.relative_to(ROOT).parts):
            continue
        if p.suffix not in SUFFIXES:
            continue
        try:
            yield p, p.read_text()
        except (UnicodeDecodeError, OSError):
            continue


DOCS = list(documents())
IDS = [str(p.relative_to(ROOT)) for p, _ in DOCS]

# A URL sitting on a line that also names BW_SERVE_URL is being offered as the
# value for it. Vaultwarden's own web vault (:8222) and the internal
# `http://vault:8000` are different things and are deliberately not matched.
URL = re.compile(r"https?://([A-Za-z0-9_.<>{}\[\]-]+):(\d+)")
SERVE_CMD = re.compile(r"^.*\bbw\s+serve\b.*$", re.MULTILINE)


@pytest.mark.parametrize("path,text", DOCS, ids=IDS)
def test_bw_serve_url_examples_use_the_docker_bridge(path, text):
    for lineno, line in enumerate(text.splitlines(), 1):
        if "BW_SERVE_URL" not in line:
            continue
        for host, port in URL.findall(line):
            assert (host, port) == (BRIDGE, PORT), (
                f"{path.relative_to(ROOT)}:{lineno} offers "
                f"http://{host}:{port} as BW_SERVE_URL. It must be "
                f"http://{BRIDGE}:{PORT} — the docker bridge, because an "
                "unlocked `bw serve` returns real secrets to anything that can "
                "reach it, and because Tasks already binds the CLI's default "
                "port. See docs/SETUP-VAULT.md."
            )


@pytest.mark.parametrize("path,text", DOCS, ids=IDS)
def test_bw_serve_invocations_bind_to_the_bridge(path, text):
    for line in SERVE_CMD.findall(text):
        if "--hostname" not in line and "--port" not in line:
            continue  # prose about `bw serve`, not an invocation to copy
        assert "0.0.0.0" not in line, (
            f"{path.relative_to(ROOT)} shows `bw serve` on 0.0.0.0. Unlocked, it "
            "answers with real credentials to anyone who can reach the port."
        )
        assert f"--hostname {BRIDGE}" in line, (
            f"{path.relative_to(ROOT)} shows `bw serve` without "
            f"`--hostname {BRIDGE}`: {line.strip()}"
        )
        assert f"--port {PORT}" in line, (
            f"{path.relative_to(ROOT)} shows `bw serve` without `--port {PORT}` "
            f"(the CLI default 8087 collides with Tasks): {line.strip()}"
        )


def test_the_guard_is_actually_looking_at_the_three_places():
    """A scan that silently matches nothing passes forever. Pin the files that
    must be in range, so narrowing SUFFIXES or SKIP_DIRS fails here first."""
    seen = {str(p.relative_to(ROOT)) for p, t in DOCS if "BW_SERVE_URL" in t}
    for required in ("services/vault/app/main.py", ".env.example",
                     "docs/SETUP-VAULT.md"):
        assert required in seen, f"{required} is no longer being scanned"
