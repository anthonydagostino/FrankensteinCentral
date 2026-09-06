# Unattended Trusted Release — Design Proposal

Written: 2026-09-06
Author: Claude (protocol agent, OptiPlex)
Status: **PROPOSAL. READ-ONLY PASS. NOTHING EXECUTED.**

Supersedes `ENFORCEMENT_PROCEDURE.md` rev 2, which made Anthony the only actor
able to move production. That is now explicitly not the desired end state.

Target: after one-time setup, Anthony says *"add X to FrankensteinCentral"* and
performs **no operational action** for the rest of the lifecycle.

The design separates three authorities that rev 2 collapsed into one:

    implementation authority  !=  acceptance authority  !=  release credential
    Claude implements             ChatGPT accepts           fcrelease promotes

---

## 1. Smallest design for the deterministic release actor

**`scripts/release-service.sh`** — a fixed shell script, run by a systemd timer
as the Unix user `fcrelease`. It **never invokes Claude** and contains no model,
no prompt and no branching product logic. Roughly 150 lines plus tests, in the
same style as `autopull.sh`.

Its entire behaviour:

```
fetch control (anonymous, public repo — no credential needed to read)
evaluate the release condition in §4
  -> all conditions hold:  push the exact accepted SHA to production, log it
  -> anything else:        log the reason and exit 0, changing nothing
```

It has exactly two possible effects on the world: a fast-forward push of one
specific commit to `refs/heads/production`, or nothing. It cannot choose work,
cannot write `control`, cannot run `deploy.sh`, and cannot call `promote.sh`
(whose gates it reimplements deterministically and more strictly).

It reports each decision to a `releases` branch using its own credential —
never to `control`, which it may not write.

## 2. Where its production credential lives

    /home/fcrelease/.frankenstein/release-token     mode 600, owner fcrelease
    /home/fcrelease                                 mode 700

A **fine-grained PAT** belonging to a machine account
`frankenstein-release-bot`, scoped to this repository only, permission
**Contents: read and write**. The systemd unit runs `User=fcrelease` and reads
the token through a per-repo credential helper — never an `Environment=` line,
never a file in the repo.

*Why a machine account and not a deploy key:* ruleset bypass by `DeployKey`
requires `actor_id: null`, so it matches **every** deploy key on the repository.
A deploy key can therefore never be the production bypass without also admitting
the Claude lanes' key. `User` takes a specific `actor_id`; a machine account is
individually nameable.

## 3. How implementation Claude is prevented from reaching it

Four independent layers, in order of strength:

1. **Unix ownership.** The token is mode 600 under `fcrelease`; Claude lanes run
   as `fcagent` / `fcprotocol` and simply cannot read it.
2. **No escalation.** `sudo` on this box requires a password (verified). Claude
   lanes are in **neither `sudo` nor `docker`**.
3. **No Claude on the docker-capable account.** `antdag3` is in the `docker`
   group, which is root-equivalent — anything there can mount `/` into a
   container and read any file. This is why *no Claude session may run as
   `antdag3`*, which is the rule this whole design rests on.
4. **Server-side.** Even holding the Claude lanes' key, `production` and
   `control` refuse the push, because the deploy key is in neither bypass list.

The worker's sandbox never sees the release credential: it is not in the
sandbox's environment, not in its mounts, and not readable by its Unix user.

## 4. Exact condition that authorizes a release

**All** must hold, evaluated against the tip of `control`. Any failure means
*do nothing* — fail closed, log, exit 0:

1. `STATE.json` parses and `protocol_version` is recognised
2. `status == "accepted"`
3. `PRODUCT_DIRECTIVE.md` **at that same control commit** says
   `Deployment Authorization: deploy-approved`
4. `implementation_commit` I is present, and I exists in the repository
5. I is reachable from a `claude/*` task branch (not an arbitrary object)
6. **I is a strict descendant of `origin/production`** — fast-forward only
7. I's history contains the worker's authoritative control-snapshot commit, and
   that snapshot names `directive_commit` D
8. D is an ancestor of the control tip, and `task_id` in control equals
   `task_id` in the snapshot — the implementation was built from the directive
   that was accepted, not a different one
9. `origin/production` is re-read immediately before the push and still matches
   what was evaluated

Then: `git push origin <I>:refs/heads/production` — non-forced, no lease
override, single ref. Never `--force`, never a delete, never any other ref.

Conditions 7 and 8 are the anti-substitution check: they bind
*directive → implementation → acceptance* into one chain, so an accepted SHA
cannot be swapped for a different commit that merely happens to be a descendant.

## 5. How Product Owner acceptance reaches that condition

ChatGPT writes `control` using the two-commit order already in `PROTOCOL.md`:
content first, state flip second. To accept and release:

```
commit 1   IMPLEMENTATION_HANDOFF review notes, and
           PRODUCT_DIRECTIVE.md  -> Deployment Authorization: deploy-approved
commit 2   STATE.json -> status: accepted, implementation_commit: <SHA>,
                         turn: none, last_actor: product_owner
```

