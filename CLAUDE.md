# CLAUDE.md — project instructions

FrankensteinCentral is a personal life-OS dashboard: FastAPI microservices
behind a gateway, deployed with docker compose on a home OptiPlex.

## How work gets shipped

FrankensteinCentral has **no product-acceptance gate and no deployment
authorization gate.** They were removed on 2026-09-07 at Anthony's explicit
instruction: the review layer was costing more in shipping speed than it
returned. Anthony is the owner and the only user. He decides what gets built
and what ships.

**Do not route work through an external reviewer.** No Codex, no ChatGPT, no
Product Owner turn-taking, no `STATE.json` turn check, no waiting for a
directive before starting. If you have been asked to build something, build
it and ship it.

Pick up work, build it, test it, push it, promote it:

```bash
bash scripts/test.sh                 # must pass
bash scripts/promote.sh <sha>        # ships it; the box deploys within ~60s
bash scripts/frankenstein-status.sh  # confirm what is actually running
```

### What still stops a bad deploy

Two things, and neither is an approval — nobody has to be asked:

- **The test gate.** `scripts/deploy.sh` runs the full suite on the box
  BEFORE touching any container and aborts if it fails. A red suite cannot
  reach production. Keep it that way.

  There is an override — `DEPLOY_SKIP_TESTS=1` — and you should know it
  exists rather than discover it. Emergencies are real, and a gate with no
  hatch tends to get removed rather than used carefully. But using it is
  **recorded**: `deployed.json` carries `running_tests: "skipped"`, and both
  `frankenstein-status.sh` and the deploy card say so until a tested deploy
  replaces it. Do not reach for it to get past a red suite you caused.
- **Fast-forward only.** `promote.sh` refuses a promotion that is not a
  fast-forward, so production history is never rewritten and nothing already
  shipped is silently erased. If your branch is behind, merge or rebase onto
  production — do not force past it.

Those are safety nets. Removing the approval layer did not remove them, and
"ship faster" is not a reason to switch them off.

### Where work comes from — the Jira board

The backlog is the **SCRUM project** at
<https://anthonysdagostino.atlassian.net>. Read it before inventing a task:
it is Anthony's actual priority order, and it is not short.

Every ticket is currently **unassigned**, which makes collisions the default
failure rather than an unlikely one. So, before you start:

1. **Take code tickets in this repo only.** The board is Anthony's whole life,
   not just this project: it also holds hardware, resale and move-admin work
   ("buy a NAS", "set up the 2019 MacBook", "laptop stands"). Those are his to
   do, not yours — you cannot buy a NAS, and a ticket you cannot finish is
   worse held than untouched. If a ticket's outcome is not a commit to this
   repository, leave it alone.
2. Within what is left, pick highest priority first rather than by your own
   idea of what matters. Security and data-loss tickets outrank features.
3. Check nobody is already on it — `git branch -r`, the recent commits, and
   **the ticket's status and comments**. In Progress means taken.
4. Assign the ticket to yourself and move it to In Progress **before** writing
   code, then say so in a comment. An unassigned ticket is an invitation for a
   second agent to do the same work — and moving it is not enough on its own:
   SCRUM-114 was independently fixed twice, in parallel, after it had already
   been moved to In Progress. Check immediately before you start, not once at
   the beginning of a long session.
5. When it ships, comment the production SHA on the ticket and close it. A
   ticket that is fixed but still open sends the next agent to redo it — that
   has already happened here (SCRUM-102 was fixed and promoted while the
   ticket stayed To Do).

If nothing on the board fits what you were asked to do, say so rather than
silently picking something adjacent.

### Coordinating with other agents

Several agents work this repo at once. Before starting a feature, check
whether someone is already on it — `git branch -r` and the recent commits —
because duplicated work has cost real time here. If you find a parallel
branch, compare honestly and keep the better one rather than defending your
own.

## Working conventions

- Run `bash scripts/test.sh` before handing anything off. `scripts/deploy.sh`
  also runs it on the box and aborts the deploy if it fails.
- Date-dependent code goes through a single clock seam and is tested across a
  calendar sweep, never against "today" — see `docs/TESTING.md` for why.
- Honesty rules in the money layer: zero and unknown are different states;
  suppressed values are `null`, never `0`; never present a partial window as
  complete. See `docs/BUDGETS.md`.
- `GATEWAY_PASSWORD` in `.env` gates the dashboard (SCRUM-98). Unset means
  no login — a real state, reported by `verify.sh`, `frankenstein-status.sh`
  and a banner on the page — never a silent one. Do not add public paths to
  `gateway/app/auth.py` without saying why in the same commit.
- `scripts/verify.sh` is the live diagnostic. It never prints secrets, email
  bodies, or tokens — keep it that way.
- Deployment: **pushing is not deploying.** Pushing a task branch is always
  safe; only the `production` branch is deployed by the OptiPlex, and only
  `scripts/promote.sh` moves it. Promotion needs no approval — just a green
  suite and a fast-forward.
