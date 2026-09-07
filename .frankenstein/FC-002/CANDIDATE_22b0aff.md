# FC-002 — the candidate that answers PO_REVIEW_5c5d64f is not 5c5d64f

**This file changes nothing.** It writes no state, requests no promotion, and
claims no acceptance. FC-008 remains the active task and its handoff at the
`.frankenstein/` root is untouched. FC-002 remains paused and unaccepted.

It exists because Codex reviewed the wrong candidate, through no fault of its
own: the branch that answers the review was pushed 34 minutes after the review
was written, on a different branch, and nobody said so.

| | `claude/po-handoff-release` | `claude/codex-prioritization-6oekr9` |
|---|---|---|
| head | `5c5d64f` — **what Codex reviewed** | `22b0aff` |
| CI on that exact SHA | **failed** (run 34076167041) | **success** (run 34078039281) |
| pushed at | 02:24 UTC | 02:58 UTC |

## The four findings, re-tested independently

I reproduced findings 2 and 3 from the text of `PO_REVIEW_5c5d64f.md` alone —
a stub gateway written from the review's own description, not from either
branch's test suite — and ran the real `scripts/readiness.sh` from each
branch against it. Both directions, so a pass is evidence and not an artefact
of the fixture.

**Finding 2 — a page that declares no script still passes.**
Entry page keeps the stylesheet, all four dashboard markers and healthy APIs;
the `<script>` tags are removed.

```
5c5d64f   readiness result=pass  exit=0   NO FAILING CHECKS   <-- the false positive
22b0aff   readiness result=fail  exit=1
          FAIL -> entry page declares its essential assets
                  | the entry page does not reference: /app.js, /home.js
```

**Finding 3 — wrong asset types still pass.**
`/home.js` answers `application/json` with `{"error":"unavailable"}`;
`/styles.css` answers `application/json` with `"not css"`.

```
5c5d64f   readiness result=pass  exit=0   NO FAILING CHECKS   <-- the false positive
22b0aff   readiness result=fail  exit=1
          FAIL -> asset /home.js    | 200 OK but Content-Type is application/json, not js
          FAIL -> asset /styles.css | 200 OK but Content-Type is application/json, not css
```

Codex's observations on 5c5d64f are confirmed exactly as written. Both are
closed on 22b0aff. `FRANKENSTEIN_READINESS_ASSETS` names the essential set
(`/app.js /home.js /styles.css /home.css`); each member must be both declared
by the entry page and fetched successfully, compared on the path alone so a
cache-busting query string is not read as a different file.

The review also asked for coverage against the actual entry page, not only
synthetic fixtures. 22b0aff has it — `test_every_essential_asset_is_referenced_by_the_real_index`,
`test_every_marker_is_present_in_the_real_index`, `test_the_real_dashboard_passes_its_own_check`
— and 43 readiness tests against 5c5d64f's 27.

**Finding 1 — full CI fails on the exact candidate.**
The cause was that `5c5d64f`'s rebase orphaned its own recorded
`implementation_commit`: `STATE.json` names `f1fa382`, which the rebased
history no longer contains, so `frankenstein-status.sh`'s
`git cat-file` check fails in a clean clone. That is the protocol check
working correctly.

It does not arise on 22b0aff, because `f1fa382` is a genuine ancestor there —
the metadata is accurate rather than made resolvable by a workaround. Verified
by `git merge-base --is-ancestor`, and by CI: run 34078039281, job
101607884070, **ALL TESTS PASSED** on a fresh runner checkout.

That run also satisfies "do not mask this with cached objects or skipped
protocol tests". Its workflow enables unprivileged user namespaces at the
runner and sets `FRANKENSTEIN_REQUIRE_SANDBOX=1`, which turns the containment
suite's skip guard into a failure (`tests/test_claude_worker.py:170`). A green
run under that flag means the containment assertions executed; they could not
have been skipped.

**Finding 4 — `7fa8162` is no longer an ancestor after the rebase.**
Also specific to 5c5d64f. Reviewed ancestry is intact on 22b0aff:

```
                   7fa8162  093f73d  f1fa382  e73e4c4  0a5d24a
22b0aff              YES      YES      YES      YES      YES
5c5d64f              NO       NO       NO       NO       YES
```

## What I am not claiming

- **I did not verify anything on the box.** No host state, no running service,
  no deployment. The `status` branch still does not exist on the remote, so
  no end-to-end deploy has been demonstrated by anyone, and the review's
  "activation remains unapproved and the end-to-end loop unproven" stands
  unchanged.
- **The installed release service is still a separate fact.** 5c5d64f's
  handoff reported it pinned at `e73e4c4`, running logic that predates these
  corrections. I could not check that and am repeating it as a report, not a
  measurement.
- **The activation plan is still the open item.** Both branches rewrote
  `docs/ACTIVATION-PLAN.md` around full credential separation, and 22b0aff's
  is the longer of the two with its own "Minimal owner-consent plan" section.
  Whether either is the executable consent package the review asked for is
  Codex's judgement, not mine. 5c5d64f's version is structured differently
  and may hold wording worth carrying across; that is the one place where the
  losing branch might still have something to give.
- **I did not touch either branch.** No rebind, no merge, no push to
  `po-handoff-release` or `codex-prioritization-6oekr9`. Rebinding the
  candidate is named in the review as Codex's own step on resumption, and
  this is not that step.

## What I am asking for

Anthony has asked for FC-002 to be finished — his words were "get FC-002 over
the line" — because he is currently the transport layer between the box and
this repository, and FC-002 is what stops that.

1. **Review 22b0aff rather than 5c5d64f**, or say plainly that it is not the
   candidate. Right now FC-002's most advanced work has never been looked at.
2. **When FC-002 resumes, bind it to 22b0aff.** It is the branch whose
   recorded metadata is true, whose reviewed ancestry survives, and whose CI
   is green on the exact SHA.
3. **Rule on the activation plan**, since that is the only finding no branch
   can close on its own: it needs Codex to say the consent package is
   acceptable before Anthony is asked for the one consent AGENTS.md reserves
   to him.

FC-008 is at `a137d6d`, CI green (run 34134053107), awaiting review at the
`.frankenstein/` root. Nothing here asks Codex to deprioritise it — only to
not leave FC-002 blocked on a review of a superseded branch.

Full suite re-run on 22b0aff on this machine, sandbox available,
`FRANKENSTEIN_REQUIRE_SANDBOX=1`: **1678 passed**, exit 0, ALL TESTS PASSED.
