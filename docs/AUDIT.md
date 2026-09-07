# FrankensteinCentral — Audit & Redesign Plan

_Internal working doc. Audit of the system as-is, why it isn't sticky, the new
information architecture, and the phased build plan._

## 1. What exists today

**Topology:** a `gateway` (FastAPI) reverse-proxies `/api/<app>/<path>` to 13
independent FastAPI microservices, all behind one Postgres (`db`). The frontend
is a static SPA (`gateway/static`) whose homepage is a canvas "lounge" where
skeleton agents walk to stations; clicking a station opens that app's modal.

**Services and what they actually hold:**

| Service | Real data? | Value |
|---|---|---|
| gmail | ✅ live triage (needs-reply, deals, interview/deadline classification, follow-up threads) | **High** — already the strongest signal, buried in a modal |
| firefly | ✅ net worth, month spend/income, categories, accounts (your real Firefly:8093) | **High** |
| networth | ✅ sourced from firefly | High (dup of firefly) |
| finance | ✅ bills/subscriptions (user-entered) | Medium |
| budget | ✅ category budgets (user-entered) | Medium (overlaps firefly categories) |
| schedule | ✅ events + Google Calendar sync | High |
| tasks | ✅ to-dos | High |
| fitness | ✅ gym visits + plan | Medium (gym signal) |
| deals | ✅ discounts parsed from gmail | Low |
| powerbuy | ✅ arbitrage tracker (external API) | Low/niche |
| vault | ✅ password-health metrics (no secrets) | Low on homepage; keep as secure shortcut |
| jellyfin | ✅ continue-watching etc. | Low unless actively watching |
| assistant | ✅ orchestrator: `/briefing` (attention seed), `/overview` (number row), `/ask`, `notify` (telegram/whatsapp), activity/memory/deadlines tables | **Foundation** — underused by the UI |

**Security posture (current):** each service holds its own secrets server-side
(firefly token, gmail OAuth, vault); nothing sensitive is sent to the browser;
vault returns metadata only. LAN-only via docker. **Gaps:** the dashboard itself
has no auth (anyone on the LAN can open it — acceptable for home, noted); the
gateway will proxy any `/api/<app>/<path>` (fine internally). No secrets are
logged. Fake/mock data was just removed so numbers are trustworthy.

## 2. Why it isn't sticky (the real problem)

1. **The homepage is decoration, not signal.** A canvas of walking skeletons is
   charming once; it gives no reason to return. The actual signal (overview row,
   briefing) is cramped in the margins.
2. **It only displays — you can't _do_.** No study timer, no water log, no "log
   workout", no reply/snooze, no quick-capture. Information without action.
3. **No time-of-day context.** Same screen at 7am and 11pm. No morning plan, no
   evening wrap-up.
4. **No personal-goal model.** Study, gym, hydration, nutrition — the habits you
   actually want to build — aren't tracked at all.
5. **No next-best-action and no daily score.** Nothing tells you what to do next
   or holds you accountable.
6. **Signal is scattered across 13 modals.** No unified "needs attention" feed.
7. **Slow to use.** No command palette, no keyboard nav, no capture box.

## 3. New information architecture (the Personal Context model)

```
Personal Context
├── Today       calendar · tasks · Big 3 · deadlines · time-of-day mode
├── Health      study/focus · gym · hydration · nutrition   (+ daily score)
├── Finance     accounts · spending · bills · investments   (firefly + stocks)
├── Comms       email triage · follow-ups
├── Home        services · infra health
└── Memory      captures · notes · metric history
```

Two new backend pieces make this real:

- **`core` service** — owns personal state: settings/goals, per-day metrics
  (study, water, gym, nutrition, sleep), focus sessions, Big 3, quick-capture
  inbox, metric history, and the transparent **daily-score** engine. Pure state
  + rules; no LLM required.
- **`assistant` becomes the brain** — a single fast `/home` endpoint fans out
  (concurrent, timed-out, cached) to every service + `core` and assembles the
  homepage: greeting/mode, briefing line, unified **Needs Attention** feed
  (severity Important/FYI), **Do This Next** (explainable rules), Money,
  Portfolio, Health, Score, Big 3, captures. One call → the whole home screen.

Plus a **`stocks`** service (keyless quotes via Stooq) for the portfolio hook,
and a rebuilt **Today command center** frontend with **Cmd/Ctrl-K** command
palette. The canvas lounge is demoted to an "Apps" view, not the homepage.

## 4. Phased plan

**Phase 1 — Daily use (this iteration):** core service (goals, water, study
timer, gym, nutrition, Big 3, capture, daily score, history) · stocks service ·
assistant `/home` aggregator (briefing, Needs Attention, Do This Next, Money,
Portfolio, Health) · new Today homepage with morning/evening modes · command
palette · quick capture · settings.

**Phase 2 — Intelligence:** smarter Do-Next, local financial observations, email
follow-up tracking, weekly review, habit trends.

**Phase 3 — JARVIS layer:** natural-language query over the context model,
cross-service actions, proactive notifications.

Deterministic data/rules are the core; AI only enhances later.
