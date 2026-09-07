# Implementation Handoff — FC-008 (seven-day weekly calendar)

This file is the in-branch record. The canonical, reviewable handoff — with
the exact final SHA — is published on the `handoff` branch, because a commit
cannot contain its own SHA and fabricating a self-referential one would be a
lie in the bookkeeping.

| | |
|---|---|
| Task | FC-008 |
| Authorization epoch | `6033de85290a25fda38d5f98b613f6c2be80c991` |
| directive_commit | `7599cb195ee204ca2821a39cd7d6c3a9946fc6ce` |
| Task branch | `claude/FC-008-weekly-calendar` |
| Candidate Codex reviewed | `5f6b9bf0f35e0debc3b72a42730eb20d3c3e7ec5` |
| Integrated production | `e7adf839e0e2e225e50550f5564de7dd93d85512` |
| Deployment Authorization | `none` — pushed for review, nothing deployed |

## Correction 2 — what this round answers

**1. P1: `schedule_state` returned `ok` on evidence that did not support it.**
Gmail keeps `mode="live"` while serving cached mail after a failed fetch
(`services/gmail/app/main.py`, `sync_status="failed"` alongside), and a shared
credential proves nothing about Calendar API access. `ok` now requires
positive, Calendar-specific evidence: `services/schedule/app/gcal.py` gains a
read-only `probe()`, exposed as `GET /calendar-health`, which the assistant
fetches in its existing parallel gather. Precedence — no schedule payload →
`unreachable`; positive probe → `ok`; no token, or gmail `disconnected` →
`disconnected`; everything else, including `live` with `sync_status` failed or
never → `unknown`, keeping local commitments and the caveat. Gmail is negative
evidence only. Tests use the actual cached-failure shape and separate a
healthy inbox from confirmed Calendar health. No new scopes, no writes.

**2. The metadata contradiction is resolved.** `tests/test_protocol.py` was
ported to assert consistency rather than the bootstrap state, and this branch
now carries its real authorization: `STATE.json`, `PRODUCT_DIRECTIVE.md` and
`AUTHORIZING_CONTROL_COMMIT` are bound to epoch `6033de8` with FC-008
identity, replacing the FC-001 placeholders that were still in place. No
FC-002 release code was imported and no binding check was weakened.

**3. The canonical handoff is rewritten** around the final candidate on the
`handoff` branch. Prior reports remain in git history.

## What is NOT claimed

- Nothing was promoted or deployed; Deployment Authorization is `none`.
- Neither `control` nor `production` was written.
- `PO_REVIEW_e7adf83.md`'s money findings remain open and untouched — they are
  not authorized under this directive.
- FC-002 remains paused and unaccepted. Its historical handoff is preserved in
  git history and was not edited.
