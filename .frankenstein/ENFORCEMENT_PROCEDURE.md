# Production-Write Enforcement — Human-Run Procedure

Written: 2026-09-06
Author: Claude (protocol agent, OptiPlex)
Status: **PREPARATION ONLY.** Nothing in this file has been executed.

**No credential, account, SSH, deploy-key, ruleset or GitHub-auth change has
been made.** No key was generated, no remote was repointed, no login was
removed. `STATE.json` and `PRODUCT_DIRECTIVE.md` are untouched; control remains
`turn: product_owner` / `status: awaiting_directive`; FC-001 unissued; the
autonomous worker is not installed and has no `ENABLED` flag.

Every command below is for **Anthony to run himself**, one block at a time,
checking the expected output before continuing.

---

## Pre-flight facts (verified read-only, no changes made)

| check | result | why it matters |
|---|---|---|
| `github.com:22` reachable from the box | **yes** | SSH transport will work |
| `ssh.github.com:443` reachable | **yes** | fallback exists if 22 is ever blocked |
| `~/.ssh/config` | **does not exist** | Block 3 creates it; nothing to clobber |
| `github.com` in `~/.ssh/known_hosts` | **absent (0 entries)** | Block 2 pins it deliberately instead of trusting first contact |
| SSH private keys on the box | **none** | no existing identity can be picked up by accident |
| local git hooks (both clones) | **none**, `core.hooksPath` unset | a rejected production push can only come from GitHub |
| poller / `deploy.sh` git usage | **`git fetch` only, never push** | deployment needs no write credential |
| anonymous fetch of this public repo | **works** — tested with the credential helper disabled: `git -c credential.helper= ls-remote origin production` returned `9b96bd0…` | **removing the login does not break deployment** |

That last row is the safety guarantee for step 8: after `gh auth logout`, the
box can still fetch and deploy. It simply cannot write.

---

## Block 1 — generate the deploy key (Anthony runs)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/frankenstein_deploy \
  -C "optiplex-frankenstein-agents" -N ""
chmod 600 ~/.ssh/frankenstein_deploy
cat ~/.ssh/frankenstein_deploy.pub
```

**Expected:** `ssh-keygen` reports the key pair was created, then exactly one
line is printed, beginning `ssh-ed25519 AAAAC3...` and ending
`optiplex-frankenstein-agents`.

Only `.pub` is ever displayed. **Never** `cat ~/.ssh/frankenstein_deploy` (no
extension) — that is the private half, and it never leaves the box.

*On `-N ""` (no passphrase):* required, because unattended agents cannot type
one. Be clear-eyed about what this does and does not buy: the private key is a
file readable by anything running as this user, exactly as the OAuth token is
today. The improvement is not secrecy — it is that this credential **cannot
move production**, whereas today's token can.

---

## GitHub UI step A — register the deploy key

Repository → **Settings** → **Deploy keys** → **Add deploy key**

- **Title:** `optiplex-agents (write, non-production)`
- **Key:** paste the single `ssh-ed25519 …` line from Block 1
- **Allow write access:** ☑ **enabled** (agents must be able to push branches)
- **Add key**

**Expected:** the key appears in the Deploy keys list, marked as having write
access, with "never used" until the first push.

---

## Block 2 — pin GitHub's host keys (do not trust first contact)

```bash
curl -fsS https://api.github.com/meta \
| python3 -c 'import json,sys; [print("github.com", k) for k in json.load(sys.stdin)["ssh_keys"]]' \
>> ~/.ssh/known_hosts
grep -c '^github.com ' ~/.ssh/known_hosts
ssh-keygen -lf ~/.ssh/known_hosts | grep -i ed25519
```

**Expected:** `3`, then a line whose fingerprint is
`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`.

That value came from `api.github.com/meta` over TLS on this box a moment ago
and matches GitHub's published ED25519 fingerprint. If it differs, **stop** —
do not continue over that connection.

---

## Block 3 — dedicated SSH identity, no fallback

```bash
cat >> ~/.ssh/config <<'EOF'

# FrankensteinCentral agent identity. This alias is used ONLY by agent clones.
# IdentitiesOnly stops ssh from offering any other key if this one is refused.
Host github-frankenstein
    HostName github.com
    User git
    IdentityFile ~/.ssh/frankenstein_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github-frankenstein
```

**Expected:**

```
Hi anthonydagostino/FrankensteinCentral! You've successfully authenticated,
but GitHub does not provide shell access.
```

and an exit status of 1 — that is normal for GitHub and is **not** an error.

**Read the greeting carefully.** It must name the **repository**
(`anthonydagostino/FrankensteinCentral`). That proves the connection
authenticated as a *deploy key*. If it greets you by **username**
(`Hi anthonydagostino!`), a personal SSH key was used instead — stop and fix
the config, because the whole boundary depends on this not being your account.

---

## Block 4 — repoint the agent clone only

```bash
git -C ~/frankenstein-protocol remote set-url origin \
  git@github-frankenstein:anthonydagostino/FrankensteinCentral.git
