# FrankensteinCentral — how work ships

**The Product Owner protocol was removed on 2026-09-07, at Anthony's explicit
instruction as owner.** There is no external reviewer, no approval gate, no
turn-taking, and no `STATE.json`. Do not reintroduce any of them, and do not
route work through Codex or ChatGPT.

The previous version of this file is in git history if it is ever needed. It
described a Product Owner ↔ Claude message protocol with acceptance and
deployment-authorization gates. It cost more in shipping speed than it
returned, and on a personal dashboard with one owner and one user the owner
is the authority.

## Who decides

Anthony. He is the owner, the operator and the only user. If he asks for
something, build it. If he says ship it, ship it.

## How to ship

```bash
bash scripts/test.sh                 # must pass — no exceptions
git push origin HEAD:refs/heads/claude/<slug>
bash scripts/promote.sh <sha>        # moves production; the box deploys in ~60s
bash scripts/frankenstein-status.sh  # confirm what is actually running
```

Pushing a branch is not deploying. Only the `production` branch is deployed,
and only `promote.sh` moves it.

## The two things that still stop a bad deploy

Neither is an approval. Neither requires asking anyone.

1. **The test gate.** `scripts/deploy.sh` runs the full suite on the box
   before touching any container, and aborts the deploy if it fails. A red
   suite cannot reach production.
2. **Fast-forward only.** `promote.sh` refuses a promotion that is not a
   fast-forward. Production history is never rewritten, so nothing already
   shipped can be silently erased. If a branch is behind, merge or rebase
   onto production.

Removing the approval layer did not remove these, and "ship faster" is not a
reason to switch them off. They are what make shipping fast *safe* rather
than merely fast.

## Rollback

Production moves forward. To undo something, promote a NEW commit that
restores the earlier tree — never force-push production backwards. A
rollback commit has an old tree and a new SHA, which keeps the history
honest about what happened and when.

`scripts/rollback.sh` does this correctly. Rollback is never automatic: a
deploy that fails its test gate has not touched the containers, so there is
nothing to undo.

## Working with other agents

Several agents work this repo at once, and duplicated work has cost real
time here. Before starting a feature:

- check `git branch -r` and recent commits for a branch already doing it;
- if one exists, compare honestly and keep the better implementation rather
  than defending your own;
- if you find a real defect in another agent's branch, reproduce it before
  reporting it, and send the reproduction rather than the opinion.

## Honesty rules that still apply

These are about the product telling the truth, not about permission:

- Zero and unknown are different states. Suppressed values are `null`, never
  `0`. Never present a partial window as a complete one. See
  `docs/BUDGETS.md`.
- Date-dependent code goes through a single clock seam and is tested across
  a calendar sweep, never against "today". See `docs/TESTING.md`.
- `scripts/verify.sh` never prints secrets, tokens or message bodies.
- The repository is public. Fixture and sample data is synthetic — never
  real financial, account or message content.

## Still off-limits

Removing the review layer did not open these. They are about safety and
money, not about approval:

- Do not create credentials, accounts or security boundaries, and do not
  change existing ones.
- Do not spend money or activate paid services.
- Do not print tokens, credential contents or private keys.
- Do not weaken or skip the test gate to make something ship.
