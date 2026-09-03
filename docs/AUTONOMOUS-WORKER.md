# Autonomous Claude worker

Orchestration infrastructure for the Product Owner ↔ Claude loop. It polls the
`control` branch and, **only** when control state authorizes it, runs Claude
non-interactively against an isolated clone.

**It is not enabled.** The systemd units are templates, and the worker refuses
to run unless `~/.frankenstein/agent/ENABLED` exists.

## Three branches

| branch | who moves it | deploys? | contents |
|---|---|---|---|
| `production` | `promote.sh`, after PO acceptance | **yes** — the only branch the deploy poller watches | product code |
| `control` | Product Owner (and the worker's handoff) | never | `.frankenstein/` only |
| `claude/FC-###-work` | the worker | never | implementation |

`control` is an **orphan** branch sharing no history with `production`. It
therefore cannot be fast-forwarded into production by accident, a merge would
be unmistakable, and a directive never requires touching production to write.

## Wake condition

```
turn == "claude"  AND  status in (ready_for_implementation, changes_requested)
```

Everything else is a logged no-op with exit 0: `product_owner/*`,
`none/accepted`, `blocked`, `claude/implementing`, a `task_id` that isn't
`FC-###`, malformed JSON, a missing control branch. **Claude never invents a
task because the worker woke up.**

State is read from the control *commit* (`git show <sha>:.frankenstein/STATE.json`),
not from a working tree, so it cannot be influenced by local files.

## Isolation

Work happens in `~/.frankenstein/worktrees/agent-repo`, a **separate clone**.
The live deployment checkout `~/FrankensteinCentral` is owned by the deploy
mechanism and is used read-only (to discover the remote URL).

A separate clone rather than `git worktree`: worktrees share `.git`, so hooks
and config would be shared with the production checkout. This clone has its own
hooks. The worker refuses to start if the clone path resolves inside — or
equal to — the production checkout.

## Safety boundaries

May: edit files in its clone, run repo tests, commit, push the task branch,
publish a handoff to control.

May **not**: push or merge production, run `promote.sh` / `rollback.sh` /
`deploy.sh`, touch systemd, force push, use `sudo`, issue a directive, or
change scope. Enforced three ways:

1. the worker source contains none of those invocations (asserted by test),
2. a `pre-push` hook installed in the isolated clone rejects
   `refs/heads/production|main|master` (behaviorally tested),
3. every push target in the script is `$TASK_BRANCH` or `$CONTROL_BRANCH`
   (asserted by test).

## Concurrency

- **Single flight** — `flock` on `~/.frankenstein/agent/worker.lock`,
  non-blocking. A second poll while a run is active is a no-op.
- **Control commit as token** — the authorizing commit is recorded at the
  start. Before publishing, control is re-fetched; if it moved, the worker
  stops and does **not** overwrite newer Product Owner state. The task branch
  remains locally for inspection. The handoff push is also non-forcing, so a
  race still cannot clobber.

## Failure behavior

A failure never fabricates a handoff. Claude exiting non-zero, producing no
commits, failing tests, a failed push, a moved control branch, or malformed
state all stop before publishing. Production and the running stack are
untouched in every case, and the outcome is recorded.

The worker **re-runs the test suite itself** after Claude finishes rather than
trusting the run's claim.

`FRANKENSTEIN_CLAUDE_TIMEOUT` (default 3600s) bounds the run so a wedged
process cannot hold the lock forever.

## Logging

`~/.frankenstein/agent/` — outside the repository:

- `worker.log` — decisions
- `run-<task>-<id>.log` — full Claude output
- `runs.jsonl` — one record per run: task id, control commit, task branch,
  start/end, result, Claude exit status, handoff commit, log path

No tokens, credentials or `.env` contents are printed.

## Kill switch

| file | effect |
|---|---|
| `~/.frankenstein/agent/ENABLED` | **required**; absent ⇒ the worker no-ops |
| `~/.frankenstein/agent/DISABLED` | overrides ENABLED; immediate no-op |

Completely independent of the production deployer: separate systemd units,
and neither script references the other (asserted by test). Stopping Claude
never stops deployment, and vice versa.

## Dry run

```bash
bash scripts/claude-worker.sh --status     # decision only, no action
bash scripts/claude-worker.sh --dry-run    # full flow, mocked Claude
FRANKENSTEIN_MOCK_CLAUDE='...' bash scripts/claude-worker.sh   # inject a mock
```

`--dry-run` exercises control fetch → wake decision → isolated clone →
mocked invocation → verification → publish, without invoking Claude.

## Enabling it (after Product Owner approval)

```bash
sudo cp scripts/agent/frankenstein-agent.* /etc/systemd/system/
sudo sed -i "s/REPLACE_WITH_USER/$USER/g" \
  /etc/systemd/system/frankenstein-agent.service
sudo systemctl daemon-reload
touch ~/.frankenstein/agent/ENABLED
sudo systemctl enable --now frankenstein-agent.timer
```

To stop it: `touch ~/.frankenstein/agent/DISABLED` (instant), or
`sudo systemctl disable --now frankenstein-agent.timer`. Neither affects
`frankenstein-deploy.timer`.
