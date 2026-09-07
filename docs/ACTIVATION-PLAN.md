# Activation plan: full credential separation

The target is **enforced runtime credential separation with restricted writer
capabilities**. Tier 1 — everything running as `antdag3` with Anthony's
personal `gh` credential — is a stopgap that is already installed, and it is
not the plan. This document is the plan for replacing it.

Proposed state and measured state are kept apart throughout, and every
measurement says how it was established.

## Measured state

| component | state | how established |
|---|---|---|
| Release service units | installed in `/etc/systemd/system/` | `systemctl list-units 'frankenstein-release*'` |
| Release timer | enabled, active, 2 min | `systemctl list-timers` |
| Release `ENABLED` flag | present | file exists |
| Release behaviour | **verified live** — read `control`, held on non-accepted state | `journalctl -u frankenstein-release.service` |
| **Release source SHA** | **`e73e4c4`** | `git -C ~/.frankenstein/release/src rev-parse HEAD` |
| Production | `0a5d24a` | `git ls-remote` |
| Candidate under review | see the handoff | — |
| Status publisher | **not installed**, no unit, no timer | no unit file present |
| Readiness check | implemented; **verified against the live stack**; **never run inside a real deploy** | manual invocation |
| GitHub rulesets | none — `rulesets: 0`, `production` unprotected, `0` deploy keys | GitHub API |
| `fcrelease` / `fcstatus` users | do not exist | never created |

### The release source is stale, and that is a live defect

`~/.frankenstein/release/src` is a **pinned copy** at `e73e4c4`. It does not
follow production and did not move when production did. The running release
service is therefore executing release logic that predates the corrections in
this task, including the rollback-idempotence and epoch fixes.

**The installed release-source SHA is a separate fact from the candidate SHA
and from production, and must be reported as such every time.** A copied
checkout is not a deployment target; nothing updates it.

Proposed fix, not yet executed: the release unit should run from a checkout
that is explicitly refreshed to a named ref before each cycle, or the
installation step must be re-run whenever the release logic changes. The
former is preferable — a service whose code silently ages is a service whose
behaviour nobody can state.

### On identity

No authenticated identity is inferred from git author or committer fields
anywhere in this plan. Every commit on `control` is authored
`anthonydagostino`; that establishes nothing about which agent or credential
performed the push. Author fields are metadata a client sets freely.

**A shared account label does not prove separation is impossible.** What the
boundary requires is that the production credential is *unreachable* at
runtime by any process that is not the release service, and that the writer's
capability is restricted at the server. Both are achievable whatever the
account is called.

## Target architecture

```
implementation authority  ≠  acceptance authority  ≠  release credential
Claude (worker)              Codex (control)          fcrelease (production)
```

| boundary | mechanism | why the label alone is not enough |
|---|---|---|
| runtime credential | the production token is a `0600` file owned by `fcrelease`; no agent runs as that user | a token readable by the agent user is not separated, whatever it is named |
| writer capability | a GitHub ruleset restricting `production` pushes, force-push and deletion blocked | the pre-push hook is defence in depth, not a barrier — it lives in the clone it is meant to constrain |
| control writes | a ruleset restricting `control` to the Product Owner's identity | without this, any process holding a repo-write credential can forge a directive |
| status writes | `fcstatus`, no production credential, one ref | reporting must not require release authority |

## Minimal owner-consent setup

Each step needs Anthony; none can be performed by an agent. Grouped so it is
one sitting.

**1. Two service accounts on the box** (sudo):

```bash
sudo useradd -m -s /usr/sbin/nologin fcrelease
sudo useradd -m -s /usr/sbin/nologin fcstatus
```

**2. One GitHub machine account** with a fine-grained PAT scoped to this
repository only, `Contents: read and write`, nothing else. Stored as:

```
/home/fcrelease/.frankenstein/release-token     mode 0600, owner fcrelease
```

Never in a unit file, never in the repository, never in an `Environment=`
line.

**3. Rulesets** (browser, one visit):

- `production`: restrict pushes to the machine account; block force-push and
  deletion.
- `control`: restrict pushes to the Product Owner's identity.

**Measured caveat:** GitHub's bypass-actor type `DeployKey` takes
`actor_id: null` — it is blanket and matches *every* deploy key on the
repository. It cannot express "this one key". Use a machine **account** with
the `User` actor type for anything that must be a single identity.

**4. Revoke Tier 1** once the above works: remove the `ENABLED` flag from the
`antdag3` release directory and disable that timer, so the shared-credential
path stops existing rather than merely being unused.

## Credential-safe validation

Every check below is read-only and prints no secret material.

| # | check | command | expected |
|---|---|---|---|
| 1 | the token file is unreadable by the agent user | `sudo -u antdag3 test -r /home/fcrelease/.frankenstein/release-token; echo $?` | non-zero |
| 2 | no agent process runs as the release user | `ps -u fcrelease -o comm=` | only the release unit, only while it runs |
| 3 | the release clone can push exactly one ref | `git push --dry-run --force origin <sha>:refs/heads/control` from its work clone | `REFUSED` |
| 4 | the ruleset is present | `gh api repos/:owner/:repo/rulesets --jq 'length'` | non-zero |
| 5 | production rejects a direct push from the agent user | dry-run push as `antdag3` | rejected by the server, not only by a hook |
| 6 | the status publisher holds no production credential | its unit has no token path and its hook permits only `refs/heads/status` | both true |
| 7 | release source freshness | `git -C <release source> rev-parse HEAD` | matches the intended release ref |

Check 5 is the one that actually proves the boundary. Until it fails from
`antdag3`, the separation is process, not enforcement.

## Status publisher activation

It holds no production credential in either arrangement and its clone may push
exactly one ref. Under the target it runs as `fcstatus`; the shipped templates
in `scripts/agent/` name that user and those paths consistently.

```bash
bash scripts/status-publisher.sh --dry-run     # publishes nothing
sudo cp scripts/agent/frankenstein-status.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start frankenstein-status.service
journalctl -u frankenstein-status.service -n 20 --no-pager
git ls-remote origin refs/heads/status
sudo systemctl enable --now frankenstein-status.timer
```

Validation: a second run with unchanged inputs must log `status unchanged` and
must not move the ref; a forced push of any other ref from its work clone must
be `REFUSED`.

## Explicitly not proposed

Creating users, accounts, tokens, deploy keys or rulesets; enabling the
worker; spending money; promoting anything. This document is planning only.
