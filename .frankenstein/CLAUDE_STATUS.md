# Product Owner Write Path — Measured Failure and Revised Design

Written: 2026-09-06
Author: Claude (protocol agent, OptiPlex)
Re: ChatGPT's `403 Resource not accessible by integration` on `control`

**Status report. Authorizes nothing.** Revised design is in
`.frankenstein/RELEASE_AUTOMATION.md` §6. Nothing was executed.

---

## The measurement stands, and it is the useful kind of failure

ChatGPT's integration is **read-only for repository contents**. The probe was
worth running precisely because it failed: the previous design named ChatGPT as
the `control` write actor on the strength of a stated capability, and the
capability is not there. Had this been discovered after credentials were
created and Anthony's token removed from the box, the loop would have been
half-built and stuck.

`control` is unchanged at `d0d97d1`. `PROBE.md` does not exist. **No part of
this design now assumes ChatGPT can write anything** unless a later measurement
says otherwise.

## What this does and does not invalidate

It invalidates the **transport**, not the logic. The release condition (§4) only
requires that *someone the ruleset trusts* wrote acceptance to `control`. The
deterministic release service, the nine-point fail-closed check, the
directive → implementation → acceptance binding, the append-only rollback, the
Unix identity split and the worker's `handoff` change are all unaffected.

What changed is who can be that trusted writer.

## Next measurement — three probes, ten minutes

**A GitHub App's `Contents` permission is separate from `Issues`, and separate
again from gists.** A contents 403 tells us nothing about the others. Ask
ChatGPT to attempt each and report the exact error:

1. **open an issue** on the repository
2. **comment on an existing issue** — #10 will do
3. **create a gist**

This matters more than it sounds: **issues are enabled here and already in
use** — ten issues exist, all authored by `anthonydagostino`. If issue writes
are permitted, a Product Owner write channel already exists and needs no new
credential at all.

## The three paths, in preference order

**Path A — restore write access to ChatGPT. Recommended.** One browser action by
Anthony: GitHub → Settings → Applications → Installed GitHub Apps → the
ChatGPT/OpenAI app → Configure → grant **Contents: Read and write** for this
repository (or re-authorize the connector from ChatGPT's side with write scope).
If it works, **the design at `d0d97d1` stands unchanged** — no courier, no extra
machine account, no extra Unix user. This is exactly the class of rare one-time
account action the Product Owner already allowed as an exception.

**Path B — issues as the channel, plus a deterministic courier.** If contents
stay read-only but issues are writable: the Product Owner writes a strict fenced
block in a GitHub issue; `po-courier.sh` runs as Unix user `fcpo`, with no
Claude and no model in it, and transcribes it into `control` in the two-commit
order. It requires `issue.user.login` to equal the Product Owner's account
exactly — the repo is public, so every other author is ignored silently — and
fails closed on anything malformed. It **transcribes only**: it cannot originate
a directive and cannot accept an implementation. Acceptance authority stays with
the Product Owner's GitHub account. Cost: one machine account, one Unix user,
~150 lines plus tests.

**Path C — the Product Owner function moves onto the box. Not recommended.** A
separate Claude lane with its own credential. Credential separation would hold;
judgement independence would not — Claude would be accepting Claude. This is a
product decision about who the Product Owner *is*, not a technical one, so I am
flagging it rather than proposing it.

**Rejected: Anthony relays acceptance.** Trivial, and it is what happens today.
It contradicts the stated goal.

## How the four requirements survive each path

| requirement | Path A | Path B |
|---|---|---|
| Anthony does not relay routine messages | ✓ | ✓ |
| implementation Claude cannot impersonate acceptance | ✓ — no `control` write, ever | ✓ — same, and the courier's credential is under a Unix user Claude lanes cannot read |
| release automation stays deterministic | ✓ unchanged | ✓ unchanged |
| zero routine DevOps for Anthony after setup | ✓ | ✓ |

## Honest status until one lands

Product Owner decisions keep reaching the repository the way this document
did — through Anthony pasting them. That is the status quo Path A or B removes,
and it is why the §6a probes are the highest-value next action.

---

## Confirmations

| item | state |
|---|---|
| anything executed | **nothing** — no credential, user, ruleset, service, key, logout, or worker activation. `gh` still logged in as `anthonydagostino`; no SSH private keys; `rulesets` = `[]`; deploy keys = `[]`; no `fcagent`/`fcprotocol`/`fcrelease`/`fcpo` user exists |
| production | **healthy and unchanged** — `9b96bd0` desired and running, `last_result: success` |
| `STATE.json` | **unchanged** — `turn: product_owner`, `status: awaiting_directive` |
| FC-001 | **unissued** |
| autonomous worker | **disabled** — no unit installed, no `ENABLED` flag |

This commit changes only `CLAUDE_STATUS.md` and `RELEASE_AUTOMATION.md` §6, §8
and §13.

---

## What I would do next

1. **Run the §6a probes** — ChatGPT tries issue, issue comment, gist. Ten
   minutes, and it decides between Path A and Path B.
2. **In parallel, start the code that needs no credentials:** the worker's
   `handoff` change and `release-service.sh` with tests. Both are inert without
   a unit and a token, and neither depends on which path wins. That work is
   currently blocked only because you told me to stop — say the word and it
   proceeds while the write path is being settled.

Stopping here for your review.
