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

| | invocation | contents | home | Claude credential | network |
|---|---|---|---|---|---|
| A | the child | Claude, or the mock | child scratch | yes, read-only | allowlisted egress |
| B | structure check | fixed git commands | tool scratch | no | none |
| C | verification | `bash scripts/test.sh` | tool scratch | no | none |
| D | export | fixed git commands | tool scratch | no | none |

Two separate scratch homes, not one. B, C and D get a home the child never had
access to, so no credential — staged or copied there by the child — is present
when child-controlled code runs during verification.

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

### Network containment

Filesystem isolation does not protect the running product from HTTP or TCP. A
child sharing the OptiPlex network stack could reach `127.0.0.1` services, the
box's own LAN address, the Frankenstein service ports, PostgreSQL, other LAN
devices, and link-local ranges — and a malicious `scripts/test.sh` could post a
credential anywhere.

So **every invocation gets a private, empty network namespace** (`--net`). There
is no route to anything. Loopback is brought up inside it, so `127.0.0.1` is the
sandbox's own loopback and reaches nothing on the host. `ip` is not assumed to
exist; the interface is brought up with the `SIOCSIFFLAGS` ioctl directly.

B, C and D stop there: no egress at all.

A needs to reach Claude, so it gets exactly that and nothing more, through a
two-part channel that needs no privileged tooling and no third-party package:

```
child (private netns)                     host
  HTTPS_PROXY=127.0.0.1:8118
        │ TCP
        ▼
  egress-relay.py  ──── UNIX socket ────▶  egress-proxy.py ──▶ api.anthropic.com:443
  (dumb pump, in the netns)                (HTTP CONNECT, allowlisted)
```

The UNIX socket is a filesystem object inside the child's scratch home, so it
crosses the network namespace without giving the child a route. The host half
speaks HTTP CONNECT and applies **two independent checks**, because a name is
not an address:

1. `host:port` must be on `FRANKENSTEIN_EGRESS_ALLOW` (default
   `api.anthropic.com:443`);
2. **every** address that name resolves to must be a global unicast address —
   which is what stops a DNS answer pointing at `127.0.0.1`, the OptiPlex's LAN
   address, `169.254.0.0/16`, `::1`, `fe80::/10` or `fc00::/7` from becoming a
   tunnel into the house.

Both helpers run under `env -i` inside the namespace. That is not cosmetic: they
live in the child's PID namespace, so anything they inherited would be readable
at `/proc/<pid>/environ` by the process this boundary exists to contain. The
probe caught exactly that during development.

If the egress channel cannot be started, the worker refuses to run the child
rather than falling back to the host network stack.

### Claude's own authentication

The child needs to reach Claude, and nothing else. Two narrow channels, both
opt-in, neither carrying a GitHub credential:

- `FRANKENSTEIN_CLAUDE_EXPOSE` — colon-separated absolute paths bind-mounted
  **read-only** into the scratch home at their original locations. Default:
  `~/.claude/.credentials.json`.
- `FRANKENSTEIN_CLAUDE_WRITABLE` — paths **copied** into the scratch home
  instead. Default: `~/.claude.json`, which the CLI rewrites on startup, so a
  read-only bind would break it. The child edits a throwaway copy and the
  host's file is never touched by a run.
- `FRANKENSTEIN_CLAUDE_ENV` — variables forwarded into the child. Default:
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_MODEL`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CONFIG_DIR`.

**The CLI's own install directory is detected, not configured.** A CLI
installed with `npm --prefix` (`~/.npm-global/bin/claude`), or an interpreter
under `nvm`, lives inside the home directory the mask hides — so the child
would not find the binary at all. The worker resolves `claude` and `node` on
`PATH`, and when either sits under the home it exposes that install root
read-only. Read-only matters: the child must not be able to rewrite the CLI the
next run executes. The install root is exposed to the **child only** — the
verification zone cannot see it.

This is the one place a real host differed from the design, and the probe is
what found it: on the OptiPlex the CLI is at `~/.npm-global/bin/claude` and the
child got `exit 127, No such file or directory`.

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
- `IMPLEMENTATION_HANDOFF.md` exists, is non-empty, and carries the documented
  `Deviations From Directive` section;
