# Consulting engagement — ledger migration

Problem: A regional bank ran settlement reconciliation on a nightly batch that took 11 hours and regularly breached its reporting deadline.
Action: Led a three-person engagement to re-platform reconciliation onto an event-sourced ledger with incremental Kafka-fed materialized views in Python.
Result: Reconciliation finished in 40 minutes and the bank met every reporting deadline in the following two quarters.

Problem: The client had no way to audit historical balance corrections.
Action: Designed an append-only correction journal with cryptographic chaining and a replay tool.
Result: Auditors signed off on the correction trail with zero findings.
