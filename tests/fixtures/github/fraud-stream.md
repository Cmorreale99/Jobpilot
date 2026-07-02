# fraud-stream

Realtime card-fraud scoring over a Kafka event stream.

## Highlights

Problem: Batch fraud models scored transactions hours late, too slow to block fraud.
Action: Built a Flink job computing rolling velocity features served from a low-latency store.
Result: Delivered sub-50ms scoring at 20k transactions/second on a single cluster.

Problem: Model changes shipped without a way to measure impact on historical fraud.
Action: Added a backtesting harness that replays the event log against candidate models.
Result: Cut model evaluation turnaround from two days to under an hour.

## Stack

- Python scoring service behind gRPC
- Kafka + Flink streaming
- Terraform-managed infrastructure