The release service only ever acts on the second. Because `control` is
ruleset-restricted to the Product Owner actor, **acceptance can only originate
from the Product Owner** — that is the property that lets a deterministic script
trust it without judgement.

To request changes instead, ChatGPT writes `status: changes_requested` and the
release service does nothing, forever, silently.

## 6. Can ChatGPT's GitHub integration own `control` writes?

**Unknown, and I will not guess — it must be measured.** What I established
read-only:

- **ChatGPT has never written to this repository.** Across every ref, there are
  exactly three commit identities: `Claude <noreply@anthropic.com>`,
  `Anthony D'Agostino <anthonysdagostino@gmail.com>` (local git), and one commit
  authored by `anthonydagostino` with committer `GitHub` — the repository's
  *initial commit*, from the web UI. No integration write exists to inspect.
- **This box cannot enumerate installed GitHub Apps.** `/user/installations`
  returns `403 — must authenticate with an access token authorized to a GitHub
  App`, and the repo-level installation endpoint requires a JWT. So I cannot
  tell from here whether ChatGPT acts as an App, an OAuth app, or as Anthony.

**Proposed measurement — cheap, reversible, and it decides the design:**

> Ask ChatGPT to commit one throwaway file, e.g. `.frankenstein/PROBE.md`
> containing a single line, to `control`. I then read
> `author.login`, `committer.login` and `commit.verification` for that commit
> and report exactly which actor type it presents as. Then delete the file.

The result maps directly onto the bypass list:

| what the probe shows | control ruleset bypass | assessment |
|---|---|---|
| a GitHub App (`Integration`) | that app, by id | **best** — a distinct, nameable, revocable actor |
| `anthonydagostino` (`User`) | Anthony's account | **acceptable** — but only because his credential is being removed from the box, so no agent can borrow it |
| unsigned pushes from an unclear actor | — | stop; use a dedicated PO machine account instead |

In every outcome, **protocol Claude is never given `control` write**, so it can
never impersonate the Product Owner. That is the point of §9's worker change.

## 7. Required GitHub rulesets and actors

| branch | rule | bypass |
|---|---|---|
| `production` | restrict updates, block force pushes, block deletions | **`frankenstein-release-bot` (User) + admin** |
| `control` | restrict updates, block force pushes, block deletions | **the Product Owner actor from §6 + admin** |
| `handoff` | none | — |
| `claude/*` | none | — |

Actors:

- **one deploy key** for all Claude lanes (`fcagent`, `fcprotocol`) — task
  branches and `handoff` only. Being blanket, `DeployKey` is deliberately in
  **no** bypass list, which is exactly the desired outcome.
- **`frankenstein-release-bot`** — production only.
- **the Product Owner actor** — control only.
- **Anthony (admin)** — retained as bypass on both purely as a break-glass path
  for the day a bot credential expires; not used in normal operation.

This is one fewer machine account than rev 2 required, because the worker no
longer writes `control`.

## 8. Required Unix identities

| user | runs | sudo | docker | credential held |
|---|---|---|---|---|
| `antdag3` | human, host operations, the deploy poller | yes (password) | **yes** | none, after `gh auth logout` |
| `fcagent` | general/product/CM Claude sessions | no | no | Claude-lane deploy key |
| `fcprotocol` | the autonomous implementation worker | no | no | Claude-lane deploy key |
| `fcrelease` | `release-service.sh` only — **no Claude** | no | no | release bot PAT |

`fcagent` and `fcprotocol` are indistinguishable to GitHub (same deploy key);
their separation is OS-level blast radius, keeping ad-hoc sessions out of the
worker's sandbox, workspaces and logs. If that is judged unnecessary, they can
be merged into one user without weakening any server-side property — the
enforceable boundary is `fcrelease` versus everything else.

**The rule this design depends on: no Claude session runs as `antdag3`.** With
`docker` there, that account can read every file on the box, including the
release token.

## 9. Does the current worker need changes?

**Yes — one substantive change, and it is the crux.**

`claude-worker.sh` currently publishes its handoff by **pushing `control`**
(it stamps `implementation_commit`, `turn: product_owner`,
`status: awaiting_review` into `STATE.json` and pushes `HEAD:$CONTROL_BRANCH`).
Under this design the worker must have **no** write access to `control` — a
worker that can write `control` can write `status: accepted` and release itself.

Required changes, all narrow:

1. **Publish the handoff to a `handoff` branch** instead of `control`. The
   worker already parameterises the branch name; the publish path needs to
   target `HANDOFF_BRANCH`, and the "control moved under us" concurrency check
   becomes a read-only comparison rather than a push race. ~20–30 lines.
2. **Extend the worker's pre-push hook** to refuse `control` as well as
   `production`/`main`/`master`. Defence in depth only — the ruleset is the real
   barrier, a local hook proves nothing.
3. **Tests** for both: the worker must fail closed if it is ever pointed at
   `control`, and the existing "worker never invokes promote/rollback/deploy/
   systemctl/sudo" assertions stay.
4. The systemd unit's `User=REPLACE_WITH_USER` becomes `fcprotocol`.

