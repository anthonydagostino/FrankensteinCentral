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
PRODUCT_DIRECTIVE.md carries exactly one `Task ID: FC-###` naming that task
```

Every field is validated exactly. `FC-01`, `FC-001-extra`, `FC-001 `, `fc-001`
and `XX-001` are all rejected, as is any `protocol_version` other than the
supported one.

The directive rule is strict in both directions: a directive with **no**
`Task ID:` line, a **malformed** one, a **different** one, or **two conflicting**
ones each block the run. A nonempty directive is not evidence of authorization;
naming this exact task is.

Everything else is a logged no-op with exit 0: `product_owner/*`,
`none/accepted`, `blocked`, `claude/implementing`, malformed JSON, a missing
control branch, or a missing directive. **Claude never invents a task because
the worker woke up.**

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

## Three trust zones

Containment is not one boundary but three, because the dangerous moments are
not only while Claude is running.

| zone | what runs there | trusted? |
|---|---|---|
| **child** | Claude, or the mock | no — everything it can influence |
| **verification** | the independent `scripts/test.sh` re-run | no — Claude may have edited that file |
| **publisher** | fetch, ancestry checks, the two pushes | yes — and it executes nothing that came from the child |

### Isolation of the clone

Work happens in `~/.frankenstein/worktrees/agent-repo`, a **separate clone**.
The live deployment checkout `~/FrankensteinCentral` is owned by the deploy
mechanism. A separate clone rather than `git worktree`: worktrees share `.git`,
so hooks and config would be shared with the production checkout. The worker
refuses to start if the clone path resolves inside — or equal to — the
production checkout.

### The child zone: filesystem isolation, not just environment isolation

`env -i` hides environment variables. It does not hide the filesystem, and the
child runs under the same UID as the deploying user. So the child is given a
user+mount namespace in which, in this order:

1. the production checkout is bind-mounted **read-only**;
2. the agent clone is bind-mounted at a **neutral path outside the home**
   (`/mnt`, `/media` or `/srv` — the first empty one) and becomes the working
   directory;
3. any narrowly configured Claude authentication paths are bind-mounted
   **read-only** into a prepared scratch home;
4. **the real home directory is replaced** by that scratch home. `~/.config/gh`,
   `~/.ssh`, `~/.gitconfig`, `~/.frankenstein`, shell configuration, the
   production checkout and the agent directory simply do not exist for the
   child, by absolute path or any other;
5. shared runtime and temp locations (`/run/user/<uid>`, `/tmp`, `/var/tmp`)
   get a **private tmpfs** — skipping any that is an ancestor of something the
   child legitimately needs, since masking those would remove the workspace or
   the scratch home too.

On top of that the clone's `origin` remote is **removed** for the duration of
the run — the child has nothing to push to — and the environment is rebuilt
from nothing with `env -i`: no `GITHUB_TOKEN`, no `GH_TOKEN`, no
`SSH_AUTH_SOCK`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`,
`GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=/bin/false`.

If the namespace cannot be established the worker **refuses to run**.
`FRANKENSTEIN_ALLOW_UNSANDBOXED=1` exists for hosts without unprivileged user
namespaces; it removes the boundary, and the systemd template never sets it.

### Claude's own authentication

The child needs to reach Claude, and nothing else. Two narrow channels, both
opt-in and neither carrying a GitHub credential:

- `FRANKENSTEIN_CLAUDE_EXPOSE` — colon-separated absolute paths bind-mounted
  **read-only** into the scratch home at their original locations. Defaults are
  `~/.claude/.credentials.json` and `~/.claude.json`; they are **candidates,
  not assumptions**.
- `FRANKENSTEIN_CLAUDE_ENV` — the environment variables forwarded into the
  child. Default: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
  `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `CLAUDE_CODE_OAUTH_TOKEN`,
  `CLAUDE_CONFIG_DIR`.

What the installed CLI actually needs is a property of the host, so it is
measured there rather than guessed — see **Probe** below. If the CLI itself is
installed under the home directory, its install directory must be added to
`FRANKENSTEIN_CLAUDE_EXPOSE`; the probe says so explicitly when it detects
that.

### The verification zone

Claude may edit `scripts/test.sh`. Running it on the host after the child exits
would be a direct escape: modify the script, exit 0, and the orchestrator
executes arbitrary code as the deploying user. The independent test re-run
therefore happens **inside the same namespace, with the same masks**. The
orchestrator inspects data afterwards; it does not execute code the child
produced.

### The publisher zone

The child owns `.git/hooks` and `.git/config` in its workspace, and a
`pre-push` hook or a `core.fsmonitor` / `uploadpack.packObjectsHook` entry there
would otherwise be executed by the trusted publisher. So:

- before **any** git command touches that clone again, `.git/config` is
  restored from a trusted copy taken before the run and `.git/hooks` is emptied
  — pure file operations, no git involved;
- every subsequent git command in that clone runs with
  `core.hooksPath=/dev/null`, `core.fsmonitor=` and
  `uploadpack.packObjectsHook=`;
- publication happens from a **fresh clone of origin** that the child never had
  access to. The implementation commit is imported into it as data, its SHA is
  checked against the commit verification actually ran on, and its ancestry
  against the authorized baseline, before the task ref is pushed;
- the handoff is written from a **fresh control clone**, and its content is read
  from the publisher, never from the child's workspace;
- both trusted clones carry a `pre-push` hook rejecting
  `production`/`main`/`master` and any non-fast-forward push, detected by
  `merge-base --is-ancestor`.

May: edit files in its workspace, run tests, commit.

May **not**: push or merge production, run `promote.sh` / `rollback.sh` /
`deploy.sh`, touch systemd, force push, use `sudo`, issue a directive, or change
scope. The worker source contains none of those invocations (asserted by test),
and every push in the script comes from `$PUB_DIR` or `$CONTROL_DIR`.

### How this is tested

Behaviorally, not by reading the source. The child is handed real attacks and
the test asserts they failed:

| attack | asserted |
|---|---|
| read `~/.config/gh/hosts.yml` | credential never obtained |
| read `~/.ssh/id_test`, list `~` | key never obtained; home shows only scratch |
| write `~/PWNED_HOME`, `~/.ssh/authorized_keys` | never appear on the host |
| read a file planted in `/var/tmp` | masked |
| write into the production checkout | never appears |
| `git push` to production, by remote name and by URL | production SHA unchanged; the child's `git remote -v` is empty and the bare remote is not even visible on disk |
| rewrite `scripts/test.sh` to write outside | marker never appears; run fails; nothing published |
| plant `.git/hooks/pre-push` | never executed; branch still published |
| set `uploadpack.packObjectsHook` and `core.hooksPath` | never honoured; config restored |
| ordinary edits, commits, `bash scripts/test.sh` | still work |

The last row matters as much as the others: containment that breaks honest work
is not containment, it is an outage.

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
trusting the run's claim — inside the sandbox, since the test script is a file
Claude may have edited.

Failure results recorded in `runs.jsonl`: `no_sandbox`, `no_workspace_mount`,
`sandbox_failed`, `claude_failed`, `no_branch`, `no_commits`, `tests_failed`,
`fetch_failed`, `control_conflict`, `publisher_clone_failed`,
`impl_import_failed`, `impl_mismatch`, `impl_not_descendant`, `push_failed`,
`control_clone_failed`, `control_fetch_failed`, `control_conflict_late`,
`control_reset_failed`, `state_write_failed`, `handoff_commit_failed`,
`handoff_push_rejected` — plus `dry_run` and `success`.

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

## Probe

```bash
bash scripts/claude-worker.sh --probe
```

Run on the host **before** activation. It is inert: no fetch, no push, no
deploy, no control read, no run record, and it does not require
`~/.frankenstein/agent/ENABLED`. It reports the chosen workspace mountpoint and
mask list, establishes the full containment around a throwaway directory, and
from inside it:

- asserts `~/.config/gh/hosts.yml`, `~/.gitconfig`, `~/.ssh/*` and
  `~/.frankenstein/deployed.json` are **not** visible, printing `LEAK:` for
  anything that is;
- shows what the home directory now contains, and that `GITHUB_TOKEN`,
  `GH_TOKEN` and `SSH_AUTH_SOCK` are unset;
- runs `claude -p 'Reply with the single word READY'` and reports whether the
  installed CLI could authenticate from inside the restricted environment.

It prints `RESULT: PASS` only when containment holds **and** Claude
authenticated. Anything else means the exposure list needs adjusting on that
host — not that the worker should be enabled anyway.

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

`--probe` must report `RESULT: PASS` on the host first.

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
