# Evaluation

This file records how well the spike detectors find the incidents of the simulator. Every number comes from `insights evaluate` and can be reproduced with the command given above its table.

## Method

`insights evaluate` runs the same steps for every seed:

1. Simulate 28 days of traffic ending on 2026-10-20 00:00 UTC.
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
