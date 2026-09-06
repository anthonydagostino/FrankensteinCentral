> # SUPERSEDED — DO NOT EXECUTE
>
> Superseded 2026-09-06 by `.frankenstein/RELEASE_AUTOMATION.md`, on Product
> Owner decision. This procedure made Anthony the only actor able to move
> production, which is explicitly not the desired end state: he is a product
> stakeholder, not the routine release operator. The two-lane credential work
> below is largely reusable, but the model it serves has changed — read the
> replacement first, and execute nothing from this file.

# Production + Control Write Enforcement — Human-Run Procedure

Written: 2026-09-06 (rev 2 — two-lane model)
Author: Claude (protocol agent, OptiPlex)
Status: **PREPARATION ONLY. NOTHING HAS BEEN EXECUTED.**

Supersedes rev 1 (`f5d4782`), which was defective: it gave every agent one
repository-wide deploy key and then *verified* that the same key could write
`control`. The Product Owner was right — that key is readable by any process
running as the OptiPlex user, so the money, CM and any future lane could have
rewritten `STATE.json`, forged a directive, set `turn=claude`, and woken the
worker with syntactically valid state. The worker's validation cannot defend
against an attacker who can write valid control state.

**No key generated. No remote repointed. No ruleset created. No deploy key
added. `gh` still logged in. `~/.ssh` still holds no private keys.**

---

## What changed since rev 1, and why

Two findings drive the redesign.

**1. Two deploy keys cannot be told apart by a ruleset.** The Product Owner was
correct that `DeployKey` is a valid ruleset bypass actor type — my rev-1 claim
that it was not was wrong. But the API requires `actor_id: null` for it, which
means it is a **blanket** actor: it matches *every* deploy key on the
repository, not a chosen one. So "one deploy key for agents, another for the
protocol lane, separated by a ruleset" **cannot work**. The valid actor types
are `Integration`, `OrganizationAdmin`, `RepositoryRole`, `Team`, `DeployKey`
and **`User`** — and `User` does take a specific `actor_id`.

**2. The OptiPlex user is in the `docker` group.** `sudo` requires a password
here (good), so a second Unix user genuinely hides a file from a plain session.
But `antdag3` is in `docker`, and docker group membership is equivalent to
root: any session there can `docker run -v /:/host` and read any file on the
box. **A protocol credential stored under a second Unix user is not protected
from a Claude session running as `antdag3`.** OS separation only works if the
*general agent* lane runs as a user that has neither `sudo` nor `docker`.

---

## The two-lane model

| lane | GitHub identity | may push | may NOT push |
|---|---|---|---|
| **A — general agents** (money/product, CM/docs, ad-hoc sessions) | repository **deploy key** | task branches | `production`, `control` |
| **B — protocol publisher** (this lane, and later the autonomous worker) | **machine user account** with its own fine-grained PAT | task branches, `control` | `production` |
| **C — human** (Anthony / Product Owner) | his own account, admin, **not stored on the box** | everything, incl. `production` | — |

Server-side rules that make it true:

- **`production` ruleset** — restrict updates, block force pushes, block
  deletions. Bypass: **admin only**. Neither the deploy key (lane A) nor the
  machine user (lane B) is in the bypass list.
- **`control` ruleset** — restrict updates. Bypass: **the machine user
  (`User` actor) + admin**. `DeployKey` is deliberately absent, which is what
  blocks lane A.

Lane A is blocked from `control` by the *absence* of `DeployKey` in that bypass
list — the blanket nature of the DeployKey actor works in our favour here, since
we never want any deploy key touching `control`.

### If the `User` bypass actor turns out to be unavailable

This repository is personally owned, and I could not create a ruleset read-only
to confirm the UI exposes user bypass actors. **Phase 0 decides this before
anything is created.** If `User` bypass is not selectable, use the fallback:

> **Fallback — move `control` into its own repository.** Deploy keys are scoped
> per repository by construction, so lane A's key on `FrankensteinCentral`
> simply has no access to a `FrankensteinCentral-control` repo, and lane B gets
> a deploy key there. No ruleset needed for `control` at all, and no untested
> actor semantics. The cost is a narrow code change: `claude-worker.sh` already
> parameterises the branch (`FRANKENSTEIN_CONTROL_BRANCH`) but reads it from
> `origin`, so it would need a second remote — roughly 15–20 lines plus tests,
> in `claude-worker.sh` and `control-bootstrap.sh`.