git -C ~/frankenstein-protocol remote -v
git -C ~/FrankensteinCentral remote -v
```

**Expected:** `~/frankenstein-protocol` shows the `git@github-frankenstein:…`
URL; `~/FrankensteinCentral` still shows the **https://** URL.

**Leave the deployment checkout on HTTPS deliberately.** It only fetches, the
repo is public, and anonymous fetch was verified to work. Giving the deployment
checkout an SSH write key would hand a write credential to the one checkout
that has no use for it.

Any future agent clone (money/product, CM) uses the same alias and the same
rule: agent clones on SSH, `~/FrankensteinCentral` on HTTPS.

---

## Block 5 — prove SSH write works BEFORE anything is removed

```bash
cd ~/frankenstein-protocol
git fetch origin
git push origin origin/control:refs/heads/tmp-deploykey-check
git ls-remote origin production
git push origin --delete tmp-deploykey-check
```

**Expected:**
- `fetch` succeeds with no password prompt
- `* [new branch] origin/control -> tmp-deploykey-check`
- `git ls-remote origin production` prints `9b96bd0c…` — **unchanged**
- the temporary branch is deleted

This proves three things at once: the deploy key can write, `control` is
readable and writable through it, and production has not moved. Note it pushes
`origin/control` to a **temporary** name — `control` itself is never modified.

**If any of this fails, stop here and use the recovery block.** Nothing has
been removed yet, so the old HTTPS path is still fully intact.

---

## GitHub UI step B — create the production ruleset

Repository → **Settings** → **Rules** → **Rulesets** → **New ruleset** →
**New branch ruleset**

- **Name:** `production is human-only`
- **Enforcement status:** **Active** (not "Evaluate" — evaluate mode only logs)
- **Target branches:** Add target → include by name/pattern → `production`
- **Bypass list:** add **Repository admin** and nothing else

**Rules to enable — by effect, since labels vary:**

| required property | look for the rule that |
|---|---|
| updates restricted | prevents the branch from being **updated** by anyone outside the bypass list (commonly "Restrict updates") |
| force pushes blocked | prevents non-fast-forward / force pushes ("Block force pushes") |
| deletions blocked | prevents branch deletion ("Restrict deletions") |

**Deploy keys must have no bypass.** GitHub does not offer deploy keys as
bypass actors — if this repository's UI somehow does, **do not add one**. The
bypass list must contain the admin/human path and nothing else.

I have deliberately not asserted exact label wording: I could not create or
inspect a ruleset read-only, and the UI differs between account types. Match
the *effect* column, then let Block 7 prove it behaviorally rather than
trusting the screen.

**Expected:** the ruleset appears in the list, targeting `production`, status
**Active**.

---

## Block 6 — remove Anthony's unrestricted credential from the box

Only after Block 5 passed and the ruleset is Active.

```bash
gh auth status
gh auth logout --hostname github.com
gh auth status
git config --global --unset-all credential.https://github.com.helper
git -C ~/FrankensteinCentral fetch --prune origin production && echo FETCH_STILL_WORKS
bash ~/FrankensteinCentral/scripts/frankenstein-status.sh
```

**Expected:**
- the first `gh auth status` shows logged in as `anthonydagostino`
- after logout, `gh auth status` reports **not logged in** to github.com
- `FETCH_STILL_WORKS` prints — deployment is unaffected, as pre-flight verified
- `frankenstein-status.sh` still shows desired `9b96bd0` == running `9b96bd0`,
  last result **success**

*Note on the helper:* `git config --global --get-regexp credential` currently
shows **two** entries for that key, which is why `--unset-all` is used rather
than `--unset`. Removing it is optional hygiene — with no login, the helper
returns nothing anyway — but it removes the ambiguity.

**This changes nothing on Anthony's own workstation or browser.** Only the
OptiPlex copy of the credential is removed.

---

## Block 7 — behavioral verification of server-side enforcement

This is the step that actually proves the boundary. Run it **after** logout, so
it genuinely runs as the deploy key.

**A. non-production push must SUCCEED**

```bash
cd ~/frankenstein-protocol
git fetch origin
git push origin origin/production:refs/heads/tmp-enforcement-check
```
Expected: `* [new branch] … -> tmp-enforcement-check`

**B. control must remain accessible and writable**

```bash
git fetch origin control
git push origin origin/control:refs/heads/tmp-control-check
```
Expected: `* [new branch] … -> tmp-control-check`

**C. production push must be REJECTED BY GITHUB**

```bash
git checkout -q --detach origin/production
git commit -q --allow-empty -m "enforcement probe: this push must be rejected"
git push origin HEAD:refs/heads/production
```

**Expected: the push FAILS**, with `remote:` lines from GitHub — something like
`GH013: Repository rule violations found` or `protected branch hook declined` —
and a non-zero exit status.

The `remote:` prefix is the proof: it means GitHub refused the update. There
are **no local git hooks on this box and `core.hooksPath` is unset** (verified),
`promote.sh` is not involved in this command, and nothing here consults a branch
name. A rejection here cannot have come from our own tooling.

**If the push SUCCEEDS: STOP and report immediately.** The ruleset is not
effective. The damage is contained by design — the probe commit is **empty**,
so production's tree is byte-identical to `9b96bd0`; the poller will redeploy
the same tree and nothing about the running system changes. Do not try to fix
it by force-pushing; production is append-only.

**D. cleanup**

```bash
git push origin --delete tmp-enforcement-check tmp-control-check
git checkout -q claude/activation-reconcile
git ls-remote origin production
```
Expected: both temporary branches deleted, and production still `9b96bd0…`.

---

## Recovery — if SSH authentication fails before logout

Nothing is lost. The OAuth credential is still on the box at that point, so the
HTTPS path is untouched and fully working.

```bash
# put the agent clone back on HTTPS
git -C ~/frankenstein-protocol remote set-url origin \
  https://github.com/anthonydagostino/FrankensteinCentral.git