- and its `STATE.json` is a **complete, valid handoff**, not merely one that
  names the right task:

| field | required value |
|---|---|
| `protocol_version` | the supported version |
| `task_id` | the authorized task |
| `turn` | `product_owner` |
| `status` | `awaiting_review` |
| `last_actor` | `claude` |
| `directive_commit` | exactly what the Product Owner set — a run may not rewrite the directive's identity |

That last group is a safety property, not bookkeeping. A run that left
`turn: claude` with `ready_for_implementation` or `changes_requested` would
**re-authorize itself on the very next poll**, and one that wrote
`status: accepted` would have accepted its own work. Neither is publishable.

`implementation_commit` is the publisher's to set, and it sets it to the exact
commit it imported and verified.

Only then is the task ref pushed. After the handoff lands, the worker re-reads
the control commit it just created and asserts it says
`product_owner / awaiting_review / <implementation SHA> / claude`; anything else
fails the run loudly rather than leaving control in a state nobody checked. The handoff is written from a **fresh control
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
| write to the exposed CLI install root, or add a file to it | refused; the launcher and package are unchanged |
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
| connect to a host loopback listener, the box's LAN address, `169.254.169.254` | all refused; the decoy listeners record zero connections |
| connect to `::1`, `fe80::1`, `fc00::1` | all refused |
| use the egress channel for `github.com`, `localhost:5432`, a LAN address | `403` from the proxy in every case |
| rewrite `scripts/test.sh` to dump its environment, read the credential file, and phone home | no Claude credential in the environment or on disk, no proxy configured, no route out, decoy listener untouched |
| leave `turn: claude`, or `status: accepted`, or rewrite `directive_commit`, or rename the task, or bump `protocol_version`, or claim the Product Owner acted | nothing published, control unmoved |
| omit `IMPLEMENTATION_HANDOFF.md`, or omit its Deviations section | nothing published, control unmoved |
| a CLI installed at `~/.npm-global/bin/…` | still executable by the child, still invisible to verification |
| rewrite the host's `~/.claude.json` | the child rewrites its own copy; the host's file is byte-identical afterwards |
| ordinary edits, commits, `bash scripts/test.sh` | still work, still export, still publish; control reads back correctly and the next poll is a NO-OP |

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
`workspace_unreadable`, `tests_failed`, `egress_unavailable`, `export_failed`,
`export_incomplete`,
`export_branch_mismatch`, `fetch_failed`, `control_conflict`,
`publisher_clone_failed`, `bundle_invalid`, `impl_import_failed`,
`impl_mismatch`, `impl_not_descendant`, `authorizing_snapshot_missing`,
`handoff_missing`, `handoff_incomplete`, `handoff_state_invalid`,
`published_state_wrong`, `push_failed`, `control_clone_failed`,
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

It starts decoy listeners on the host's loopback and on the box's own LAN
address, puts a marker secret in its own environment, then measures — rather
than asserts — from inside the full containment:

```
PASS  user namespace
PASS  mount namespace
PASS  PID namespace / private proc
PASS  network namespace
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
PASS  child cannot reach a host loopback listener
PASS  child cannot reach the host RFC1918 LAN address
PASS  child cannot reach IPv4 link-local / metadata
PASS  child cannot reach IPv6 loopback
PASS  child cannot reach IPv6 link-local
PASS  child cannot reach IPv6 unique-local

PASS  verification has no Claude credential in its environment
PASS  verification has no egress proxy configured
PASS  verification cannot read exposed Claude credential files
PASS  verification has no egress socket
PASS  verification cannot reach a host loopback listener
PASS  verification cannot reach the host LAN address
PASS  verification has no outbound network at all

PASS  Claude API authentication through the permitted egress
```

`RESULT: PASS` **exits 0**. `RESULT: NOT READY` **exits non-zero**, so it can
never be mistaken for success by a script or a skim. Anything less than PASS
means the exposure list or the egress allowlist needs adjusting on that host —
not that the worker should be enabled anyway.

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
