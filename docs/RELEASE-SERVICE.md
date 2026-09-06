# The deterministic release service

`scripts/release-service.sh` is the only actor that moves `production`.

**It is not enabled.** The systemd units are templates, and the service
refuses to run unless `~/.frankenstein/release/ENABLED` exists. No credential,
Unix user or GitHub ruleset for it exists yet either.

## Why it exists

Three authorities that are easy to collapse into one, and must not be:

```
implementation authority  ≠  acceptance authority  ≠  release credential
Claude implements            Codex accepts            fcrelease promotes
```

Acceptance is judgement, and belongs to the Product Owner. Release is
mechanism, and belongs to something that cannot form an intention. The moment
an agent holds the production credential, "who decided to ship this" stops
being answerable — so the thing holding it contains no model, no prompt and no
product logic at all.

## Its entire effect on the world

```
git push origin <accepted SHA>:refs/heads/production      # fast-forward
```

or nothing. It cannot force push, cannot delete, cannot push any other ref,
cannot write `control`, and never runs `deploy.sh`, `promote.sh` or
`rollback.sh`. The OptiPlex deploy poller does the deploying, as it always
has; this service only moves the ref the poller watches.

A `pre-push` hook in its own clone enforces the same three rules locally —
defence in depth. The GitHub ruleset is the real barrier.

## The release condition

**All** of these must hold, evaluated against the tip of `control`. Any
failure means *do nothing*:

| | condition |
|---|---|
| 1 | `STATE.json` parses and `protocol_version` is the supported one |
| 2 | `status == accepted` |
| 3 | `last_actor == product_owner`, and `turn` is not `claude` |
| 4 | `PRODUCT_DIRECTIVE.md` **at that same control commit** carries exactly one `Deployment Authorization:` line, reading `deploy-approved`, and names the task exactly once |
| 5 | `implementation_commit` I is a full 40-character SHA that exists here |
| 6 | I is reachable from a `claude/*` branch — an object that exists is not an accepted one |
| 7 | I is a **strict descendant** of `origin/production` — fast-forward only |
| 8 | I carries `.frankenstein/AUTHORIZING_CONTROL_COMMIT` = S, and S is an ancestor of the accepting control commit |
| 9 | S's `task_id` equals control's, and S's `directive_commit` equals control's |
| 10 | `origin/production` is re-read immediately before the push and still matches what was evaluated |

Conditions 8 and 9 are the anti-substitution check. They bind
*directive → implementation → acceptance* into one chain, so an accepted SHA
cannot be swapped for a different commit that merely happens to descend from
production. Hand-editing `implementation_commit` to a SHA the worker did not
produce fails at 8.

Condition 3 is what stops the implementation worker from accepting itself —
together with the worker never being able to write `control` at all.

## Two exit dispositions, deliberately different

- **NO-OP, exit 0** — the state legitimately does not authorize a release:
  not accepted, no deploy approval, nothing new, protocol version unknown.
  This is the common case and stays quiet.
- **REFUSED, exit 1** — control *says* accepted but the release cannot be
  validated: a missing binding, a non-descendant, an acceptance not written by
  the Product Owner. Loud on purpose. It changes nothing, and a red timer is
  the correct outcome for a state someone needs to look at.

Every decision is appended to `~/.frankenstein/release/releases.jsonl`.

## Rollback: authorized by the same gate, and append-only

The Product Owner writes `rollback_to: <SHA>` instead of
`implementation_commit`, with `status: accepted` and `deploy-approved`. The
two are mutually exclusive; both present means do nothing.

The target must already be an **ancestor of production** — you can only roll
back to something that was actually released. The service then builds a new
commit on top of production whose *tree* is the good commit's tree, and
fast-forwards to it.

Production therefore only ever moves **forward**. The bad deploy stays in the
audit trail, nothing is rewound, and the ruleset can keep force pushes and
deletions blocked with no exception at all.

## Where its credential lives

```
/home/fcrelease/.frankenstein/release-token     mode 0600, owner fcrelease
/home/fcrelease                                 mode 0700
```

A fine-grained PAT belonging to a machine account
`frankenstein-release-bot`, scoped to this repository only, **Contents: read
and write**, read through a git credential helper — never an `Environment=`
line, never a file in the repository.

*Why a machine account and not a deploy key:* ruleset bypass by `DeployKey`
requires `actor_id: null`, which matches **every** deploy key on the
repository — so a deploy key could never be the production bypass without also
admitting the Claude lanes' key. `User` takes a specific `actor_id`.

Four independent layers keep the Claude lanes away from it:

1. **Unix ownership** — mode 0600 under `fcrelease`; the Claude lanes run as
   `fcagent` / `fcprotocol` and cannot read it.
2. **No escalation** — `sudo` needs a password here, and the Claude lanes are
   in neither `sudo` nor `docker`.
3. **No Claude on the docker-capable account** — `antdag3` is in `docker`,
   which is root-equivalent. This is why no Claude session may run as
   `antdag3`; the whole model rests on that rule.
4. **Server-side** — even holding the Claude lanes' key, `production` and
   `control` refuse the push.

## Running it before it is real

```bash
bash scripts/release-service.sh --status     # decision only, no action
bash scripts/release-service.sh --dry-run    # full evaluation, NO push
```

Neither needs `ENABLED`, and a test asserts `--dry-run` leaves every remote ref
identical.

## Enabling it (after the rulesets exist)

```bash
sudo cp scripts/agent/frankenstein-release.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo -u fcrelease touch /home/fcrelease/.frankenstein/release/ENABLED
sudo systemctl enable --now frankenstein-release.timer
```

Verify it does **nothing** while control says `awaiting_directive` before
trusting it with anything — that fail-closed default is the first thing to
confirm on the real box.

To stop it: `touch ~/.frankenstein/release/DISABLED` (instant), or
`sudo systemctl disable --now frankenstein-release.timer`. Neither affects
`frankenstein-deploy.timer` or `frankenstein-agent.timer`.

## The dependency this design does not hide

The service trusts `control` because a GitHub ruleset restricts `control` to
the Product Owner actor. **If that ruleset is ever disabled, the service keeps
trusting control.** Optional hardening, not implemented: require signed commits
on `control` and verify the signature here, which survives a ruleset being
switched off.
