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
protocol_version == 1
turn             == "claude"
status           in (ready_for_implementation, changes_requested)
task_id          matches ^FC-[0-9]{3,}$   (exact, not a substring match)
PRODUCT_DIRECTIVE.md exists on the control commit and names the same task
```

Every field is validated exactly. `FC-01`, `FC-001-extra`, `FC-001 `, `fc-001`
and `XX-001` are all rejected, as is any `protocol_version` other than the
supported one. Everything else is a logged no-op with exit 0: `product_owner/*`,
`none/accepted`, `blocked`, `claude/implementing`, malformed JSON, a missing
control branch, a missing directive, or a directive naming a different task.
**Claude never invents a task because the worker woke up.**

## The authoritative directive

State and directive are read from the control *commit*
(`git show <sha>:.frankenstein/…`), never from a working tree.

A task branch descends from `production`, whose `.frankenstein/` copies are
whatever was last accepted — possibly a stale placeholder for a different task.
So before the run starts, the worker **materializes the control snapshot** onto
the task branch: `PRODUCT_DIRECTIVE.md`, `STATE.json` and `PROTOCOL.md` from the
authorizing commit, plus `.frankenstein/AUTHORIZING_CONTROL_COMMIT` recording
the exact SHA that authorized the run. It commits that as
`[CLAUDE] FC-### authoritative control snapshot <sha>`.

Claude therefore reads the directive that authorized *this* run, and the
authorizing SHA is auditable on the branch afterwards.

## Continuing, not restarting

`changes_requested` means corrections to work already under review, so the
worker **resumes** `origin/claude/FC-###-work` rather than branching afresh from
production. If control names an `implementation_commit`, it must be an ancestor
of that branch — otherwise the worker refuses rather than continue on divergent
history. With neither a branch nor an `implementation_commit`, there is nothing
to continue and the run stops.

`ready_for_implementation` starts from `origin/production`, the approved
baseline.

## Isolation

Work happens in `~/.frankenstein/worktrees/agent-repo`, a **separate clone**.
The live deployment checkout `~/FrankensteinCentral` is owned by the deploy
mechanism and is used read-only (to discover the remote URL).

A separate clone rather than `git worktree`: worktrees share `.git`, so hooks
and config would be shared with the production checkout. This clone has its own
hooks. The worker refuses to start if the clone path resolves inside — or
equal to — the production checkout.

## The child-process boundary

A prompt is not containment. The Claude child runs under a real boundary, and
the worker **refuses to run at all** if it cannot establish one:

| boundary | how |
|---|---|
| cannot write the production checkout | `unshare --user --map-root-user --mount`, with `~/FrankensteinCentral` bind-mounted **read-only** inside the namespace |
| cannot push anywhere | the clone's `origin` remote is **removed** for the duration of the run and restored by the orchestrator afterwards — the child has no remote at all |
| cannot reuse a credential | the child is started with `env -i`: no `GITHUB_TOKEN`/`GH_TOKEN`, no inherited environment, a scratch `HOME`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=/bin/false` |
| cannot force push or touch production from this clone | a `pre-push` hook that rejects `production`/`main`/`master` **and** rejects any non-fast-forward push, detected by `merge-base --is-ancestor` |

Each of these is tested behaviorally, not by reading the source: the child is
handed an attack (write into production, push to production, dump its
environment) and the test asserts the attack failed while ordinary work — edits,
commits, running the test suite — still succeeds.

`FRANKENSTEIN_ALLOW_UNSANDBOXED=1` exists for hosts without unprivileged user
namespaces. It removes the boundary; the systemd template never sets it.

Publication happens in the orchestrator, after the child exits and verification
passes.

May: edit files in its clone, run repo tests, commit.

May **not**: push or merge production, run `promote.sh` / `rollback.sh` /
`deploy.sh`, touch systemd, force push, use `sudo`, issue a directive, or
change scope. The worker source contains none of those invocations (asserted by
test), and every push target in the script is `$TASK_BRANCH` or
`$CONTROL_BRANCH`.

## Concurrency

- **Single flight** — `flock` on `~/.frankenstein/agent/worker.lock`,
  non-blocking. A second poll while a run is active is a no-op.
- **Control commit as a token, checked twice.** The authorizing commit is
  recorded at the start.
  - *Stage 1*, after verification: control is re-fetched; if it moved, the run
    stops.
  - *Stage 2*, in the control clone immediately before writing the handoff:
    checked again, because the Product Owner can move control during the
    task-branch push that sits between the two. The clone is then reset to the
    **authorizing commit explicitly**, never to `origin/control` — resetting
    onto whatever origin now points at would rebase this run's stale state on
    top of newer Product Owner state and fast-forward over it cleanly.
  - The handoff push is non-forcing, so a race past both checks still cannot
    clobber.

  In every case the work stays on the task branch locally for inspection and
  newer Product Owner state is preserved untouched.

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
bash scripts/claude-worker.sh --dry-run    # full flow, mocked Claude, NO pushes
FRANKENSTEIN_MOCK_CLAUDE='...' bash scripts/claude-worker.sh   # inject a mock
```

`--dry-run` exercises control fetch → validation → wake decision → isolated
clone → control snapshot → sandboxed mock invocation → independent test re-run
→ the stage-1 token check, and then **stops before any push**. It reports what
it would have published — task branch, implementation SHA, control transition —
and records the run as `dry_run`. A test snapshots every ref on the remote
before and after and asserts they are identical.

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
