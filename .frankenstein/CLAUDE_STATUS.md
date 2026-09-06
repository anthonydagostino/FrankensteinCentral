# Status Report — Codex as Product Owner: worker split + release service

Written: 2026-09-06
Author: Claude (protocol agent, OptiPlex)
Re: Anthony's directive "Codex replaces ChatGPT as Product Owner / orchestrator"

**Status report and implementation handoff. Authorizes nothing. Nothing was
activated.** This is not a protocol handoff for an FC task — `FC-001` remains
unissued and `STATE.json` is untouched on every branch.

---

## 1. Implementation branch

`claude/po-handoff-release`

## 2. Exact implementation SHA

`f1a045c1577ee8eefaa3c0c9be3b7d3c516aa7e1`

## 3. Files changed

| file | |
|---|---|
| `scripts/claude-worker.sh` | modified — publishes to `handoff`, never `control` |
| `scripts/release-service.sh` | **new**, 323 lines |
| `scripts/agent/frankenstein-release.service` | **new** template — not installed |
| `scripts/agent/frankenstein-release.timer` | **new** template — not installed |
| `AGENTS.md` | **new** — the Product Owner's operating contract |
| `docs/RELEASE-SERVICE.md` | **new** |
| `docs/AUTONOMOUS-WORKER.md` | modified |
| `.frankenstein/PROTOCOL.md` | modified |
| `.frankenstein/IMPLEMENTATION_HANDOFF.md` | modified |
| `CLAUDE.md` | modified |
| `tests/test_claude_worker.py` | modified — updated and extended |
| `tests/test_release_service.py` | **new**, 48 tests |

No product code was touched.

## 4. Worker handoff architecture

The role change lands as one technical requirement: **the implementation
worker must no longer be able to write `control`.** The old worker published
its handoff by pushing `control` — the branch that also carries
`status: accepted`. Under unattended release, that is self-authorization
waiting to happen.

```
control  ──read──▶  worker  ──write──▶  handoff  ──read──▶  Product Owner
   ▲                                                              │
   └──────────────────────── write ───────────────────────────────┘
```

One-way per branch. Claude reads `control` and writes `handoff`; Codex reads
`handoff` and writes `control`. A single shared branch would mean the
implementation agent could write acceptance.

`handoff` is an orphan branch, append-only, carrying four files:

| file | why |
|---|---|
| `STATE.json` | `implementation_commit` stamped by the publisher, not the child |
| `IMPLEMENTATION_HANDOFF.md` | what was built, and its declared deviations |
| `AUTHORIZING_CONTROL_COMMIT` | **the binding** — which control commit authorized it |
| `TASK_BRANCH` | which branch carries the work |

Also changed: `pre-push` hooks in every trusted clone now refuse `control` as
well as `production`/`main`/`master`; the stage-2 concurrency check became a
read-only comparison (no clone to reset, no push to race); the prompt forbids
writing control, self-acceptance and `release-service.sh`. Sandboxing, egress
allowlisting, the export bundle and every fail-closed path are unchanged.

**One addition that was not in the directive.** Control no longer flips to
`awaiting_review` when a run finishes — you do that, after reviewing. Nothing
else would have stopped the next poll re-running the same directive forever.
The worker now reads `handoff`'s `AUTHORIZING_CONTROL_COMMIT` before starting
and no-ops if it already equals the authorizing control commit. Server-side, so
it survives losing host state, and readable by you. Your next control commit is
what makes the next run legitimate.

## 5. Release-service architecture

`scripts/release-service.sh` — a fixed script with no model, no prompt and no
product logic. It never invokes Claude or Codex, and never runs `deploy.sh`,
`promote.sh` or `rollback.sh`. Its entire effect on the world:

```
git push origin <accepted SHA>:refs/heads/production      # fast-forward
```

or nothing. The OptiPlex poller still does the deploying.

Ten conditions, all of which must hold, evaluated against the control tip:

1. `STATE.json` parses; `protocol_version` supported
2. `status == accepted`
3. `last_actor == product_owner`, and `turn` is not `claude`
4. `PRODUCT_DIRECTIVE.md` **at that same control commit**: exactly one
   `Deployment Authorization: deploy-approved`, naming the task exactly once
