# Data Model

Every table, which service owns it, and what it is for. Column types were read
from the **live Postgres instance** on the box, so this reflects the schema as
actually migrated — including columns added by the additive `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS` statements that run at service startup.

## Storage inventory

| Store | Type | Contents |
|---|---|---|
| `frankensteincentral_db_data` | Docker volume | The Postgres 16 data directory. All tables below. |
| `frankensteincentral_gmail_token` | Docker volume | Mounted at `/data` in the gmail service: `token.json` (Google refresh token) and `gmail_state.json` (last-known-good inbox). |

Neither is touched by a deploy — `git reset --hard` only affects tracked files
in the repo. `docker compose down -v` destroys both, including the Gmail
consent.

## How the schema is managed

There is **no migration tool**. Each service runs its own `CREATE TABLE IF NOT
EXISTS` (and, where a column was added later, `ALTER TABLE ... ADD COLUMN IF
NOT EXISTS`) on startup. This is why the deploy sequence is safe to re-run: the
statements are idempotent by construction.

The consequence to understand: **schema changes are forward-only and
additive.** Nothing drops or renames a column, and nothing backfills. A column
added today is `NULL` (or its default) for every existing row.

## Ownership

One database, eight services with tables, and **no table shared between two
services**. Services read each other's data over HTTP, never over SQL. The
database is shared infrastructure, not a shared model — that is what keeps the
"each sub-app is independent" property true rather than aspirational.

| Service | Tables |
|---|---|
| `assistant` | `activity`, `deadlines`, `memory`, `thread_state` |
| `core` | `big3`, `captures`, `core_settings`, `daily_log`, `focus_sessions` |
| `schedule` | `events` |
| `networth` | `accounts`, `recurring` |
| `deals` | `deals` |
| `finance` | `bills` |
| `fitness` | `visits` |
| `tasks` | `tasks` |
| — (legacy) | `budget` |

Stateless services — `budget`, `stocks`, `vault`, `plex`, `firefly`,
`powerbuy`, `gateway` — hold no tables at all. They derive everything from an
upstream on each request.

---

## assistant

### `thread_state` — what makes sync idempotent

The most important table in the system. Without it, every assistant sync would
re-create the same calendar events and re-send the same notifications.

| Column | Type | Notes |
|---|---|---|
| `thread_id` | `text NOT NULL` | Gmail thread id. |
| `signature` | `text NOT NULL` | Fingerprint of the thread's last-seen state. Unchanged signature ⇒ the whole thread is a no-op. |
| `status` | `text NOT NULL` | Last-known outcome for the thread. |
| `updated_at` | `text NOT NULL` | ISO timestamp. |

### `activity` — the agent action log

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `agent` | `text NOT NULL` | Which agent acted. |
| `station` | `text NOT NULL` | Which sub-app it acted on. |
| `action` | `text NOT NULL` | |
| `detail` | `text NOT NULL` | |
| `created_at` | `text NOT NULL` | ISO timestamp. |

