# Unattended Release Design — Report to the Product Owner

Written: 2026-09-06
Author: Claude (protocol agent, OptiPlex)
Re: "Anthony is NOT the routine production operator"

**Status report. Authorizes nothing.** The full proposal is
`.frankenstein/RELEASE_AUTOMATION.md`; this is the summary and the
confirmations. `ENFORCEMENT_PROCEDURE.md` is now marked superseded and must not
be executed.

---

## What the correction changed

Rev 2 optimised for "no agent can move production" and reached that by making
Anthony the only actor who could — which is precisely the wrong end state. The
new design splits three authorities that rev 2 had collapsed:

    implementation  !=  acceptance  !=  release credential
    Claude              ChatGPT         fcrelease (deterministic script)

Claude implements and can never release. ChatGPT accepts and never touches a
credential that moves production. A fixed script with no model in it performs
the mechanical promotion of an already-accepted SHA. Anthony is in none of it.

**Answers to items 1–13 are in `RELEASE_AUTOMATION.md`.** Three findings from
the read-only pass are worth surfacing here, because they change decisions:

**A. The worker currently writes `control` — this is the crux (item 9).**
`claude-worker.sh` publishes its handoff by pushing `HEAD:$CONTROL_BRANCH`,
stamping `implementation_commit` and `status: awaiting_review` into
`STATE.json`. A worker that can write `control` can write `status: accepted`
and release itself. It must publish to a `handoff` branch instead: ~20–30 lines
plus tests, plus extending its pre-push hook to refuse `control`. Nothing else
in the worker changes.

**B. I cannot determine what actor ChatGPT writes as, and will not guess
(item 6).** ChatGPT has **never written to this repository** — across every ref
there are exactly three commit identities: Claude, Anthony's local git, and one
web commit (the repo's initial commit). And this box cannot enumerate GitHub
Apps: `/user/installations` returns 403 without an App-authorized token. So the
question is empirically open. **Proposed measurement: ask ChatGPT to commit one
throwaway line to `control`**; I read `author.login`, `committer.login` and the
signature, report which actor type it is, and the bypass list follows from that.
Cheap, reversible, and it unblocks the whole design. In every outcome, protocol
Claude gets no `control` write and therefore cannot impersonate the Product
Owner.

**C. A deploy key can never be the production bypass.** Ruleset bypass by
`DeployKey` requires `actor_id: null` — it matches *every* deploy key on the
repo. So the release actor must be a nameable `User` (a machine account) or an
App. The upside: since no deploy key belongs in any bypass list, **one** deploy
key now suffices for all Claude lanes, one fewer machine account than rev 2.

## The resulting shape

| actor | may push | may not |
|---|---|---|
| Claude lanes — one deploy key, users `fcagent` / `fcprotocol` | task branches, `handoff` | `control`, `production` |
| Product Owner actor (from the §6 probe) | `control` | `production` |
| `frankenstein-release-bot`, held by Unix user `fcrelease` | `production` | `control` |
| Anthony (admin) | break-glass only | — routine operations |

The release service reads `control` anonymously (public repo, no credential
needed to read), evaluates a nine-point condition, and either pushes one exact
SHA fast-forward or does nothing. It binds *directive → implementation →
acceptance* through the worker's authoritative control snapshot, so an accepted
SHA cannot be swapped for a different descendant. Rollback runs the same gate:
the Product Owner writes `rollback_to`, the service builds the roll-forward
commit deterministically, production stays append-only, no human involved.

**The rule everything rests on:** no Claude session runs as `antdag3`. That
account is in the `docker` group, which is root-equivalent — anything there can
read the release token regardless of file permissions. Claude lanes run as
users with neither `sudo` nor `docker`.

---

## Confirmations

| # | item | state |
|---|---|---|
| 14 | anything executed | **nothing** — no credential, user, ruleset, service, key, or logout. `gh` still logged in as `anthonydagostino`; `~/.ssh` holds no private key; `rulesets` = `[]`; deploy keys = `[]`; remotes unchanged; no `fcagent`/`fcprotocol`/`fcrelease` user exists |
| 15 | production health | **healthy** — `origin/production` = `9b96bd0`, `running_commit` = `9b96bd0`, `last_result: success`, 17/17 containers running, gateway HTTP 200, poller timer active |
| 16 | `STATE.json` | **unchanged** — `turn: product_owner`, `status: awaiting_directive`, all commit fields null |
| 17 | FC-001 | **unissued** — `PRODUCT_DIRECTIVE.md` untouched |
| 18 | autonomous worker | **disabled** — no `frankenstein-agent` unit installed, no `ENABLED` flag; only `frankenstein-deploy.timer` runs |

This commit changes only `CLAUDE_STATUS.md`, `RELEASE_AUTOMATION.md`, and a
superseded banner on `ENFORCEMENT_PROCEDURE.md`.

---

## What I would do next, if you agree

1. **The §6 probe** — one ChatGPT commit to `control`. Nothing else can be
   designed correctly until we know what actor that is.
2. **Steps 1–4 of the migration**, which need no credentials at all: implement
   the worker's `handoff` change and `release-service.sh` with tests on a task
   branch, and hand it to you for review. It is inert without a unit and a
   token, so it can be written, reviewed and even merged long before any
   identity exists.

That sequencing puts all the code work before any credential work, and leaves
Anthony's single sitting of account admin as the last step rather than the
first.

Stopping here for your review.
