# Daily flyer notifications

Publication events remain in `notification_jobs`. The worker resolves the existing
recipients (nearby customers, the branch manager and all admins), then collects all
ready events before freezing each user's daily summary. No table or column is added.

## Schedule and delivery

- The first summary can be sent at or after 10:00 in `Europe/Rome`. If there are no
  ready flyers at 10:00, the first later publication can trigger it.
- One daily job, keyed by `flyer-digest:<user UUID>:<Italian date>`, owns that day's
  summary. Later events wait until 10:00 the next day. Ineligible flyers, including
  flyers without current confirmed public offers, are discarded.
- The snapshot and consumed event IDs are durable JSON payloads. Planning uses a
  PostgreSQL advisory lock and explicit transactions; `DB_DSN` is required. Delivery
  still uses the configured batch size, concurrency, stale-lock recovery and deadlines.
- The inbox row and the push-attempt reservation commit atomically before contacting
  providers. A retry cannot recreate a deleted notification or attempt another push.
  This deliberately favors at-most-once attempts: a crash after reservation, or an
  ambiguous provider failure, may leave only the inbox notification. Delivery to a
  device cannot be guaranteed. Failures before reservation can retry; unattempted
  summaries from an earlier date restore their events into the next daily summary.
- Disabling account notifications suppresses push, not the inbox. Extraction and
  shared-list notifications retain their existing behavior.
- Completed jobs are part of delivery history. Do not purge today's summary jobs;
  deleting inbox notifications is independent of the daily delivery limit. During
  rollout, completed legacy recipient jobs also suppress a second publication alert
  on that same Italian day.

## API and clients

The notification kind remains `flyer_published`. New `data` contains `aggregation_key`,
`flyer_ids`, `supermarket_ids`, `kind` and `url: "/volantini"`. A representative
`flyer_id` lets older mobile clients deduplicate local/server copies; it does not
change the aggregate URL or scope. Arrays are JSON encoded
strings on FCM; large summaries send only kind, aggregation key and URL in the push
to respect the provider size limit, while the inbox retains the complete arrays; the aggregation key is a string on both transports. Legacy singular
notifications remain readable, with their original branch deep links. New summaries
open the full flyer page. Ship the compatible frontend before enabling this backend.

`GET /flyers/public` and `/flyers/discovery` retain their response shape. Within each
supermarket, `created_at` descending represents publication order; ID breaks ties.
The branch order is unchanged. Published target rows are created at publication time;
editing offers or validity does not reset that timestamp.

## The “Nuovo” badge

The frontend uses the later of the public target's `created_at` and midnight in
`Europe/Rome` on `valid_from`. The badge lasts 48 elapsed hours, including across DST,
and disappears earlier when validity ends. It is the same for guests and signed-in
users, independent of notifications, reading or local storage. Missing/invalid
publication or validity data does not create a badge. One page clock updates all
badges at their next boundary and when focus/visibility returns.

A supermarket shows “Nuovo” if any current flyer has that badge. The page preserves
its complete branch list, desktop drawer, mobile shelf, counter positions and card
height. Counters use the logo's light green (`#7eb009`) and orange (`#f75101`); the
“Nuovo” badge uses logo green (`#12643e`), white text and the top-right corner.

## Validation

Backend unit tests cover schedules, domain summaries, reservations and recovery;
integration tests use the isolated `girospesa-itest` database for transactions,
concurrent planner exclusion, batches, deletion, next-day carryover and eligibility.
Frontend tests cover exact boundaries, Italian midnights/DST, shared guest/customer
behavior, publication sorting and local/server deduplication. Desktop and mobile
smoke tests include deterministic badge screenshots and expiry with a controlled clock.
