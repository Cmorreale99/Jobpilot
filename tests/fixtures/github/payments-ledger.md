# payments-ledger

Idempotent, sharded settlement ledger for high-volume payouts.

## Highlights

Problem: Nightly settlement missed the banking cutoff, delaying merchant payouts.
Action: Re-architected settlement into idempotent, sharded workers with a replayable log.
Result: Cut settlement runtime from 6 hours to 40 minutes with no missed cutoffs.

## Notes

- Written in Go, backed by PostgreSQL.
- Exactly-once semantics via an idempotency key per payout.
