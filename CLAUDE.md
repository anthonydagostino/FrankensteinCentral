# CLAUDE.md — project instructions

FrankensteinCentral is a personal life-OS dashboard: FastAPI microservices
behind a gateway, deployed with docker compose on a home OptiPlex.

## Before starting any work — mandatory

Read `.frankenstein/PROTOCOL.md` and `.frankenstein/STATE.json`.

**The Product Owner controls scope and roadmap.** Do not begin product work
unless `STATE.json` says `turn: claude` with an authorized status
(`ready_for_implementation` or `changes_requested`).

Quick check:

```bash
bash scripts/frankenstein-status.sh
```

If it is not Claude's turn, say so and stop. Do not invent a task, do not
pick the next feature, and do not simulate a Product Owner response.
`PROTOCOL.md` is the canonical and complete set of rules — this file only
points at it.

Reading the repo, answering questions, and explaining existing behavior are
always allowed. The restriction is on changing the product.

## Agent boundaries — deployment is not yours

An agent doing **product work** (the money layer, services, gateway, docs,
tests) has exactly one delivery mechanism: commit to its task branch and
push that branch. Specifically, it must **never**:

- run `scripts/promote.sh` (with or without `--force`/`--bootstrap`),
- push, force-push, or otherwise move the `production` branch by any other
  means, including a direct `git push <sha>:refs/heads/production`,
- edit anything under `.frankenstein/` — `STATE.json`,
  `PRODUCT_DIRECTIVE.md`, `IMPLEMENTATION_HANDOFF.md` and `PROTOCOL.md`
  included.

Only the **protocol agent** moves deployment and owns the protocol files.
This holds even when the Product Owner says "deploy this" in a session:
the answer is to hand over the commit SHA and stop, not to override the
gates. `promote.sh --force` is not an escape hatch for a product agent —
it exists for the protocol agent's explicitly approved overrides.

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
- Deployment: **pushing is not deploying.** Task branches
  (`claude/FC-###-<slug>`) are always safe to push for review; only the
  `production` branch is deployed by the OptiPlex, and only
  `scripts/promote.sh` moves it — after acceptance, when the directive says
  `deploy-approved`. See the protocol's deployment sections.
