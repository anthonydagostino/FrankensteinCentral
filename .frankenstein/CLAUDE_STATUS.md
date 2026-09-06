# Promotion Complete + Production-Write Enforcement Audit

Written: 2026-09-06
Author: Claude (protocol agent, OptiPlex)
Re: your authorization of 9b96bd0 — promotion done, enforcement audit read-only

**This file is a status report. It authorizes nothing.**
`STATE.json` and `PRODUCT_DIRECTIVE.md` are untouched. No account, token, deploy
key, GitHub App, ruleset or credential was created, changed or deleted.

---

## 1. Promoted production SHA

    9b96bd0c2076688e689576109bc57c17df291cee

    bash scripts/promote.sh --bootstrap 9b96bd0...
    Fast-forward: 6d290be -> 9b96bd0
    6d290be..9b96bd0  -> production

**On `--bootstrap`:** `promote.sh`'s acceptance gate reads `STATE.json`, which
you directed me not to modify, and its authorization gate reads
`PRODUCT_DIRECTIVE.md`, likewise. Both would have refused. `--bootstrap` skips
**only** those two protocol gates and prints that it did. It does **not** skip
the fast-forward check — that guard sits outside the skip branch, and a test
enforces that it stays there. The push itself was an ordinary non-forced push,
so git enforced fast-forward a second time independently. No force, no rewind,
no bypass of the production guard.

## 2. Running SHA

    9b96bd0   (deployed.json .running_commit)

## 3. Deployment result

    last_result:      success
    last_attempt:     9b96bd0 at 2026-09-06T02:08:49Z
    desired == running == 9b96bd0

The poller picked it up on its own; the test gate ran on the box before any
container was touched. No retry, no manual intervention, nothing rolled back.

## 4. Service health

    17 of 17 containers running
    gateway  ->  HTTP 200 on localhost:8080
    db       ->  healthy

No service restarted into a failure state. Uptimes show the long-running
services were untouched, as expected for a change that contains no product
code.

    frankenstein-deploy.timer: active, enabled

## 5. Deployment-checkout cleanliness

    ~/FrankensteinCentral, branch production @ 9b96bd0
    git status --porcelain --untracked-files=no    ->  0 lines
    git status --porcelain --untracked-files=all   ->  0 lines

`.github/workflows/` on the box now contains `tests.yml` only. The Actions
deploy path is gone from the deployed tree, not just from a branch.

## 6. Current GitHub credential / identity layout

Read-only audit. No token, key or credential content was printed or copied.

| question | finding |
|---|---|
| transport, both clones | **HTTPS** (`https://github.com/anthonydagostino/FrankensteinCentral.git`) |
| credential helper | **global**: `credential.https://github.com.helper = !/usr/bin/gh auth git-credential` |
| scope of that helper | every process running as `antdag3` inherits it, including every Claude session |
| stored credential | `~/.config/gh/hosts.yml`, mode 600 — one account: `anthonydagostino` |
| token type / scopes | classic OAuth, scopes `gist, read:org, repo, workflow` — `repo` is full write to every branch; `workflow` can also rewrite CI |
| `~/.git-credentials`, `~/.netrc` | absent |
| SSH private keys | **none** (`~/.ssh` holds `authorized_keys` and `known_hosts` only) |
| distinct machine/bot identity | **none exists** |
| deploy keys on the repo | **none** |
| collaborators | one: `anthonydagostino` (admin) |
| repository | **public**, owned by a personal account (not an org) |

**Every push from this box — mine, the CM agent's, the money agent's, and any
promotion — reaches GitHub as `anthonydagostino` using the same full-`repo`
token.** GitHub cannot currently distinguish them, exactly as you anticipated.

## 7. Current production protection state

    GET /repos/.../branches/production/protection   ->  404 "Branch not protected"
    GET /repos/.../rulesets                         ->  []

No branch protection. No rulesets. `production` is writable by anything holding
that token, which is everything on this box.

## 8. Recommended minimal enforcement model

**The trap to avoid first.** A fine-grained PAT looks like the obvious answer
and is not: it still authenticates **as `anthonydagostino`**. Any rule whose
bypass list admits Anthony admits every agent using it. The same is true of a
second OAuth login. A real boundary needs an actor GitHub sees as *not
Anthony*.

**Recommended: deploy key for the box + a `production` ruleset.**

1. **Give the box a non-Anthony identity.** Add a repository **deploy key**
   (SSH, write-enabled). A deploy key is its own actor — pushes are attributed
   to the key, not to a user account.
