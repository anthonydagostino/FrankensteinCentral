# Activation plan: full separation

**Nothing in this document has been executed.** No Unix user has been created,
no credential issued, no unit installed, no ruleset changed, no money spent.
Actual state is reported in its own section and is never mixed with proposed
state.

The target of this plan is **full separation**: enforced runtime credential
separation and restricted writer capabilities. Process-only Tier 1 — every
actor running as one human account, with the boundary held by convention — is
recorded here as the current deviation, not as the destination.

## 1. Actual state, and how each line is known

| component | state | how this was established |
|---|---|---|
| Release service units | **installed** in `/etc/systemd/system/` | `systemctl list-units 'frankenstein-release*'` |
| Release timer | **enabled and active**, every 2 min | `systemctl list-timers` |
| Release `ENABLED` flag | **present** | `ls ~/.frankenstein/release/ENABLED` |
| Release behaviour | **verified live** — fetched `control` through the systemd sandbox and held on non-accepted state | `journalctl -u frankenstein-release.service` |
| Release credential | Anthony's personal `gh` credential (Tier 1) | `~/.gitconfig` delegates to `gh auth git-credential` |
| Release **source checkout** | a copy; **does not follow production** | see §2 |
| Status publisher | **NOT installed**, no unit, no timer | no unit file in `/etc/systemd/system/` |
| `status` branch | **absent** on the remote | `git ls-remote origin refs/heads/status` |
| Readiness check | **implemented**; exercised by hand against the live stack; **never run inside a real deploy** | manual invocation |
| GitHub rulesets | **none** — `rulesets: 0`, `production` unprotected, `0` deploy keys | GitHub API |
| Separate Unix users | **none** — `fcrelease`, `fcstatus`, `fcdeploy` do not exist | `useradd` never run |
| Machine accounts | **none** | no account created |

Installed, enabled, and verified are three different states and are reported
as three. "Installed" means a unit file exists; "enabled" means a timer will
start it; "verified" means its behaviour was observed in the journal. Nothing
above is claimed as verified on the strength of the two weaker facts.

**Provenance caveat.** The authenticated identity behind a push cannot be
determined from git author or committer fields. Every commit on `control` is
authored `anthonydagostino`, which establishes nothing about which agent or
credential performed it. Nothing in this plan infers authorization identity
from commit attribution, and no evidence below is gathered that way.

## 2. The release service runs from a copy

`frankenstein-release.service` executes
`/home/fcrelease/FrankensteinCentral/scripts/release-service.sh` — its own
checkout. That checkout does **not** move when `production` moves. The gate
enforcing a release can therefore be an older revision of the nine-point check
than the commit under review, and the release record alone could not show it.

`scripts/release-service.sh` now records `source_commit` — the SHA of the
checkout it is itself running from — on every line it writes, and
`scripts/status-publisher.sh` publishes it as `release_service.source_commit`
alongside `release_service.matches_production`. **The installed release-source
SHA and the candidate SHA are separate fields and must be read separately.**

Updating that checkout is an ordinary `git fetch && git reset --hard` in
`/home/fcrelease/FrankensteinCentral`, and it is the responsibility of whoever
activates the service. It is not automatic, and this plan does not make it
automatic — a service that updates its own gate on a schedule is a service
whose gate nobody has reviewed.

## 3. Why "one shared account" does not settle the question

The earlier plan concluded that the boundary was impossible because Codex
writes `control` as `anthonydagostino`, the same GitHub identity as Anthony
and as any Claude lane using his `gh` credential.

That conclusion goes too far. A shared **account label** is not a shared
**capability**. What the design actually requires is:

1. **Enforced runtime credential separation** — the credential that can move
   `production` is readable by exactly one Unix user, and no agent process
   runs as that user.
2. **Restricted writer capabilities** — each credential can write only the
   refs its holder is supposed to write, enforced on the server by rulesets
   and locally by the pre-push hooks that already exist.

Neither requires that Codex have a distinct GitHub identity. A GitHub identity
governs *who may push*; these two properties govern *what each running process
is able to push*, which is the property that actually contains a
misbehaving or compromised agent. A separate machine account for the Product
Owner remains desirable and is listed in §6, but it is a hardening step, not a
precondition.

What a shared account genuinely costs: `control` cannot be protected from a
Claude lane by authentication alone, because a lane holding Anthony's `gh`
credential can write any ref that credential can write. That is why §4 gives
each non-human actor **its own credential**, and why Anthony's personal
credential must stop being the one the services use.

## 4. The target: four actors, four credentials

| actor | Unix user | may write | credential |
|---|---|---|---|
| Deploy poller | `fcdeploy` | **nothing** — read-only | none; the repository is public and it fetches anonymously |
| Release service | `fcrelease` | `refs/heads/production` only | fine-grained PAT, machine account, Contents: read+write, this repository only |
| Status publisher | `fcstatus` | `refs/heads/status` only | fine-grained PAT, second machine account, same scope |
| Claude worker | `fcworker` | `refs/heads/claude/*`, `refs/heads/handoff` | fine-grained PAT, third machine account |
| Product Owner (Codex) | not on the box | `refs/heads/control` | its own connector credential |
| Anthony | `antdag3` | anything | personal; **no service uses it** |

Enforcement is in two layers, and each is independently sufficient to stop the
common mistake:

- **Server side.** A ruleset per protected branch restricting who may push it.
  `production`: `fcrelease`'s account only, and no force-push. `control`:
  Codex's account only. `status`: `fcstatus`'s account only.
- **Box side.** Each service's clone carries a `pre-push` hook permitting
  exactly one ref. `scripts/status-publisher.sh` already installs its own;
  `scripts/release-service.sh` already installs its own. These work today,
  under Tier 1, and are the reason Tier 1 is a *deviation* and not an absence
  of a boundary.

The unit templates in `scripts/agent/` describe exactly this arrangement, and
`tests/test_unit_templates.py` holds them to it: every service names its user,
executes from that user's checkout, writes only under that user's home, and no
unit other than the release service references a credential path.

## 5. Minimal owner-consent plan

These are the smallest steps that get from here to §4. Each is an action only
Anthony can take, and **none has been performed**. They are ordered so that
nothing is created before the thing that would use it is reviewable.

**Step 1 — three machine accounts.** GitHub → new account per actor
(`frankenstein-release-bot`, `frankenstein-status-bot`,
`frankenstein-worker-bot`), each added to this repository as a collaborator
with Write. Cost: none. Reversible: remove the collaborator.

**Step 2 — three fine-grained PATs**, one per account, scoped to this
repository only, Contents: read and write, no other permission, 90-day expiry.
Each written to a file owned by the matching Unix user with mode 0600, never
into a unit file and never into the repository:

```
/home/fcrelease/.frankenstein/release-token    0600 fcrelease
/home/fcstatus/.frankenstein/status-token      0600 fcstatus
/home/fcworker/.frankenstein/worker-token      0600 fcworker
```

**Step 3 — three Unix users**, no login shell, no sudo, each with its own
checkout of this repository:

```bash
sudo useradd -m -s /usr/sbin/nologin fcrelease
sudo useradd -m -s /usr/sbin/nologin fcstatus
sudo useradd -m -s /usr/sbin/nologin fcdeploy
```

`fcdeploy` additionally needs the `docker` group to drive Compose. That is a
real privilege and is called out rather than buried: membership of `docker` is
equivalent to root on the host. It is the reason `fcdeploy` holds no GitHub
credential at all.

**Step 4 — three rulesets**, as in §4. Cost: none. Reversible: delete them.

**Step 5 — remove Anthony's credential from the service path.** Once each
service has its own token, `~/.gitconfig`'s delegation to `gh
auth git-credential` must no longer be reachable by any service unit. This is
the step that converts Tier 1 into full separation, and it is deliberately
last: doing it earlier strands the loop.

**Step 6 — install the units**, in the order status → deploy → release, each
with one manual run and a journal read before its timer is enabled. The
release service is last because it is the only one that can move `production`.

Nothing above may proceed under the current directive. Deployment
Authorization is `test-only`; this section is a plan awaiting a decision.

## 6. Not proposed here

Enabling the autonomous worker; promoting or deploying anything; changing
repository settings beyond the three rulesets named above; any spend; any
change to the Firefly, Gmail, Plex or Vault integrations or their credentials.
A dedicated machine account for the Product Owner is *desirable* (it would let
`control` be protected by authentication rather than by process) but is not
required by §4 and is not requested here.

## 7. Credential-safe validation evidence

Every check below is read-only, runs before any credential exists, and reveals
no secret. None of them writes a ref, and none of them requires a deployment.

| # | check | command | expected |
|---|---|---|---|
| 1 | publisher builds a valid record | `bash scripts/status-publisher.sh --dry-run` | JSON, `schema: 2`, `control.authorization_epoch` equals control's `STATE.json` commit; **nothing pushed** |
| 2 | publisher redacts | inspect the §1 dry-run output | no token-shaped string, no `user:pass@` URL |
| 3 | readiness against the live stack | `bash scripts/readiness.sh --json-only` | `result`, per-service `pass`/`degraded`/`fail`; unconfigured integrations degrade and do not fail |
| 4 | readiness rejects an error page | `FRANKENSTEIN_READINESS_URL=<a 200 that is not the hub> bash scripts/readiness.sh` | `fail`, `gateway serves the dashboard` in `required_failed` |
| 5 | release service holds | `journalctl -u frankenstein-release.service` | refuses on non-accepted state; **no push** |
| 6 | release source is reported | `release_service.source_commit` in the dry-run record | present, and compared against `production` rather than assumed equal |
| 7 | ref restriction, publisher | attempt a push of any other ref from its work clone | `pre-push: REFUSED` |
| 8 | ref restriction, release | same, from the release clone | `pre-push: REFUSED` |
| 9 | unit templates | `python3 -m pytest tests/test_unit_templates.py` | every ExecStart, user and path consistent |
| 10 | full suite | `bash scripts/test.sh` | `ALL TESTS PASSED` |

Checks 1–4 and 6–10 need no credential and no deployment, and can all be run
now. Checks 5 requires the already-installed release unit and observes only.

**What cannot be validated without an accepted release:** that a real deploy
records `verification.result` `pass`/`fail` against the running commit, and
that the publisher then reports `in_sync`. Those are the two facts that
require an end-to-end deployment, and until one has been observed, **no claim
of unattended readiness is supportable.** This document does not make one.