5. `implementation_commit` I is a full 40-char SHA that exists
6. I is reachable from a `claude/*` branch
7. I is a **strict descendant** of production — fast-forward only
8. I carries `AUTHORIZING_CONTROL_COMMIT` = S, and S is an ancestor of the
   accepting control commit
9. S's `task_id` and `directive_commit` equal control's
10. production is re-read immediately before the push and still matches

Conditions 8–9 are the anti-substitution check, binding *directive →
implementation → acceptance* into one chain. Condition 3 is what stops an
implementation agent accepting itself, together with it never being able to
write `control` at all.

**Rollback** uses the same gate: you write `rollback_to: <SHA>` instead of
`implementation_commit`. The two are mutually exclusive. The target must
already be an ancestor of production, and production moves *forward* to the
older tree — append-only, so force pushes and deletions stay blocked with no
exception.

**Two exit dispositions, deliberately different.** NO-OP exit 0 when the state
legitimately does not authorize a release (the quiet common case). REFUSED
exit 1 when control says accepted but cannot be validated — loud, because that
state needs a human to look at it. Neither changes anything.

## 6. `AGENTS.md` — summary

Repository root, 122 lines, written to be short enough to actually follow.

- **Roles**: Anthony product intent and exceptional consent only; you as
  Product Owner and orchestrator; Claude implementation only; the release
  service as the sole production writer.
- **Branch map** and who writes each.
- **The loop**, step by step, including that you never perform the release and
  never need Anthony for it.
- **The exact `control` write format** — the two-commit order (content first,
  state flip last), the JSON for a directive, for changes-requested, and for
  acceptance, plus rollback. This is the part the release service validates, so
  it is spelled out rather than described.
- **What still requires Anthony**: new paid spend; credentials or accounts;
  destructive deletion of meaningful personal data; weakening a security
  boundary. It says explicitly that normal feature work and normal releases
  require none of these, and that wanting to ask him something operational is
  a signal the design has a hole.
- **Standing constraints**: never ask Claude to write `control`; the repo is
  public while the deployment holds real financial data; `main` is not
  production; tests are the deploy gate.

## 7. Tests run and results

`bash scripts/test.sh` → **1526 passed**, 0 failed (1469 before; **+57**).
Python suites, JavaScript syntax, and shell syntax for all ten scripts.

Required regression coverage, all present and passing:

| # | required property | result |
|---|---|---|
| 1 | worker cannot push `control` | hook refuses it behaviorally; no `git push` in source names it |
| 2 | worker cannot push `production` | hook refuses it; production unchanged after every run |
| 3 | worker can publish `handoff` | end-to-end run publishes and reads back |
| 4 | handoff identifies the exact implementation commit | asserted against the task branch tip |
| 5 | handoff binds to the exact directive/control snapshot | asserted on both handoff and the implementation |
| 6 | worker cannot mark its own work accepted | 8 invalid-state cases; nothing published, control unmoved |
| 7 | existing containment tests remain green | full suite passing |

Plus: `handoff` is an orphan; it appends rather than being rewritten; an
answered authorization does not re-run; a new control commit does authorize a
new run; and 48 release-service tests covering every refusal path, both
rollback directions, dry-run inertness, and the structural guarantees (no
model, no deployment tooling, no force push, production-only pushes).

Two development failures were real bugs, not test noise: the release service
exited 1 rather than no-op when `control` did not yet exist, and it signed
rollback commits with an Anthropic address. Both fixed.

## 8. Remaining one-time setup for Anthony

Only what GitHub or the OS requires an account owner or a sudo password for.
Items 1–4 are browser work; 5–8 are typed commands. Roughly one sitting, and I
prepare every command and verify every result.

1. **Settle the Codex write path** — §9 below. Nothing downstream can be
   finalised until this is measured.
2. Create the machine account `frankenstein-release-bot`, invite it as a
   collaborator, mint a fine-grained PAT (Contents: read+write, this repo only).
3. Add the Claude-lane deploy key (public half only).
4. Create the two rulesets — §10.
5. `sudo adduser fcagent fcprotocol fcrelease`; place the release token at
   `/home/fcrelease/.frankenstein/release-token`, mode 0600.
6. Install and enable `frankenstein-release.timer`; confirm it does **nothing**
   while control says `awaiting_directive`.
7. `gh auth logout` on the box, removing the full-`repo` token.
8. Start using `fcagent` for ad-hoc Claude sessions instead of `antdag3`.