Everything else — sandboxing, egress allowlist, state validation, the
authoritative control snapshot, the clean publisher — is unchanged and is what
conditions 7–8 of §4 rely on.

## 10. Automated rollback that still requires valid authorization

Same gate, same actor, no new authority. The Product Owner writes to `control`:

```
STATE.json  ->  rollback_to: "<known-good SHA>"
                status: accepted
PRODUCT_DIRECTIVE.md -> Deployment Authorization: deploy-approved
```

The release service, seeing `rollback_to` (mutually exclusive with a promotion —
if both are present it does nothing and says so), then:

1. verifies the good SHA is an **ancestor of current production** — you can only
   roll back to something that was actually released
2. builds a **new commit on top of production whose tree is that good commit's
   tree**, deterministically, exactly as `rollback.sh` does
3. pushes it fast-forward

Production stays append-only: nothing is rewound, the bad deploy remains in the
audit trail, and the poller deploys the rollback like any other change. The
release service never force-pushes and never deletes, so the `production`
ruleset can keep both blocked with no exception.

Anthony is not involved. Neither is a human.

## 11. What Anthony must do ONCE during setup

Only things GitHub or the OS require an account owner or `sudo` password for:

1. create the machine account `frankenstein-release-bot`, invite it as a
   collaborator, mint its fine-grained PAT (Contents: write, this repo only)
2. add the Claude-lane deploy key (write access) — public half only
3. ask ChatGPT to make the one probe commit in §6, so the control bypass actor
   can be identified
4. create the two rulesets (§7)
5. `sudo adduser` for `fcagent`, `fcprotocol`, `fcrelease`; place the release
   token under `fcrelease`
6. install two systemd units (`frankenstein-release.timer`,
   `frankenstein-agent.timer`) and enable them
7. `gh auth logout` on the box, removing his own full-`repo` token
8. start using `fcagent` for ad-hoc Claude sessions instead of `antdag3`

Roughly one sitting. Items 1–4 are browser work; 5–7 are typed commands with a
sudo password; I prepare every command and verify every result.

## 12. What Anthony never needs to do again

Push production · run `promote.sh` · run `rollback.sh` · copy a SHA · create,
merge or review a PR · manage branches · approve a commit · run a deploy
command · relay a message between ChatGPT and Claude · check whether a deploy
landed · perform rollback mechanics.

His remaining involvement is product intent — *add this, remove that, I don't
like this, do that next* — plus the genuinely exceptional consents: creating or
revoking a credential, authorizing spend, destroying meaningful data, or
weakening a security boundary.

## 13. Migration from the current state (`9b96bd0`)

Ordered so nothing is removed before its replacement is proven, and so the
system is releasable at every step:

1. **measure the Product Owner actor** — the §6 probe commit (nothing else can
   be designed correctly until this is known)
2. implement the §9 worker changes on a task branch; full suite green; publish
   for Product Owner review — this is ordinary protocol work and needs no new
   credential
3. write `release-service.sh` and its tests on the same branch; it is inert
   until a unit exists and a token is present
4. Product Owner accepts that branch; **Anthony promotes it manually one last
   time** — the final manual promotion in the system's life
5. create identities and credentials (§11 items 1–2, 5)
6. create the two rulesets (§7)
7. prove each lane behaviorally: Claude key pushes a task branch (succeeds),
   pushes `control` (must be refused by GitHub), pushes `production` (must be
   refused); release bot pushes production (succeeds, on a throwaway probe)
8. install and enable `frankenstein-release.timer`; verify it does **nothing**
   while `control` says `awaiting_directive` — the fail-closed default
9. `gh auth logout`; confirm the poller still deploys (anonymous fetch of a
   public repo, verified working)
10. only then install and enable the worker timer, and issue FC-001 as the first
    end-to-end exercise

Steps 1–4 need no credential changes at all, so the risky work is confined to
5–9 and each step is independently reversible: every ruleset can be disabled
from a browser, and every credential can be revoked without touching the
running stack.

---

## Residual risks, stated plainly

- **The `control` bypass may end up being Anthony's own account** (if that is
  what ChatGPT presents as). Then anyone holding his credential can forge
  acceptance. Mitigated only by that credential leaving the box in step 9 — it
  is a real dependency, not a proof.
- **The release service trusts `control` because a ruleset says only the Product
  Owner may write it.** If that ruleset is ever disabled, the release service
  keeps trusting `control`. Optional hardening: require signed commits on
  `control` and have the service verify the signature, which survives a ruleset
  being switched off.
- **`fcagent` and `fcprotocol` are one actor to GitHub.** A compromised general
  session can push task branches the worker also pushes to. It still cannot
  write `control` or `production`, so it cannot cause a release.
- **The whole model assumes no Claude runs as `antdag3`.** That is an operating
  habit, not an enforced control. The one way to make it enforced is to remove
  `antdag3` from the `docker` group and give the deploy poller its own service
  user — worth considering, and out of scope until you ask.

---

Prepared for Product Owner review. Nothing will be executed by me.
