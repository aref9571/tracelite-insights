# Evaluation

This file records how well the spike detectors find the incidents of the simulator. Every number comes from `insights evaluate` and can be reproduced with the command given above its table.

## Method

`insights evaluate` runs the same steps for every seed:

1. Simulate 28 days of traffic ending on 2026-10-20 00:00 UTC (`--days` changes the length).
2. Load the simulation into a temporary DuckDB database.
3. Build one hourly series per application and environment and run the detector on it.
4. Compare the flagged hours with the incident windows in `truth.json`.

The detector never sees `truth.json`. Each simulation has 10 incidents, 3 of them small on purpose (at most 2 hours, at most 6 times the normal rate of the affected failure types).

Results are reported as the mean ± sample standard deviation over seeds 2, 3, 4 and 5. Seed 1 is kept for choosing thresholds, so the reported numbers never come from the data a threshold was tuned on.

### Metrics

| Metric | Meaning |
|:--|:--|
| Precision | Share of flagged hours that lie inside an incident window of the same series |
| Recall | Share of incident hours that were flagged |
| F1 | Harmonic mean of precision and recall |
| Incident recall | Share of incidents with at least one flagged hour from the incident start up to one hour after its end |
| False alerts/week | Flagged hours outside every incident window (including that extra hour), per week in which the detector has a score |
| Median delay (h) | Median time from the incident start to its first flagged hour, over the detected incidents |

Precision, recall and F1 count the hours of all series together. A ratio with nothing to divide by (for example precision without any flagged hour) is 0. Median delay is averaged only over seeds with at least one detected incident.

## Rolling z-score baseline

```bash
insights evaluate --seeds 2,3,4,5 --detector zscore --threshold 4
```

| Detector | Threshold | Precision | Recall | F1 | Incident recall | False alerts/week | Median delay (h) |
|:--|--:|--:|--:|--:|--:|--:|--:|
| zscore | 4 | 0.55 ± 0.31 | 0.16 ± 0.11 | 0.25 ± 0.16 | 0.25 ± 0.13 | 1.08 ± 0.57 | 1.00 ± 1.08 |

Per seed:

| Seed | Precision | Recall | F1 | Incidents detected | False alerts |
|--:|--:|--:|--:|--:|--:|
| 2 | 0.86 | 0.22 | 0.35 | 4 of 10 | 1 |
| 3 | 0.77 | 0.29 | 0.42 | 3 of 10 | 3 |
| 4 | 0.38 | 0.09 | 0.15 | 2 of 10 | 5 |
| 5 | 0.20 | 0.05 | 0.08 | 1 of 10 | 4 |

The threshold of 4 is the starting value, not a tuned one.

Observations:

- The detector finds only a quarter of the incidents. Two reasons stand out:
  - It needs 168 hours of history before it can score an hour, but the simulator places incidents from hour 24 on. 9 of the 40 incidents start in the first week and cannot be detected.
  - An incident multiplies the rate of a few failure types, while the series counts all errors of an application and environment. A ×15 incident on failure types with 0.2 errors per hour adds only about 3 errors per hour to a series with about 6, which is well inside the normal day-to-night swing.
- The spread between seeds is large: precision ranges from 0.20 to 0.86. With 10 incidents per seed, one detected or missed incident changes incident recall by 0.1, so single-seed numbers are not reliable.
- About one false alert per week is a low burden for an on-call engineer. All 13 false alerts over the four seeds fall between 09:00 and 19:00 UTC, the busy part of the simulated day, which the rolling mean over all hours of the week treats as unusual. 10 of them are in the quieter staging series, where a few extra errors already reach the threshold.

## Seasonal median detector compared with the rolling z-score

The `seasonal` detector compares each hour with the same hour of the week in the 3 previous weeks and the hour on either side (9 values). Expected count = median of those values; spread = MAD × 1.4826, at least `sqrt(expected)` and at least 1; score = `(count - expected) / spread`.

### Period

The seasonal detector needs 3 weeks and 1 hour of history. On 28 days it would have a score only in the last 167 hours, so both detectors are compared on 56 days instead. On 56 days, 1 of the 10 incidents of each seed starts during the warm-up of the z-score detector, and 3 or 4 start during the warm-up of the seasonal detector (14 of the 40 incidents of seeds 2 to 5). Incidents in the warm-up still count as missed.

### Choosing the thresholds on seed 1

```bash
insights tune --seed 1 --detector zscore --days 56
insights tune --seed 1 --detector seasonal --days 56
```

Rolling z-score on seed 1:

| Threshold | Precision | Recall | F1 | Incident recall | False alerts/week |
|--:|--:|--:|--:|--:|--:|
| 2 | 0.02 | 0.23 | 0.03 | 0.40 | 41.71 |
| 2.5 | 0.04 | 0.23 | 0.07 | 0.40 | 17.43 |
| 3 | 0.05 | 0.18 | 0.08 | 0.30 | 10.14 |
| 3.5 | 0.11 | 0.18 | 0.14 | 0.30 | 4.43 |
| 4 | 0.17 | 0.18 | 0.18 | 0.30 | 2.71 |
| 4.5 | 0.25 | 0.14 | 0.18 | 0.20 | 1.29 |
| 5 | 0.22 | 0.09 | 0.13 | 0.10 | 1.00 |
| 5.5 | 0.33 | 0.05 | 0.08 | 0.10 | 0.29 |
| 6 | 0.00 | 0.00 | 0.00 | 0.00 | 0.29 |
| 6.5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 |
| 7 | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 |
| 7.5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 |
| 8 | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 |