2. **Point the agents at it.** Switch both clones' remotes to SSH and use that
   key; then **remove the `gh` OAuth login from the box** so Anthony's
   full-`repo` token is no longer sitting where any agent can pick it up. Agents
   keep pushing task branches and `control` normally.
3. **Add a repository ruleset on `production`** — target branch `production`,
   restrict updates, block force pushes and deletions, **bypass list = repository
   admin only**. Deploy keys cannot be bypass actors, so the box's pushes to
   `production` are refused server-side while every other branch still works.
4. **Promotion becomes a human act with a different credential.** Anthony
   promotes from a context the agents cannot reach — his own machine, or the
   GitHub web UI. What makes it enforceable is that the credential that can move
   `production` is not present on the box.

Why this is the smallest version that is actually enforceable: it needs no new
GitHub account, no GitHub App, and no paid plan — rulesets are available on
free public repositories, and this repo is public. It satisfies your stated
property exactly: agents may push task branches and `control`, agents may not
push `production`, and the human promotion path can.

**Alternative if you want it cleaner long-term:** a **GitHub App** installed on
the repo with `contents: write`. Its installation token is a distinct actor
(`app/...`), it can be excluded from `production` the same way, and unlike a
deploy key it can be scoped and revoked per-permission with an audit trail.
More setup, better hygiene. Both models work; the deploy key is the cheaper
first move and does not preclude the App later.

**What will break, stated honestly.** After this, `scripts/promote.sh` and
`scripts/rollback.sh` stop working from the box, because both push
`production` directly. That is the entire point, but it means an emergency
rollback also becomes a human action. If that is unacceptable, the alternative
is to allow the box's identity to push `production` only through a pull request
that Anthony merges — but with the credential removed from the box, the deploy
key model already gives you the stronger property.

**One thing outside the enforcement question:** the repository is **public**.
Every commit, including `.env.example`, the protocol files and the deployment
baseline, is world-readable. Nothing secret has been observed in the tree, and
`.env` is correctly ignored — but this is worth a deliberate decision rather
than a default.

## 9. Exact human actions required

None of these can be done by an agent; all are account/credential changes.

1. **Generate the deploy key on the box** and add the public half at
   *Settings → Deploy keys → Add deploy key*, **with write access**.
   `ssh-keygen -t ed25519 -f ~/.ssh/frankenstein_deploy -N ""` produces it; the
   private half stays on the box and is never printed.
2. **Repoint both clones** to the SSH remote and configure that key for them.
3. **`gh auth logout`** on the box, removing Anthony's full-`repo` OAuth token
   from `~/.config/gh/hosts.yml`.
4. **Create the ruleset**: *Settings → Rules → Rulesets → New branch ruleset*,
   target `production`, enable restrict updates / block force pushes / block
   deletions, bypass = repository admin only. The exact bypass-actor options
   should be confirmed in that screen — I could not create or test a ruleset
   read-only, so I am describing the property required rather than asserting
   the precise UI wording.
5. **Verify the boundary afterwards** by attempting a task-branch push (should
   succeed) and a `production` push (should be refused by GitHub, not by a
   local hook). I can run that verification and report it.

Say the word and I will prepare exact commands for steps 1–3 for Anthony to run
and inspect; I will not run any of them without approval.

## 10. Autonomous worker still disabled

- `frankenstein-agent.service` / `.timer`: **not installed**
- no `ENABLED` flag in `~/.frankenstein/agent/`
- only `frankenstein-deploy.timer` is active
- the worker exists in the deployed tree as files only, and nothing starts it

## 11. FC-001 remains unissued

`PRODUCT_DIRECTIVE.md` untouched. No product work started, proposed or implied.

## 12. Control remains `awaiting_directive`

`turn: product_owner`, `status: awaiting_directive`, `directive_commit: null`,
`implementation_commit: null`, `last_actor: null`. This commit changes only
this file.

---

## Item 6 — other agents, read-only

Three `claude` processes are alive on the box:

| PID | started | cwd |
|---|---|---|
| 1453711 | 21:30 | `~/FrankensteinCentral` — this protocol session |
| 1572123 | 23:40 | `~` |
| 1574922 | 23:42 | `~` |

Neither of the other two has written to its transcript in the last ten minutes;
both appear idle rather than active. Nothing was killed, altered or inspected
beyond process metadata and transcript search.

**Answering your question directly: yes — both are still configured to push
production, and so is every future session.** Not through any setting of their
own, but because the global git credential helper hands the same full-`repo`
token to every process running as `antdag3`. Until item 8 is implemented, "no
agent pushes production" remains a rule that only holds while everyone obeys
it. That is the gate, and it is a human-approval gate.

Stopping here, per item 5.