Primary is the machine user because it needs **zero code change**. The fallback
is strictly more robust. Phase 0 costs ten minutes and picks the right one.

---

## Phase 0 — capability probe (creates nothing enforcing)

**GitHub UI.** Settings → Rules → Rulesets → **New branch ruleset**.

- Name it `probe-delete-me`
- **Enforcement status: `Evaluate`** — this mode logs and does **not** enforce.
- Target branch: `control`
- Open the **Bypass list** → **Add bypass** and look at what actor types are
  offered.

**What to record:** whether you can add **a specific user account** as a bypass
actor, and whether `Deploy keys` appears as an option.

Then **delete the probe ruleset**. It enforced nothing.

- Specific users selectable → **primary model**, continue to Phase 1.
- Not selectable → **fallback model**; stop and report, and I will prepare the
  separate-control-repo variant before you touch any credential.

---

## Phase 1 — lane A identity (deploy key for general agents)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/fc_agents -C "optiplex-lane-a-agents" -N ""
chmod 600 ~/.ssh/fc_agents
cat ~/.ssh/fc_agents.pub
```

**Expected:** one line, `ssh-ed25519 AAAAC3… optiplex-lane-a-agents`. Never
`cat` the file without `.pub`.

**GitHub UI.** Settings → **Deploy keys** → Add deploy key.
Title `optiplex lane A — agents (write, no control, no production)`, paste the
`.pub` line, **Allow write access ☑**, Add key.

---

## Phase 2 — pin host keys and configure SSH (both lanes)

```bash
curl -fsS https://api.github.com/meta \
| python3 -c 'import json,sys; [print("github.com", k) for k in json.load(sys.stdin)["ssh_keys"]]' \
>> ~/.ssh/known_hosts
grep -c '^github.com ' ~/.ssh/known_hosts
ssh-keygen -lf ~/.ssh/known_hosts | grep -i ed25519
```

**Expected:** `3`, and fingerprint
`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU` — read from
`api.github.com/meta` over TLS on this box and matching GitHub's published
value. If it differs, **stop**.

```bash
cat >> ~/.ssh/config <<'EOF'

# FrankensteinCentral lane A — general agents. Task branches only.
Host github-fc-agents
    HostName github.com
    User git
    IdentityFile ~/.ssh/fc_agents
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github-fc-agents
```

**Expected:** `Hi anthonydagostino/FrankensteinCentral! You've successfully
authenticated…`, exit status 1 (normal). It must greet you by **repository**,
not by username — a username greeting means a personal key was used and the
boundary is void.

---

## Phase 3 — lane B identity (machine user)

**Human account work — GitHub UI and email, no commands.**

1. Create a second GitHub account, e.g. `frankenstein-protocol-bot`. It needs
   its own email address (a `+` alias on your existing mailbox is fine).
   GitHub permits machine accounts; this is their intended use.
2. From **your** account: Settings → Collaborators → invite that account with
   **Write** access. Accept the invitation from the bot account.
3. Signed in **as the bot**: Settings → Developer settings → Personal access
   tokens → **Fine-grained tokens** → Generate. Scope it to **only**
   `anthonydagostino/FrankensteinCentral`, permission **Contents: Read and
   write**, and set an expiry you are willing to rotate.
4. Copy the token **once**, directly into the file created in Phase 4. Do not
   paste it into this conversation, a chat window, or any file that is
   committed.

---

## Phase 4 — OS separation for the lane B credential

This is the part rev 1 got wrong, and it needs a decision from you.

**The problem:** `antdag3` is in the `docker` group, which is root-equivalent —
a session there can mount any path into a container and read it. So putting the
bot token under a second Unix user does **not** hide it from a Claude session
running as `antdag3`.

**Therefore the general-agent lane must move, not the protocol lane.**

```bash
# a Unix user for lane A with NEITHER sudo NOR docker
sudo adduser --disabled-password --gecos "" fcagent
id fcagent          # expect: groups=fcagent  — and nothing else
```

**Expected:** `id fcagent` shows only its own group. If `docker` or `sudo`
appear, stop — the separation is void.

Then the lane B token lives under `antdag3`, unreadable by `fcagent`:

```bash
install -m 700 -d ~/.frankenstein/creds
umask 077; printf '%s\n' 'PASTE_BOT_TOKEN_HERE' > ~/.frankenstein/creds/bot-token
chmod 600 ~/.frankenstein/creds/bot-token
ls -l ~/.frankenstein/creds/bot-token      # expect -rw------- antdag3
sudo -u fcagent cat ~/.frankenstein/creds/bot-token   # expect: Permission denied
```

**Expected:** the last command **fails**. That failure is the separation.

From then on: money, CM and ad-hoc Claude sessions are started as `fcagent`
(`sudo -u fcagent -i`, or an ssh login), with their clones under
`/home/fcagent`. The protocol lane and the autonomous worker keep running as
`antdag3`.

**Answer to "is a separate Unix service user needed": yes — for lane A.**
Without it, every lane can read every credential on the box and the GitHub-side
rules are the only thing left standing.

---

## Phase 5 — repoint remotes

```bash
# lane B (protocol) — HTTPS with the bot token via a credential file
git -C ~/frankenstein-protocol remote set-url origin \
  https://github.com/anthonydagostino/FrankensteinCentral.git
