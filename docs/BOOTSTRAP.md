# Bootstrap: turning the loop on without breaking its own rules

The release service will only promote work that carries a binding proving it
was built from an accepted directive:
`.frankenstein/AUTHORIZING_CONTROL_COMMIT`, naming the control commit that
authorized it.

**This protocol-infrastructure branch has no such binding.** It was built
before the loop existed, so there is no directive it can point at. That is the
bootstrap problem, and there are three ways to solve it. Two are wrong.

| approach | why it is rejected |
|---|---|
| `promote.sh --bootstrap` by Anthony | a manual DevOps chore — the exact thing being removed |
| a bypass flag in the release service | the release checks are the product; an escape hatch that exists will be used |
| **give the branch a real binding** | ✅ nothing is bypassed, and it exercises the loop for real |

## The correct path

The work is already done and reviewable. What it lacks is an authorization to
point at — so create one, in the ordinary way, and let the ordinary checks pass
on their own merits.

1. **Codex issues a directive on `control`** for this infrastructure task
   (e.g. `FC-002`), following the normal two-commit write order: content
   first, `STATE.json` last. That state-flip commit is the **authorization
   epoch** — call it `E`.
2. **Claude stamps the binding**: adds `.frankenstein/AUTHORIZING_CONTROL_COMMIT`
   containing `E` to the task branch and pushes it. This is an ordinary task
   branch push; it deploys nothing.
3. **Claude publishes the handoff** naming the new tip.
4. **Codex reviews and accepts**: `deploy-approved` in the directive, then
   `status: accepted` with `implementation_commit` set to that exact tip.
5. **The release service promotes it** — every check passing normally: the
   snapshot is an ancestor of the accepting commit, the task ids match, the
   directive identity is unchanged, and it fast-forwards production.

Step 5 is also the demonstration required before anyone may call this loop
ready: directive → implementation → review → acceptance → deployment, with no
human in the path.

**Ordering note:** the release service must be installed and enabled *before*
step 4, or acceptance will sit unacted-on. Installing it is safe at any time —
it fails closed, and with `control` idle it does nothing at all.

## The one-time setup request

Everything that can be prepared without credentials has been. What remains
needs Anthony exactly once.

### Tier 1 — makes the loop run (no new credentials)

The release service runs as `antdag3` using the existing `gh` credential.

```bash
sudo cp ~/.frankenstein/release/frankenstein-release.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start frankenstein-release.service     # proves the sandbox; arms nothing
journalctl -u frankenstein-release.service -n 20 --no-pager
```

Expect `NO-OP: not enabled (...ENABLED absent)`. Then arm it:

```bash
touch ~/.frankenstein/release/ENABLED
sudo systemctl enable --now frankenstein-release.timer
```

**Declared deviation.** This is not the design in `docs/RELEASE-SERVICE.md`.
There is no Unix separation: the release service uses Anthony's personal
credential, so a compromised or careless agent running as `antdag3` could push
production directly. What still constrains it is the chain verification, the
one-ref forward-only pre-push hook, and the fact that it never invokes
deployment tooling. This is not weaker than today's box — nothing currently
prevents that push either — but it is weaker than the design, and it should not
be left this way.

### Tier 2 — the separation the design actually calls for

Needs Anthony's sudo password and two GitHub actions. Not required for the loop
to run; required before calling it trustworthy.

| | what | why |
|---|---|---|
| 1 | `sudo useradd -m fcrelease` and `fcstatus` | the release credential stops being reachable by any agent |
| 2 | a machine account + fine-grained PAT, `Contents: read and write`, this repo only, stored `0600` as `fcrelease` | no human credential in the release path |
| 3 | a GitHub ruleset on `production`: block force-push and deletion, restrict push to the machine account | the real barrier; the hook is only defence in depth |
| 4 | a ruleset on `control` restricting push to Codex's identity | stops any lane on the box forging a directive |
| 5 | `ANTHROPIC_API_KEY` for the worker unit | see below |

**Ruleset caveat, measured:** GitHub's bypass-actor type `DeployKey` takes
`actor_id: null` — it is blanket and matches *every* deploy key on the repo. It
cannot express "this one key". Use a machine **account** with `User`, not a
deploy key, for anything that must be a single identity.

### Worker authentication

The worker currently authenticates Claude with the interactive OAuth session in
`~/.claude/.credentials.json`. The containment probe passes on it today, and it
is mounted read-only so a run cannot rotate the session Anthony uses
interactively.

**It will expire, and then the worker stops.** That is not a failure mode worth
debugging later at 2am: an unattended worker needs a dedicated
`ANTHROPIC_API_KEY` supplied through the systemd unit. That is new spend, so it
is Anthony's decision, and it is the one item here that cannot be deferred
indefinitely.

### Status publishing

`scripts/agent/frankenstein-status.{service,timer}` are templates for the
`fcstatus` user. Under Tier 1 the publisher can run as `antdag3` with
`FRANKENSTEIN_STATE_DIR=/home/antdag3/.frankenstein`. It holds no production
credential in either case, and its clone may push exactly one ref.

## What is deliberately still manual

`scripts/promote.sh` and `scripts/rollback.sh` remain operator tools for
emergencies. They are not part of the loop, are never invoked by any service,
and if a routine release ever needs one, that is a bug in the design rather
than a chore for Anthony.
