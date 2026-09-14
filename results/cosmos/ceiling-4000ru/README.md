# Cosmos read sweep at a 4,000 RU/s autoscale ceiling

These 11 runs were executed with the container's autoscale maximum set to
**4,000 RU/s**. They are retained deliberately: they are a valid and important
*capacity* result, not a failed experiment.

At this ceiling every workload that returns payload is RU-bound rather than
latency-bound, and the measured throughput matches what the per-operation RU
measurements predict to within ~10%:

| Workload | RU/request (measured) | Predicted max RPS at 4,000 RU/s | Observed RPS |
| --- | ---: | ---: | ---: |
| `summary` | 4.28 | 934.6 | 50.0 (target met) |
| `search` | 8.00 | 500.0 | 50.0 (target met) |
| `mix` | 185.74 | 21.5 | 23.0 |
| `title` | 339.41 | 11.8 | 13.1 |
| `cdf` | 352.12 | 11.4 | 12.7 |
| `full` | 1,151.94 | 3.5 | 3.9 |

Per-operation RU comes from `results/cosmos/index-impact.json`.

The equivalent runs at a **40,000 RU/s** ceiling are in the parent directory and
are the ones the headline comparison uses. Both are reported, because "Cosmos
needs N RU/s to serve this workload" is the actual finding and it is only
visible by measuring both sides of the constraint.