git -C ~/frankenstein-protocol config credential.helper \
  '!f() { echo username=frankenstein-protocol-bot; echo "password=$(cat $HOME/.frankenstein/creds/bot-token)"; }; f'
git -C ~/frankenstein-protocol remote -v
git -C ~/FrankensteinCentral remote -v
```

Lane A clones (under `/home/fcagent`) use the SSH alias:
`git@github-fc-agents:anthonydagostino/FrankensteinCentral.git`

**`~/FrankensteinCentral` stays on HTTPS with no credential.** It only fetches,
the repo is public, and anonymous fetch was verified working with the helper
disabled. Deployment therefore survives every credential change below.

---

## Phase 6 — prove both lanes work BEFORE removing anything

```bash
cd ~/frankenstein-protocol
git fetch origin
git push origin origin/control:refs/heads/tmp-laneB-check
git push origin --delete tmp-laneB-check
git ls-remote origin production        # expect 9b96bd0…, unchanged
```

**Expected:** fetch and push succeed with no prompt; production unchanged.

Lane A, as `fcagent` in its own clone: a task-branch push must succeed the same
way. If either fails, **stop and use Recovery** — nothing has been removed yet.

---

## Phase 7 — the rulesets

**GitHub UI.** Settings → Rules → Rulesets.

**Ruleset 1 — `production is human-only`**
- Enforcement: **Active**
- Target: branch `production`
- Bypass: **admin only** — not the deploy key, not the bot user
- Rules by effect: prevent the branch being **updated** outside the bypass list;
  **block force pushes**; **block deletions**

**Ruleset 2 — `control is protocol-only`**
- Enforcement: **Active**
- Target: branch `control`
- Bypass: **the bot user + admin**. **Do not add `Deploy keys`** — its absence
  is exactly what blocks lane A.
- Rules by effect: prevent the branch being **updated** outside the bypass list;
  block force pushes; block deletions

Labels vary by account type; match the *effect*, then let Phase 9 prove it
rather than trusting the screen.

---

## Phase 8 — remove Anthony's unrestricted credential from the box

Only after Phases 6 and 7 pass.

```bash
gh auth status
gh auth logout --hostname github.com
gh auth status
git config --global --unset-all credential.https://github.com.helper
git -C ~/FrankensteinCentral fetch --prune origin production && echo FETCH_STILL_WORKS
bash ~/FrankensteinCentral/scripts/frankenstein-status.sh
```

**Expected:** not logged in; `FETCH_STILL_WORKS` prints; status still shows
desired `9b96bd0` == running `9b96bd0`, result success. (`--unset-all` because
that key currently has two values.) **Your workstation and browser are
untouched.**

---

## Phase 9 — behavioral verification (the actual proof)

Run after logout, so it genuinely runs as each lane's own credential.

**A. lane A task branch → MUST SUCCEED** (as `fcagent`)
```bash
git push origin origin/production:refs/heads/tmp-laneA-check
```

**B. lane A pushing `control` → MUST BE REJECTED** (as `fcagent`)
```bash
git push origin origin/control:refs/heads/control
```
Expect a `remote:` rule-violation rejection. **This is the check rev 1 was
missing.**

**C. lane B pushing `control` → MUST SUCCEED** (as `antdag3`)
```bash
cd ~/frankenstein-protocol && git push origin origin/control:refs/heads/tmp-laneB2-check
```

**D. lane B pushing `production` → MUST BE REJECTED**
```bash
git checkout -q --detach origin/production
git commit -q --allow-empty -m "enforcement probe: must be rejected"
git push origin HEAD:refs/heads/production
```

Expect `remote: … GH013: Repository rule violations found` (or equivalent) and
a non-zero exit. The `remote:` prefix is the proof. **There are no local git
hooks on this box and `core.hooksPath` is unset** (verified), so a rejection
cannot have come from our own tooling.

**If D succeeds, STOP and report.** Damage is contained by design: the probe
commit is empty, so production's tree stays byte-identical to `9b96bd0` and the
poller redeploys the same tree. Do not force-push; production is append-only.

**E. cleanup**
```bash
git push origin --delete tmp-laneA-check tmp-laneB2-check
git checkout -q claude/activation-reconcile
git ls-remote origin production     # expect 9b96bd0…
```

---

## Recovery — and why you cannot be locked out

**The escape hatch:** a repository admin can always **disable or delete a
ruleset** in Settings → Rules, from any browser, with no credential on the box.
Rulesets cannot permanently lock you out of your own repository. If anything
behaves unexpectedly, set the ruleset's enforcement to `Disabled` and
everything reverts to today's behaviour immediately.

**SSH or token fails before Phase 8:** nothing has been removed; the OAuth login
still works.
```bash
git -C ~/frankenstein-protocol config --unset credential.helper
git -C ~/frankenstein-protocol remote set-url origin \
  https://github.com/anthonydagostino/FrankensteinCentral.git