git -C ~/frankenstein-protocol fetch origin && echo HTTPS_PATH_OK
```

Then, if abandoning the attempt entirely:

```bash
# remove the alias block that was appended in Block 3, then:
rm -f ~/.ssh/frankenstein_deploy ~/.ssh/frankenstein_deploy.pub
```
and delete the key in GitHub → Settings → Deploy keys.

**Order matters:** never delete the deploy key on GitHub while a clone is still
pointed at the SSH remote — repoint to HTTPS first.

**If SSH fails *after* logout:** deployment is still unaffected (the poller
fetches anonymously — verified). To restore write access, either fix the SSH
config, or run `gh auth login --hostname github.com` on the box and re-authorize
through the device flow. Production is never at risk in this state, because
nothing on the box can write to it.

---

## Final verification — what to run at the end

```bash
git -C ~/frankenstein-protocol remote -v
git -C ~/FrankensteinCentral remote -v
ssh -T git@github-frankenstein
gh auth status
bash ~/FrankensteinCentral/scripts/frankenstein-status.sh
git ls-remote origin production
docker compose -f ~/FrankensteinCentral/docker-compose.yml ps --format '{{.Service}} {{.State}}'
```

---

## What to paste back (secrets excluded)

Safe to share:

1. `git remote -v` for both clones
2. the `ssh -T git@github-frankenstein` greeting line (it names the repo)
3. Block 5 and Block 7 push results, success and failure alike
4. **the full `remote:` rejection text from Block 7C** — this is the evidence
5. `gh auth status` after logout ("not logged in")
6. `frankenstein-status.sh` output
7. `git ls-remote origin production` (the SHA)
8. the deploy key's fingerprint if wanted: `ssh-keygen -lf ~/.ssh/frankenstein_deploy.pub`

**Never paste:** the private key `~/.ssh/frankenstein_deploy`, the contents of
`~/.config/gh/hosts.yml`, any token, or any API key. The `.pub` file and
fingerprints are public by nature and safe.

---

## What changes operationally afterwards

| | before | after |
|---|---|---|
| agents push task branches | yes | **yes**, via the deploy key |
| protocol agent uses `control` | yes | **yes**, via the deploy key |
| agents move `production` | **yes — nothing stopped them** | **no — GitHub refuses** |
| `promote.sh` from the box | works | **intentionally stops working** |
| `rollback.sh` from the box | works | **intentionally stops working** |
| deployment / the poller | works | **unchanged** — fetch only, and anonymous fetch is verified |
| Anthony's workstation & browser | — | **untouched** |

Promotion becomes a human act performed from a credential the agents cannot
reach: Anthony pushes `production` from his own machine with his own account
(admin bypass), or merges through the GitHub UI.

The cost, stated plainly: **emergency rollback also becomes a human action.**
`rollback.sh` will still compute and commit the correct roll-forward tree on
the box, but the final push will be refused, and Anthony must complete it. That
is the same trade as promotion, and it is the price of the boundary being real
rather than agreed.

One thing that is not solved by any of this: the deploy key's private half sits
on the box readable by anything running as that user, exactly as the token does
today. The boundary this creates is **not** "agents cannot read a credential" —
it is "the credential agents can read cannot move production". That is the
property worth having, and it is the one the ruleset enforces.

---

Prepared for Product Owner review. Nothing will be executed by me.
