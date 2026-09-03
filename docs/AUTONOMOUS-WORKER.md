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

The directive rule is strict: **exactly one** `Task ID:` line, well formed, and
equal to `STATE.json.task_id`. Zero lines, two lines (even identical ones), a
malformed id and a different id all block the run. A nonempty directive is not
evidence of authorization; naming this exact task exactly once is.

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
| **publisher** | bundle import, ancestry checks, the two pushes | yes — and it executes nothing that came from the child |

### The rule that shapes the design

> Once the child has run, the trusted host never executes a command against,
> nor writes through, the child's workspace — including its `.git`.

Sanitizing that tree from the host was itself a vulnerability: the child can
replace `.git`, `.git/config` or any descendant with a symlink pointing
anywhere, so a host-side `cp` or `rm -rf` through those paths becomes a write
primitive outside the sandbox. The workspace is therefore not cleaned. It is
**abandoned**, and what it produced leaves as an inert artifact.

### Four sandbox invocations

Each is a separate namespace, so nothing — including a background process
started by `scripts/test.sh` — survives from one to the next.

| | invocation | contents |
|---|---|---|
| A | the child | Claude, or the mock |
| B | structure check | fixed git commands: the branch exists and carries commits |
| C | verification | `bash scripts/test.sh` |
| D | export | fixed git commands: write a bundle + manifest out |

The workspace itself is **ephemeral**: discarded and recreated from origin
every run, so nothing a previous child left can influence the next one. (`rm -rf`
removes symlinks rather than following them, which is what makes that one host
operation safe on a tree a previous child controlled — asserted by test.)

### The child zone

`env -i` hides environment variables. It does not hide the filesystem or the
process table, and the child runs under the same UID as the deploying user. So
the child gets a **user + mount + PID** namespace with a private `/proc`, in
which, in this order:

1. the production checkout is bind-mounted **read-only**;
2. the agent workspace is bind-mounted at a **neutral path outside the home**
   (`/mnt`, `/media` or `/srv` — the first empty ones) and becomes the working
   directory;
3. narrowly configured Claude authentication paths are bind-mounted
   **read-only** into a prepared scratch home;
4. `/etc/resolv.conf` is replaced by a staged copy, so masking `/run` cannot
   take DNS with it;
5. **the real home directory is replaced** by that scratch home. `~/.config/gh`,
   `~/.ssh`, `~/.gitconfig`, `~/.frankenstein`, shell configuration, the
   production checkout and the agent directory simply do not exist for the
   child;
6. `/run`, `/tmp` and `/var/tmp` get a **private tmpfs** — skipping any that is
   an ancestor of something the child legitimately needs.

`/run` matters as much as the home: Docker on this host is usable without
`sudo`, so `/run/docker.sock` is host-control capability. Masking `/run`
wholesale beats enumerating sockets forever, and covers `/run/user/<uid>`,
container sockets and service sockets in one move.

The PID namespace is part of the **credential** boundary, not a nicety: without
it the child reads `/proc/<pid>/environ` for every process this user owns, and
`env -i` buys nothing.