> **Unbounded growth.** Reads are capped (`ORDER BY id DESC LIMIT 30`) but
> writes are never pruned. As of 2026-09-05 this table holds ~33,500 rows over
> 46 days — roughly 730 rows/day, 4.4 MB, and by far the largest object in a
> 13 MB database. It is not a problem yet and nothing depends on the old rows;
> it will need a retention policy eventually. See
> [DEPLOYMENT-BASELINE.md](DEPLOYMENT-BASELINE.md#observations).

### `deadlines`

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `title` | `text NOT NULL` | |
| `due_at` | `text` | Nullable — a deadline with no known date is a real state. |
| `source` | `text NOT NULL` | Which sub-app it came from. |
| `external_id` | `text` | Upstream id, for dedupe across syncs. |
| `created_at` | `text NOT NULL` | |

### `memory`

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `content` | `text NOT NULL` | |
| `created_at` | `text NOT NULL` | |

---

## core

### `core_settings` — the shared configuration blob

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | Single row. |
| `data` | `jsonb NOT NULL` | Everything. |

This one row is read by **other services**: `stocks` reads
`market.holdings` / `market.watchlist` from it, and `budget` reads the budget
definitions from it. Editing settings through `PUT /api/core/settings` is
therefore how you configure those services — not through environment variables.

### `daily_log`

| Column | Type | Notes |
|---|---|---|
| `day` | `date NOT NULL` | Primary key; the day in `LOCAL_TZ`, not UTC. |
| `water_oz` | `integer NOT NULL` | |
| `nutrition` | `text` | |
| `sleep_hours` | `numeric` | |
| `updated_at` | `timestamptz NOT NULL` | |

### `focus_sessions`

| Column | Type |
|---|---|
| `id` | `integer NOT NULL` |
| `day` | `date NOT NULL` |
| `label` | `text NOT NULL` |
| `minutes` | `integer NOT NULL` |
| `created_at` | `timestamptz NOT NULL` |

### `big3` — the day's three priorities

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `day` | `date NOT NULL` | |
| `position` | `integer NOT NULL` | 1–3. |
| `text` | `text NOT NULL` | |
| `done` | `boolean NOT NULL` | |
| `created_at` | `timestamptz NOT NULL` | |

### `captures` — the quick-capture inbox

| Column | Type |
|---|---|
| `id` | `integer NOT NULL` |
| `text` | `text NOT NULL` |
| `kind` | `text NOT NULL` |
| `done` | `boolean NOT NULL` |
| `created_at` | `timestamptz NOT NULL` |

---

## schedule

### `events` — the calendar, and the audit trail

| Column | Type | Notes |
|---|---|---|
| `id` | `text NOT NULL` | UUID, primary key. |
| `title` | `text NOT NULL` | |
| `starts_at` | `text NOT NULL` | ISO timestamp. |
| `ends_at` | `text` | |
| `source` | `text NOT NULL` | Default `manual`. |
| `external_id` | `text` | **UNIQUE.** The dedupe key — a Gmail message id, or `manual:<uuid>` generated for hand-added events. |
| `created_at` | `text NOT NULL` | |
| `status` | `text NOT NULL` | `pending` \| `countered` \| `confirmed` \| `declined`. Added by additive migration; default `confirmed`. |
| `thread_id` | `text` | Gmail thread, so resolving a thread can find sibling slots. Added by additive migration. |
| `gcal_event_id` | `text` | The id in the real Google Calendar. Added by additive migration. |

Three things this schema encodes:

**`external_id` being UNIQUE is what makes re-syncing safe.** `POST /events`
upserts on it, so a pending slot flips to confirmed by changing `status`
underneath the same row rather than becoming a second calendar entry. A
manually-added event with no `external_id` gets one generated — otherwise it
would never be pushed to Google Calendar, because the push step is keyed on
having one.

**Declined events are never deleted.** They are removed from Google Calendar
but the Postgres row stays with `status='declined'`. `GET /events` hides them
by default; `?include_declined=true` returns them. This is the audit trail for
"what did the system propose and what happened to it".

**`thread_id` is what makes `POST /events/resolve-thread` possible.** Confirming
one slot needs to find every sibling slot on the same thread and clear it.

---

## networth

### `accounts`

| Column | Type |
|---|---|
| `id` | `integer NOT NULL` |
| `name` | `text NOT NULL` |
| `balance` | `numeric NOT NULL` |
| `updated_at` | `text NOT NULL` |

Manual fallback. When Firefly is connected, net worth is read live from the
`firefly` service and these rows are not the source of truth.

### `recurring`

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `account_id` | `integer NOT NULL` | References `accounts.id` by convention. |
| `amount` | `numeric NOT NULL` | |
| `interval_days` | `integer NOT NULL` | |
| `next_due_at` | `text NOT NULL` | |
| `created_at` | `text NOT NULL` | |

Applied by `POST /recurring/apply`, not by a background job — nothing changes a
balance unless that endpoint is called.

---

## Leaf-service tables

### `tasks` (tasks)

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `title` | `text NOT NULL` | |
| `done` | `boolean NOT NULL` | |
| `created_at` | `text NOT NULL` | |
| `external_id` | `text` | For tasks created from another sub-app. |

### `bills` (finance)

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `name` | `text NOT NULL` | |
| `amount` | `numeric NOT NULL` | |
| `due_day` | `integer NOT NULL` | Day of month, judged against `LOCAL_TZ`. |
| `category` | `text NOT NULL` | |
| `created_at` | `text NOT NULL` | |

### `deals` (deals)

| Column | Type | Notes |
|---|---|---|
| `id` | `integer NOT NULL` | |
| `merchant` | `text NOT NULL` | |
| `offer` | `text NOT NULL` | |
| `source` | `text NOT NULL` | Usually the source email. |
| `external_id` | `text` | Dedupe across syncs. |
| `created_at` | `text NOT NULL` | |

### `visits` (fitness)

| Column | Type |
|---|---|
| `id` | `integer NOT NULL` |
| `when_at` | `text NOT NULL` |
| `note` | `text NOT NULL` |

### `budget` — **legacy, unused**

| Column | Type |
|---|---|
| `id` | `integer NOT NULL` |
| `name` | `text NOT NULL` |
| `limit_amount` | `numeric NOT NULL` |
| `spent` | `numeric NOT NULL` |
| `created_at` | `text NOT NULL` |

Left over from when the budget service persisted its own definitions. The
budget service is now **stateless** — definitions live in `core_settings.data`
and transactions come from the `firefly` service. The table still exists and is
empty. Nothing reads or writes it. Dropping it would be a destructive migration
and therefore a high-risk action requiring approval; leaving it costs nothing.

---

## Timestamp conventions

An inconsistency worth knowing before you write a query: **older tables store
timestamps as `text`** (ISO-8601 strings), while the newer `core` tables use
real `date` / `timestamptz` columns. Both appear above. Sorting `text`
timestamps works only because ISO-8601 sorts lexicographically — do not assume
you can do interval arithmetic on them in SQL without a cast.

Separately, and more importantly: **the box runs in UTC, but day boundaries in
the product mean the user's local day.** Services that make a "today" judgment
(`core`, `finance` due-days, `fitness` plan, `firefly` windows) resolve it
through `LOCAL_TZ`, not through the container clock. A query written directly
against these tables in UTC will disagree with the dashboard around midnight.
See [TESTING.md](TESTING.md) for the incident that established this rule.