Then, and only then, enable the worker timer and issue FC-001 as the first
end-to-end exercise.

**The rule the whole design rests on: no Claude session runs as `antdag3`.**
That account is in the `docker` group, which is root-equivalent — anything
running there can read every file on the box, including the release token.

## 9. Exact Codex probe instructions

The previous ChatGPT chat connector returned `403 Resource not accessible by
integration` and is irrelevant now. What was measured since: the
**ChatGPT Codex Connector** GitHub App is *authorized* on Anthony's account but
**not installed on any repository**, and shows "Never used" — which is
sufficient on its own to explain a 403 from a user-to-server token, regardless
of permissions.

So: install it on `anthonydagostino/FrankensteinCentral` (Only select
repositories), then Codex creates, on `control`:

```
path:    .frankenstein/PROBE.md
content: probe
```

**Use the current control tip, not `d0d97d1`** — control has moved. A stale-ref
error would look like a permission failure and would be misread.

Then inspect the resulting commit and report:

- `author.login`
- `committer.login`
- `verification` (verified / reason / signature present)
- whether GitHub identifies the writer as an **Integration/App**, **Anthony's
  User**, another specific User, or something else

That measured actor becomes the candidate `control` ruleset bypass actor.
**Do not guess before the probe succeeds**, and do not create the ruleset until
it does — a bypass list naming the wrong actor locks the Product Owner out of
the branch the whole loop depends on.

If the writer turns out to be an App, that is the better outcome: an
`Integration` actor is individually nameable, and it resolves the standing risk
that the `control` bypass would otherwise have to be Anthony's own account.

## 10. GitHub rulesets to create after the probe

| branch | rules | bypass |
|---|---|---|
| `control` | restrict updates, block force pushes, block deletions | the measured Codex actor + Anthony (admin, break-glass) |
| `production` | restrict updates, block force pushes, block deletions | `frankenstein-release-bot` (User) + Anthony (admin, break-glass) |
| `handoff` | none | — |
| `claude/*` | none | — |

The Claude-lane deploy key must be in **neither** bypass list. Note that
bypass by `DeployKey` requires `actor_id: null`, which matches *every* deploy
key on the repository — so a deploy key can never be the production bypass
without also admitting the Claude lanes' key. That is why the release actor is
a machine account.

**Residual dependency, stated rather than hidden:** the release service trusts
`control` because a ruleset restricts it to you. If that ruleset is ever
disabled, the service keeps trusting control. Optional hardening, not
implemented: require signed commits on `control` and verify the signature.

## 11. Confirmation — nothing was changed

| item | state, verified on the box just now |
|---|---|
| credentials / tokens | **none created, rotated or revoked** |
| machine accounts | **none created** |
| Unix users | `fcagent`, `fcprotocol`, `fcrelease`, `fcpo` — **none exist** |
| deploy keys | `[]` |
| rulesets | `[]` |
| services installed | only the pre-existing `frankenstein-deploy.*` |
| release service | **not installed, not enabled**; no release directory on the host |
| `gh` | still logged in as `anthonydagostino` — no logout performed |
| systemd | untouched; no unit added, enabled or started |

## 12. Production

**Unchanged and healthy.** `origin/production` = `9b96bd0`; the box reports
`running_commit: 9b96bd0`, `last_result: success`. The deploy poller continues
on its 60s cycle. No product code was touched by this work.

## 13. `STATE.json`

**Unchanged**, on every branch. `control` = `2ca43d6`, still
`turn: product_owner` / `status: awaiting_directive`. The task branch carries
production's placeholder untouched.

## 14. FC-001

**Unissued.** No directive was written, and none of this work was performed
under one — it is protocol infrastructure, authorized directly by Anthony.

## 15. Autonomous worker

**Still disabled.** No `~/.frankenstein/agent/ENABLED` flag, no agent unit
installed, no timer. Unchanged by this work.

---

## What I would do next

1. **Install the Codex Connector and run the §9 probe.** It is the only
   blocker: everything from §10 onward depends on knowing the actor.
2. **Review this branch** — `claude/po-handoff-release` at `f1a045c`. It needs
   no credential and changes nothing until promoted.
3. When you accept it, Anthony promotes it **manually one last time**. That is
   the final manual promotion in this system's life; after the rulesets and the
   release service exist, no human pushes production again.
