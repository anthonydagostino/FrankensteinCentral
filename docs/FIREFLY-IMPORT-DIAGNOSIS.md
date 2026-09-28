# The thin import — one account landing a fraction of its rows (SCRUM-40)

## What was seen

Counting by hand off a raw API dump (SCRUM-39), one card account had 26
withdrawals and **zero** credits over five months, against 129 withdrawals and
11 credits on another card over the same period. No total on the dashboard was
wrong in a way anyone could see: the totals were merely smaller. Nothing in the
hub's own layer can lose rows — it reads Firefly and only Firefly — so the loss
is on the way *into* Firefly, and the fix is in the importer's configuration
for that one account.

## What now watches for it

- `firefly /accounts-health` counts withdrawals and deposits per asset account
  over 60 days and flags `no_credits` (10+ purchases, not one payment or
  refund) and `stale` (nothing in three weeks while another account moved this
  week). It is on the Firefly panel and in `verify.sh`.
- `scripts/firefly-import.sh` judges every scheduled run by the ledger and
  writes `empty` when nothing entered; the home card shows *runs but nothing
  enters* when that coincides with a ledger that has been still for days.

Those name the shape. They cannot say which setting causes it — that needs the
importer's own log for that account's run, which the hub does not have.

## Decision tree — for the account that is thin

Run the import for that account alone, by hand, and read the importer's log.
The keys below are the Data Importer's configuration JSON, verified against
its source (`app/Services/CSV/Configuration/Configuration.php`).

1. **Are the rows in the source file at all?** Open the csv/OFX the importer
   was given. If the credits are missing there, the export from the bank is
   the problem (a "transactions" export that omits payments, or a date range
   that stops early) — nothing in Firefly or the importer will help.
2. **`duplicate_detection_method`.** `classic` hashes the whole row;
   `cell` uses one column as a unique id; `none` imports everything. A bank
   whose csv gives payments an empty or reused reference column, with
   `duplicate_detection_method: cell` on that column, drops every credit
   after the first as a "duplicate" — silently, one log line each. Switch that
   account to `classic`. Do **not** set `none` on a daily cron: every run
   would re-import every row in the file.
3. **`date_range` / `date_range_number` / `date_range_unit`.** A `nordigen`,
   `spectre` or `simplefin` flow fetches a window; `date_range: range` with a
   short window plus a run that missed a few days loses the rows between.
   `partial` or `all` for a catch-up run, then back to a window wider than
   the cron interval.
4. **`accounts` (the source ↔ Firefly account map).** A credit on a card
   maps the *card* as destination. If the account map points the card's
   credits at an account the importer cannot write (or at nothing), they are
   skipped. Check the map has the card in both directions.
5. **`pending_transactions`.** `false` (the default) drops pending rows. A
   feed that reports payments as pending for several days, read daily with a
   narrow `date_range`, can lose them: they are pending on the day they are
   in the window and settled after the window moved on. Widen the window
   before turning this on.
6. **The amount sign / role columns (`roles`, `mapping`).** For a file flow,
   a csv that carries payments as a separate positive-amount column, with only
   the debit column mapped to `amount`, imports no credits at all. Map the
   credit column (`amount_credit`) too.

Note the answer on SCRUM-40 with the key that was wrong, then re-run
`/accounts-health` (or `verify.sh`) — the `no_credits` flag on that account is
the acceptance signal, and it clears on its own once the credits land.
