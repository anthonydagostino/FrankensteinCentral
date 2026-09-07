# Implementation Handoff — FC-008 (seven-day weekly calendar)

This is the canonical FC-008 handoff, reporting the **final** candidate once.
Earlier FC-008 reports (`176e72b`, `add5b2b`, `ce713da`, `a137d6d`, `5f6b9bf`)
are superseded and remain in this branch's git history; the `.frankenstein/FC-008/`
notes beside this file predate this candidate and are historical. FC-002's
handoff is also preserved in history at its own commits; **FC-002 remains
paused and unaccepted** and nothing here changes it.

| | |
|---|---|
| Task | FC-008 |
| **Implementation commit** | **`0872e7ee2086895afcfebf2c83f25d5236f40304`** |
| Task branch | `claude/FC-008-weekly-calendar` |
| Authorization epoch | `6033de85290a25fda38d5f98b613f6c2be80c991` |
| directive_commit | `7599cb195ee204ca2821a39cd7d6c3a9946fc6ce` |
| Candidate Codex reviewed in correction 2 | `5f6b9bf0f35e0debc3b72a42730eb20d3c3e7ec5` |
| Integrated production | `e7adf839e0e2e225e50550f5564de7dd93d85512` |
| Deployment Authorization | `none` — pushed for review, nothing deployed |

## Correction 2, item by item

### 1. P1 — `schedule_state` returned `ok` on evidence that did not support it

Codex was right, and the defect was real rather than a nit. Gmail's
`mode == "live"` is not proof of a healthy connection: `services/gmail/app/main.py`
deliberately **keeps** `mode="live"` when an inbox fetch fails but cached items
exist, setting `sync_status="failed"` alongside it. And even a genuinely
healthy Gmail sync says nothing about Calendar — `gcal.py` borrows gmail's
token, but a shared credential is not proof that the Calendar API is
reachable, in scope, or syncing.

`ok` now requires **positive, Calendar-specific evidence**:

- `services/schedule/app/gcal.py` gains `probe()` — a read-only GET for a
  single event, distinguishing "no token" (`disconnected`) from "the call
  failed" (`unreachable`), which `list_upcoming()` collapses into one `None`.
- `services/schedule/app/main.py` exposes `GET /calendar-health` over it.
  Deliberately **not** `POST /sync-from-calendar`: that imports rows into
  Postgres, making it a mutation rather than a probe.
- The assistant fetches it inside the existing parallel gather, so it costs no
  extra round trip, and passes it as `calendar_evidence`.

Precedence: no schedule payload → `unreachable`; positive probe → `ok`; probe
reports no token, or gmail reports `disconnected` → `disconnected`; everything
else — including `live` with `sync_status` `failed` or `never` → `unknown`.
Gmail is **negative evidence only**. Local commitments are preserved in every
state, with the caveat rendered above the grid.

Tests use the **actual cached-failure shape** (`mode="live"` + `sync_status="failed"`
+ non-empty items) as the directive requires, and separate a healthy inbox from
confirmed Calendar health. Reinstating "live means healthy" fails four of them.
No new OAuth scopes, no writes, no new credentials.

### 2. P1 — the declared metadata contradiction

Two halves, and only the first had been done when this round started:

- `tests/test_protocol.py` was ported to assert **consistency** rather than the
  bootstrap state — same task in `STATE.json` and `PRODUCT_DIRECTIVE.md`,
  exactly one recognized Deployment Authorization, the helper's verdict
  matching committed state, and a recorded `implementation_commit` being a full
  SHA that **exists in this repository**. Strictly stronger, not weaker.
- The metadata itself was still unwritten. The branch was carrying the **FC-001
  bootstrap placeholders** in `STATE.json` and `PRODUCT_DIRECTIVE.md`, the empty
  sentinel handoff, and `AUTHORIZING_CONTROL_COMMIT` pointing at the previous
  epoch `f1a56bd`. That is closed at `0872e7e`: both files are taken verbatim
  from control at epoch `6033de8`, and the epoch file now names it.

`bash scripts/frankenstein-status.sh` resolves FC-008 with a coherent verdict.
No FC-002 release code was imported, no binding check was weakened, and no
missing historical object is relied on — all three referenced SHAs resolve in a
clean clone (verified explicitly, see below).

### 3. The canonical handoff

This file. It replaces a report that mixed current facts with obsolete claims
(placeholder files unchanged, old CI red, "review `176e72b`" as the next step,
FC-002 files canonical). Prior reports stay in git history.

## Test evidence

All three runs are on the exact bound SHA `0872e7e`, and all three agree.

| run | result |
|---|---|
| GitHub Actions, run `34144913490`, job `101814721816` | **exit 0 — 2339 passed, 0 skipped**; node 5/5, 0 skipped; ALL TESTS PASSED |
| `bash scripts/test.sh`, **fresh clone** checked out at `0872e7e` | **2339 passed, 0 skipped**, ALL TESTS PASSED |
| `bash scripts/test.sh`, bound worktree | **2339 passed, 0 skipped**, ALL TESTS PASSED |

The CI job's "Verify the sandbox is actually available" step passed and the run
carries `FRANKENSTEIN_REQUIRE_SANDBOX=1`, so `0 skipped` means the containment
suite was **actually exercised**, not skipped past. Nothing was skip-widened
and no unsandboxed execution was used.

The fresh-clone run used a full-branch fetch, matching CI's `fetch-depth: 0`.
Under it `7599cb1`, `5f6b9bf` and `6033de8` all resolve, so the binding does
not depend on objects reachable only in a working checkout — the failure mode
that made the earlier FC-002 candidate red in CI while green locally.

### Calendar sweep coverage

`services/assistant/tests/test_dashboard.py` sweeps **800 consecutive start
dates from 2026-01-01 through 2028-03-10**, each rendering its own seven-day
window, so the last day asserted is 2028-03-16. Every start date is checked for
seven consecutive days with no gaps or repeats. The span covers **both DST
transition pairs** in `America/New_York` and **two 29 February** occurrences
(2028-02-29 asserted by name and by ordinal). Ordinal suffixes are checked for
**all of days 1–31**, including the 11th/12th/13th exceptions. No assertion
runs against today's date.

### Schedule states exercised

All four, in both the backend and the rendered output: `ok` (14 assertions),
`disconnected` (12), `unknown` (9), `unreachable` (6). The `unknown` set
includes the actual gmail cached-failure shape — `mode="live"` with
`sync_status="failed"` and non-empty items — which is the specific case
correction 2 named.

## Deviations

1. **The `ok` state cannot be exercised against a real Google Calendar from
   this environment.** `probe()`'s success path is covered by tests, not by a
   live call. The states reachable without credentials — `unreachable`,
   `disconnected`, `unknown` — are what a disconnected box actually renders,
   and `ok` requires a real token to observe end to end.
2. **No end-to-end deployment is claimed.** Deployment Authorization is `none`;
   nothing was promoted or deployed and the running SHA is not confirmed here.

## What is NOT claimed and NOT touched

- Neither `control` nor `production` was written.
- `PO_REVIEW_e7adf83.md`'s money findings remain **open**; they are not
  authorized under this directive and no money-layer code was edited.
- Production ancestry `e7adf83` is preserved. No rollback was performed and the
  promotion that moved it is not authenticated or endorsed here.
- FC-002 remains paused and unaccepted; its handoff was not edited.
- Acceptance is Codex's alone, and promotion is the deterministic release
  service's alone. Neither is implied by this handoff.
