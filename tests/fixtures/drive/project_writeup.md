# Realtime Fraud Detection Pipeline

A side project exploring streaming feature computation for card-fraud scoring.

## Overview

Built an end-to-end pipeline that scores transactions in under 50ms using streaming
features computed from a Kafka event stream.

## Contributions

Problem: Batch fraud models scored transactions hours after they occurred, too late to block fraud.
Action: Designed a Flink job computing rolling velocity features and served them from a low-latency feature store.
Result: Delivered sub-50ms scoring at 20k transactions/second on a single mid-size cluster.

Problem: The team had no reproducible way to evaluate model changes against historical fraud.
Action: Built a backtesting harness that replays the event log against candidate models.
Result: Reduced model evaluation turnaround from two days to under an hour.

## Stack

- Kafka, Flink, Redis feature store
- Python scoring service behind gRPC
- Terraform-managed infra
