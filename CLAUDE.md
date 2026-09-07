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
- **Fast-forward only.** `promote.sh` refuses a promotion that is not a
  fast-forward, so production history is never rewritten and nothing already
  shipped is silently erased. If your branch is behind, merge or rebase onto
  production — do not force past it.

Those are safety nets. Removing the approval layer did not remove them, and
"ship faster" is not a reason to switch them off.

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
- `scripts/verify.sh` is the live diagnostic. It never prints secrets, email
  bodies, or tokens — keep it that way.
- Deployment: **pushing is not deploying.** Pushing a task branch is always
  safe; only the `production` branch is deployed by the OptiPlex, and only
  `scripts/promote.sh` moves it. Promotion needs no approval — just a green
  suite and a fast-forward.
