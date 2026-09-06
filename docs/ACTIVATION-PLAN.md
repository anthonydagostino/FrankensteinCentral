# Activation plan: status publisher and release verification

**Nothing in this document has been executed.** It is a proposal. Actual state
is reported separately, below, and is distinguished from proposed state
throughout.

## Actual state, and how it is known

| component | state | how this was established |
|---|---|---|
| Release service units | **installed** in `/etc/systemd/system/` | `systemctl list-units 'frankenstein-release*'` |
| Release timer | **enabled and active**, every 2 min | `systemctl list-timers` |
| Release `ENABLED` flag | **present** | `ls ~/.frankenstein/release/ENABLED` |
| Release behaviour | **verified live** — fetched `control` through the systemd sandbox and held on non-accepted state | `journalctl -u frankenstein-release.service` |
| Release credential | Anthony's personal `gh` credential (Tier 1) | `~/.gitconfig` delegates to `gh auth git-credential` |
| Status publisher | **NOT installed**, no unit, no timer | no unit file in `/etc/systemd/system/` |
| Readiness check | **implemented and exercised against the live stack** (15 apps, all services up); **not yet run inside a real deploy** | manual invocation |
| GitHub rulesets | **none** — `rulesets: 0`, `production` unprotected, `0` deploy keys | GitHub API |
| Separate Unix users | **none** — `fcrelease`/`fcstatus` do not exist | `useradd` never run |

**Provenance caveat:** the authenticated identity behind a push cannot be
determined from git author or committer fields. Every commit on `control` is
authored `anthonydagostino`, which establishes nothing about which agent or
credential performed it. Nothing in this plan infers authorization identity
from commit attribution.

## Minimal activation — status publisher

Publishing `status` is what lets the Product Owner see deployment outcomes
without Anthony relaying them. It holds **no production credential** and its
clone's pre-push hook permits exactly one ref.

Under Tier 1 it can run as `antdag3`:

```bash
# 1. verify by hand first — publishes nothing
bash scripts/status-publisher.sh --dry-run

# 2. install (paths for the Tier 1, single-user arrangement)
sudo cp scripts/agent/frankenstein-status.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload

# 3. one manual run, checked before any timer exists
sudo systemctl start frankenstein-status.service
journalctl -u frankenstein-status.service -n 20 --no-pager
git ls-remote origin refs/heads/status

# 4. only then, the timer
sudo systemctl enable --now frankenstein-status.timer
```

The shipped unit targets the `fcstatus` user of the full design. For Tier 1 it
must be edited to `User=antdag3` with
`FRANKENSTEIN_STATE_DIR=/home/antdag3/.frankenstein`, which is a **declared
deviation**, not an approved one.

## Validation — how to know it worked

| # | check | expected |
|---|---|---|
| 1 | `--dry-run` output | valid JSON, `schema: 2`, epoch equals control's `STATE.json` commit |
| 2 | first service run | `refs/heads/status` exists |
| 3 | second run, unchanged | logs `status unchanged — nothing published`, ref does not move |
| 4 | hook | a forced push of any other ref from its work clone is `REFUSED` |
| 5 | after a real deploy | `verification.result` is `pass`/`fail` and `verification.commit` equals `deployment.running_commit` |
| 6 | stale case | if they differ, `verification.result` is `stale` and `attention_required` is true |

Checks 1–4 need no deploy. Checks 5–6 require an actual release, which is
gated on Product Owner acceptance and is **not** part of this correction.

## Release verification path — what remains unproven

The readiness check has been exercised against the live stack, but **never
inside a deploy**, because no deploy has run since it was written. Until a
release actually happens:

- `verification` in `deployed.json` stays `not_run` for the running commit
- the end-to-end loop remains undemonstrated

The first accepted release is what validates this, and it is Codex's decision.

## Explicitly not proposed here

Creating Unix users, machine accounts, tokens or deploy keys; changing GitHub
rulesets or repository settings; enabling the worker; spending money; or
promoting anything. Those need Anthony's explicit consent, and the identity
problem below should be settled first.

## The blocker that changes the design

Codex writes `control` as `anthonydagostino`, not a distinct identity. The
Tier 2 plan assumed a `control` ruleset could restrict pushes to Codex and
exclude Claude lanes. It cannot, because those are the same GitHub identity as
Anthony's own account and as any Claude lane using his `gh` credential.

This is not a deferred hardening step — it is a designed boundary that does
not work as specified. Options: a dedicated machine account for the Product
Owner, or an explicit decision to accept that `control` is protected by
process rather than by authentication. **That is a Product Owner and owner
decision, not an implementation detail.**