git -C ~/frankenstein-protocol fetch origin && echo HTTPS_PATH_OK
```

**After Phase 8:** deployment is unaffected — the poller fetches anonymously
(verified). To restore write access, run `gh auth login --hostname github.com`
on the box and re-authorize. Production is never at risk in this state, because
nothing on the box can write to it.

**Order rule:** never delete a deploy key or revoke the bot token while a clone
still points at it. Repoint to HTTPS first.

---

## What to paste back (secrets excluded)

`git remote -v` for each clone · the `ssh -T` greeting line · `id fcagent` ·
the `sudo -u fcagent cat …` permission-denied line · every Phase 6 and Phase 9
result, success **and** failure · **the full `remote:` rejection text from 9B
and 9D** · `gh auth status` after logout · `frankenstein-status.sh` ·
`git ls-remote origin production` · key fingerprints if wanted
(`ssh-keygen -lf ~/.ssh/fc_agents.pub`).

**Never paste:** any private key, the bot token, `~/.config/gh/hosts.yml`, or
any file under `~/.frankenstein/creds/`.

---

## What changes operationally

| | before | after |
|---|---|---|
| general agents push task branches | yes | **yes** (deploy key) |
| general agents push `control` | **yes — nothing stopped them** | **no — GitHub refuses** |
| general agents push `production` | **yes** | **no — GitHub refuses** |
| protocol lane pushes `control` | yes | **yes** (bot user) |
| protocol lane pushes `production` | yes | **no — GitHub refuses** |
| `promote.sh` / `rollback.sh` from the box | work | **intentionally stop working** |
| deployment / poller | works | **unchanged** (fetch-only, anonymous) |
| Anthony's workstation & browser | — | **untouched** |
| where general agents run | as `antdag3` | **as `fcagent`**, no sudo, no docker |

Promotion and emergency rollback become human acts performed from a credential
the agents cannot reach.

**The residual risk, stated plainly.** Lane B's token is readable by anything
running as `antdag3`, which includes the protocol lane and the autonomous
worker — by design, they are lane B. It is *not* readable by lane A once lane A
runs as `fcagent`. If a general-agent session is ever started as `antdag3` out
of habit, it silently regains `control` access. That is the one operational
discipline this design still depends on, and it is worth deciding now whether
that is acceptable or whether the separate-control-repo fallback is preferable
regardless of Phase 0's outcome.

---

Prepared for Product Owner review. Nothing will be executed by me.
