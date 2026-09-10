"""Plex and Vaultwarden are one click from the main screen (SCRUM-140).

Anthony's instruction, verbatim: "I want a button to open up plex application
on the main dashboard. I want the same for vaultwarden that brings up my
vaultwarden. These are non negotiable."

These pin the whole chain, because a button whose URL never arrives is the
"instruction with no control" defect from SCRUM-137 in a new shape: the header
has the buttons; home.js wires them from the services' `web_url`; each service
reads an env var; compose passes it; .env.example and CONFIGURATION.md tell the
operator it exists. Break any link and this fails.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "gateway" / "static" / "index.html").read_text()
HOME_JS = (ROOT / "gateway" / "static" / "home.js").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
CONFIG_DOC = (ROOT / "docs" / "CONFIGURATION.md").read_text()
VAULT = (ROOT / "services" / "vault" / "app" / "main.py").read_text()
PLEX = (ROOT / "services" / "plex" / "app" / "main.py").read_text()


def header():
    m = re.search(r'<header class="cc-top">(.*?)</header>', INDEX, re.S)
    assert m, "no header"
    return m.group(1)


def test_both_buttons_are_in_the_header_not_the_launcher():
    h = header()
    assert 'id="cc-plex"' in h
    assert 'id="cc-vault"' in h
    # visible on the main screen: real anchors, opening in a new tab
    for bid in ("cc-plex", "cc-vault"):
        tag = re.search(rf'<a[^>]*id="{bid}"[^>]*>', h).group(0)
        assert 'target="_blank"' in tag and 'rel="noopener"' in tag


def test_plex_always_has_somewhere_to_go():
    """Even before home.js runs, or if the plex sub-app is down, the button
    must not be a dead `#`."""
    tag = re.search(r'<a[^>]*id="cc-plex"[^>]*>', INDEX).group(0)
    assert 'href="https://app.plex.tv/desktop"' in tag
    assert 'def _web_url(machine: str | None) -> str:' in PLEX, "plex web_url may be None"
    assert 'if _connected() else None' not in PLEX, "plex hides the URL when disconnected"


def test_home_js_wires_both_from_the_services():
    assert "wireLaunchButtons" in HOME_JS
    assert 'q("#cc-plex")' in HOME_JS and 'q("#cc-vault")' in HOME_JS
    assert '/api/vault/summary' in HOME_JS
    assert '/api/plex/summary' in HOME_JS


def test_an_unconfigured_vault_button_explains_itself():
    """The SCRUM-137 lesson: never point the user at something that isn't
    there. No URL -> the click names the env var, not a 404."""
    assert "unconfigured" in HOME_JS
    assert "VAULTWARDEN_WEB_URL" in HOME_JS
    assert ".cc-icon-btn.unconfigured" in (ROOT / "gateway" / "static" / "home.css").read_text()


def test_the_vault_service_exposes_the_web_vault_regardless_of_mode():
    assert 'VAULTWARDEN_WEB_URL = os.environ.get("VAULTWARDEN_WEB_URL"' in VAULT
    # in the summary AND health payloads
    assert VAULT.count('"web_url": VAULTWARDEN_WEB_URL or None') >= 2
    # and never gated on the bw-serve connection: `_connected()` may sit next
    # to it as a sibling key, but must not be the condition that hides it
    for line in VAULT.splitlines():
        if '"web_url"' in line:
            assert "if _connected()" not in line, line.strip()


def test_every_env_var_flows_end_to_end():
    for var in ("VAULTWARDEN_WEB_URL", "PLEX_WEB_URL"):
        assert f"{var}: ${{{var}:-}}" in COMPOSE, f"{var} not passed by compose"
        assert re.search(rf"^{var}=", ENV_EXAMPLE, re.M), f"{var} missing from .env.example"
        assert f"`{var}`" in CONFIG_DOC, f"{var} undocumented in CONFIGURATION.md"


def test_the_palette_can_open_both():
    assert '"Open Plex"' in HOME_JS
    assert '"Open Vaultwarden"' in HOME_JS