On top of that the clone's `origin` remote is removed for the duration of the
run, and the environment is rebuilt from nothing with `env -i`: no
`GITHUB_TOKEN`, no `GH_TOKEN`, no `SSH_AUTH_SOCK`,
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`,
`GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=/bin/false`.

If the namespaces cannot be established the worker **refuses to run**.
`FRANKENSTEIN_ALLOW_UNSANDBOXED=1` exists for hosts without unprivileged user
namespaces; it removes the boundary, and the systemd template never sets it.

### Claude's own authentication

The child needs to reach Claude, and nothing else. Two narrow channels, both
opt-in, neither carrying a GitHub credential:

- `FRANKENSTEIN_CLAUDE_EXPOSE` — colon-separated absolute paths bind-mounted
  **read-only** into the scratch home at their original locations. Defaults:
  `~/.claude/.credentials.json` and `~/.claude.json` — **candidates, not
  assumptions**.
- `FRANKENSTEIN_CLAUDE_ENV` — variables forwarded into the child. Default:
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_MODEL`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CONFIG_DIR`.

What the installed CLI actually needs is a property of the host, so `--probe`
measures it there. If the CLI itself lives under the home directory, its install
directory must be added to `FRANKENSTEIN_CLAUDE_EXPOSE`; the probe says so.

### The export: the only thing that crosses back

Invocation D writes into a directory mounted **only for that invocation**, so
the child never saw it:

```
implementation.bundle    a git bundle of refs/heads/claude/FC-###-work
implementation.sha       the commit it claims to carry
tree.sha
task_branch
```

Every git command in B and D runs with `core.hooksPath=/dev/null`,
`core.fsmonitor=`, `uploadpack.packObjectsHook=` and
`protocol.ext.allow=never`, so nothing in the workspace's git config executes
even inside the sandbox.

### The publisher zone

A **fresh clone of origin**, recreated each run. It consumes the bundle as data
and checks it against what the host authorized *before* the child existed:

- the bundle verifies;
- the imported commit equals the exported SHA;
- it is not the baseline (the run produced something);
- it **descends from the baseline** recorded before the child ran;
- it carries `.frankenstein/AUTHORIZING_CONTROL_COMMIT` equal to the
  authorizing control commit;
- its `STATE.json` names this task.

Only then is the task ref pushed. The handoff is written from a **fresh control
clone**, with content read from the publisher's object database. Both trusted
clones carry a `pre-push` hook rejecting `production`/`main`/`master` and any
non-fast-forward push, detected by `merge-base --is-ancestor`.

May: edit files in its workspace, run tests, commit.

May **not**: push or merge production, run `promote.sh` / `rollback.sh` /
`deploy.sh`, touch systemd, force push, use `sudo`, issue a directive, or change
scope. The worker source contains none of those invocations (asserted by test),
and every push comes from `$PUB_DIR` or `$CONTROL_DIR`.

### How this is tested

Behaviorally, not by reading the source. The child is handed real attacks:

| attack | asserted |
|---|---|
| read `~/.config/gh/hosts.yml` | credential never obtained |
| read `~/.ssh/id_test`, list `~` | key never obtained; home shows only scratch |
| write `~/PWNED_HOME`, `~/.ssh/authorized_keys` | never appear on the host |
| scan `/proc/*/environ` for a secret in the parent | never observed; the process table is the sandbox's own |
| list `/run`, reach `docker.sock`, find any socket | none visible; `/run` is a private tmpfs |
| read a file planted in `/var/tmp` | masked |
| write into the production checkout | never appears |
| `git push` to production, by remote name and by URL | production unchanged; `git remote -v` empty; the bare remote not visible on disk |
| rewrite `scripts/test.sh` to write outside | marker never appears; run fails; nothing published |
| plant `.git/hooks/pre-push` | never executed; branch still published; the planted file is still there afterwards, untouched |
| set `uploadpack.packObjectsHook`, `core.hooksPath` | never honoured |
| `rm -rf .git && ln -s <host dir> .git` | the host directory and its contents survive this run **and the next run's cleanup**; nothing is published |
| symlink `.git/config` at a host file and `.git/hooks` at a host directory | file unmodified, directory not deleted |
| ordinary edits, commits, `bash scripts/test.sh` | still work, still export, still publish |

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
`sandbox_failed`, `claude_failed`, `no_branch`, `no_commits`,
`workspace_unreadable`, `tests_failed`, `export_failed`, `export_incomplete`,
`export_branch_mismatch`, `fetch_failed`, `control_conflict`,
`publisher_clone_failed`, `bundle_invalid`, `impl_import_failed`,
`impl_mismatch`, `impl_not_descendant`, `authorizing_snapshot_missing`,
`task_id_mismatch`, `push_failed`, `control_clone_failed`,
`control_fetch_failed`, `control_conflict_late`, `control_reset_failed`,
`state_write_failed`, `handoff_commit_failed`, `handoff_push_rejected` — plus
`dry_run` and `success`.

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

Run on the host **before** activation. It makes no GitHub ref change, no
deployment and runs no product task; it does create and remove temporary
directories under `~/.frankenstein/agent/`. It does not require
`~/.frankenstein/agent/ENABLED`.

It reports the chosen mountpoints and mask list, then measures, from inside the
full containment:

```
PASS  user namespace
PASS  mount namespace
PASS  PID namespace / private proc
PASS  workspace mountpoint available
PASS  export mountpoint available
PASS  host process environment inaccessible via /proc
PASS  private /proc shows only sandbox processes
PASS  docker socket unavailable
PASS  host /run replaced by a private tmpfs
PASS  no host runtime state visible under /run
PASS  no host runtime sockets reachable
PASS  real home hidden (gh, ssh, gitconfig, .frankenstein)
PASS  no GitHub or SSH credential in the environment
PASS  workspace writable
PASS  scratch home writable
PASS  production checkout not writable
PASS  export directory writable
PASS  Claude authenticated from inside containment
```

`RESULT: PASS` **exits 0**. `RESULT: NOT READY` **exits non-zero**, so it can
never be mistaken for success by a script or a skim. Anything less than PASS
means the exposure list needs adjusting on that host — not that the worker
should be enabled anyway.

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
