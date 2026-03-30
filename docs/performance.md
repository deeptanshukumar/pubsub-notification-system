# Performance Evaluation

This report is generated/updated from `scripts/load_harness.py`.

- Last run UTC: `2026-03-30T12:39:41Z`
- Source CSV: `results/rubric_benchmark.csv`
- Command: `python scripts/load_harness.py --run-standard-scenarios --output results/rubric_benchmark.csv --markdown docs/performance.md`

## Scenario Definitions

- `S1` baseline: 1 publisher, 1 subscriber
- `S2` moderate: 5 publishers, 10 subscribers
- `S3` stress: 10 publishers, 25 subscribers with higher message volume

## Latest Results

| Scenario | Pubs | Subs | Msg/Publisher | Topics | Total Requests | ACK Success | ACK Fail | Duration (s) | Throughput (msg/s) | Avg Latency (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| S1 | 1 | 1 | 120 | 2 | 120 | 120 | 0 | 0.21 | 562.93 | 1.24 | 0.95 | 1.15 | 1.69 | 40.96 |
| S2 | 5 | 10 | 180 | 5 | 900 | 900 | 0 | 0.73 | 1236.01 | 3.29 | 2.86 | 6.36 | 11.14 | 40.87 |
| S3 | 10 | 25 | 300 | 8 | 3000 | 3000 | 0 | 1.84 | 1633.04 | 5.62 | 4.98 | 10.99 | 14.86 | 41.16 |

## Interpretation (Rubric Discussion Points)

- S1 baseline latency/throughput reference: avg=1.24 ms, throughput=562.93 msg/s.
- S2 moderate concurrency behavior: avg=3.29 ms, p95=6.36 ms, ACK success=900/900.
- S3 stress behavior: avg=5.62 ms, p99=14.86 ms, ACK fail=0/3000.
- Latency percentiles rise as concurrency and publish volume increase, which is expected for thread/socket contention.

## Optimization Notes

- Current implementation prioritizes correctness and robustness over aggressive throughput tuning.
- Future optimization options: async I/O model, backpressure/rate limiting, finer-grained locking.

## Notes

- ACK latency is measured end-to-end from `PUBLISH` send to `ACK` receive.
- Throughput is computed as successful publishes divided by scenario runtime.
- Subscribers run concurrently and consume broker messages during the benchmark.
