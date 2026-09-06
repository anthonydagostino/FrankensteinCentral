# How Codex finds out that something happened

The loop has one remaining human-shaped hole: **nothing tells the Product
Owner that a handoff is waiting.** Claude's side is solved — the worker polls
`control` on a timer. Codex has no equivalent unless it is configured to look.

If that hole is not closed, Anthony ends up prompting Codex to go and check,
which is the message-relay role this whole protocol exists to remove.

## What Claude has prepared

Everything Codex needs is readable from **two refs and three files**, with no
shell access to the box and no credentials:

| ref | file | answers |
|---|---|---|
| `handoff` | `.frankenstein/CLAUDE_STATUS.md` | what was built, deviations, evidence |
| `handoff` | `.frankenstein/AUTHORIZING_CONTROL_COMMIT` | which directive it answers |
| `handoff` | `.frankenstein/TASK_BRANCH` | where the code is |
| `status`  | `.frankenstein/RELEASE_STATUS.json` | what was promoted, what is running, what failed |

`RELEASE_STATUS.json` has a stable `schema` field and separates **promotion**
from **deployment**: `promotion.production_commit` is what the release service
pushed; `deployment.running_commit` is what the box actually has running;
`deployment.promoted_but_not_running` is true when those disagree. It contains
SHAs, protocol state, timestamps and result codes only — never credentials,
command output, file contents, or personal data.

**A single poll of those two refs is sufficient.** No webhook, no API token,
and no write access is required to read them; the repository is public.

## What Codex must configure — Codex's own action

Claude cannot do this part, and must not simulate it. Codex must arrange to be
woken on a recurring basis and, on each wake, perform this check:

```
1. Read .frankenstein/STATE.json on `control`.
2. Read .frankenstein/AUTHORIZING_CONTROL_COMMIT on `handoff`.
3. If the handoff answers the CURRENT control authorization epoch
   (the last commit on control that changed STATE.json)
   and control still says turn=claude:
       -> a handoff is waiting for review. Review it.
4. Read .frankenstein/RELEASE_STATUS.json on `status`.
   If deployment.promoted_but_not_running is true, or
   verification.result is "fail", or failures[] is non-empty:
       -> a release needs attention.
5. Otherwise: nothing to do. Do not message Anthony.
```

Step 5 matters as much as the others. A wakeup that reports "nothing happened"
to Anthony re-creates the relay in the other direction.

**Cadence:** anything from 15 minutes to a few hours is fine. The worker's own
poll is every 2 minutes, and a task branch takes far longer than that to
produce, so there is no benefit to polling aggressively.

**Which mechanism:** Claude cannot see what scheduling Codex actually has, and
will not guess. Codex must report which of these it can do, and configure it:

- a recurring/scheduled Codex task holding the check above — preferred, since
  it needs nothing from GitHub beyond public read access;
- a GitHub-event trigger on pushes to `handoff` and `status`, if Codex's
  GitHub integration exposes one;
- failing both, say so plainly. Do not substitute Anthony.

## What must NOT be done instead

- **Claude must not review its own handoff.** An implementation agent that can
  accept its own work makes acceptance meaningless. There is no "temporary"
  version of this.
- **Claude must not write a stand-in reviewer**, scheduled or otherwise. A
  second Claude lane reviewing the first is still Claude accepting Claude.
- **Anthony must not be the notifier.** "Anthony tells Codex to look" is the
  relay, wearing a different hat.

If Codex genuinely cannot be woken by any mechanism, that is a real blocker and
belongs in the handoff as one — not something to paper over.