Seasonal median on seed 1:

| Threshold | Precision | Recall | F1 | Incident recall | False alerts/week |
|--:|--:|--:|--:|--:|--:|
| 2 | 0.02 | 0.27 | 0.03 | 0.50 | 68.28 |
| 2.5 | 0.02 | 0.09 | 0.03 | 0.20 | 24.23 |
| 3 | 0.02 | 0.09 | 0.04 | 0.20 | 16.02 |
| 3.5 | 0.00 | 0.00 | 0.00 | 0.00 | 4.81 |
| 4 | 0.00 | 0.00 | 0.00 | 0.00 | 3.20 |
| 4.5 | 0.00 | 0.00 | 0.00 | 0.00 | 1.80 |
| 5 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 |
| 5.5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.20 |
| 6 | 0.00 | 0.00 | 0.00 | 0.00 | 0.20 |
| 6.5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| 7 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| 7.5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| 8 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

The best hour-level F1 on seed 1 is at threshold 4 for `zscore` (F1 0.18) and at threshold 3 for `seasonal` (F1 0.04).

### Results on seeds 2 to 5

```bash
insights evaluate --seeds 2,3,4,5 --detector zscore --threshold 4 --days 56
insights evaluate --seeds 2,3,4,5 --detector seasonal --threshold 3 --days 56
```

| Detector | Threshold | Precision | Recall | F1 | Incident recall | False alerts/week | Median delay (h) |
|:--|--:|--:|--:|--:|--:|--:|--:|
| zscore | 4 | 0.39 ± 0.14 | 0.24 ± 0.08 | 0.30 ± 0.10 | 0.38 ± 0.10 | 1.50 ± 0.44 | 0.25 ± 0.50 |
| seasonal | 3 | 0.11 ± 0.06 | 0.33 ± 0.11 | 0.16 ± 0.08 | 0.38 ± 0.10 | 16.17 ± 1.35 | 0.25 ± 0.50 |

Per seed:

| Seed | Detector | Precision | Recall | F1 | Incidents detected | False alerts | Monitored hours |
|--:|:--|--:|--:|--:|--:|--:|--:|
| 2 | zscore | 0.47 | 0.26 | 0.33 | 3 of 10 | 8 | 1176 |
| 2 | seasonal | 0.08 | 0.30 | 0.13 | 3 of 10 | 87 | 839 |
| 3 | zscore | 0.55 | 0.34 | 0.42 | 4 of 10 | 10 | 1176 |
| 3 | seasonal | 0.19 | 0.49 | 0.27 | 5 of 10 | 72 | 839 |
| 4 | zscore | 0.25 | 0.16 | 0.19 | 5 of 10 | 15 | 1176 |
| 4 | seasonal | 0.10 | 0.28 | 0.15 | 4 of 10 | 79 | 839 |
| 5 | zscore | 0.31 | 0.19 | 0.24 | 3 of 10 | 9 | 1176 |
| 5 | seasonal | 0.06 | 0.24 | 0.09 | 3 of 10 | 85 | 839 |

Observations:

- Both detectors find 15 of the 40 incidents, but not the same way. The seasonal detector can reach only the 26 incidents after its warm-up and finds 15 of them; the z-score detector can reach 36 and finds 15. Its hour-level recall is also higher (0.33 against 0.24): inside the incidents each detector finds, the seasonal detector flags 39 of 50 incident hours and the z-score detector 28 of 45.
- The price is the false alerts: about 16 per week against 1.5. 228 of the 323 false alerts of the seasonal detector over the four seeds are hours whose expected count is 0 or 1. There the spread is at its floor of 1, so 3 or 4 errors in a usually quiet hour already reach the threshold of 3. 204 of the 323 are in the staging series, which have about one error per hour. The z-score detector does not flag hours with fewer than 3 errors, which removes most of these cases; the seasonal detector has no such rule.
- The thresholds come from a single seed, and seed 1 is a poor tuning seed for the seasonal detector: after its warm-up, seed 1 has 7 incidents that last at most 2 hours, and its F1 on seed 1 is at most 0.04 for every threshold. The choice of 3 therefore rests on very little evidence and leads to a low threshold with many false alerts on the other seeds. Tuning on several seeds would make the choice more stable.
- The tests in `tests/test_detection_seasonal.py` show the cases the seasonal detector is built for: on a series with a sharp two-hour daily peak and no incident it flags far fewer hours than the z-score detector at the same threshold, and it catches a spike at a usually quiet hour that the z-score detector misses. The simulated daily cycle is gentler, so this advantage hardly shows in the numbers above.
