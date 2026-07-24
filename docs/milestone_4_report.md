# Milestone 4: Valorant-specific incremental signal

## Decision: NO-GO

Map-pool and roster-state features did not improve the primary probability metric over fixed raw K=64 Elo. They slightly improved aggregate Brier but added confidence and have date-cluster log-loss intervals spanning zero. Further map/roster feature expansion is not justified.

## Feasibility audit

The 11,101 canonical series link through frozen source keys to 23,690 historical map rows. Map-score coverage is 11,094 series and player-overview (`Side=both`) lineup coverage is 11,096 series. `maps_scores.csv` provides map name, teams, and score, making historical map winners usable. The player overview table permits a historical team lineup union. Current-series maps and observed current lineups are excluded. No general reliable best-of field was found, so format, region, and event context were not modeled.

## Leakage-safe features

All state is frozen at the start of each calendar date; map and roster updates apply only after every series on that date. The map model contains raw Elo difference plus prior map-pool mean/max/min/std rating differences, map-history-count difference, and minimum map history. The roster ablation adds prior-only last-lineup-size difference, historical lineup continuity, recent-five-lineup unique-player difference, and missing-history indicator. It never compares against the current observed lineup.

## Aggregate rolling-origin results

| Model | Log loss | Brier | Accuracy | Confidence |
|---|---:|---:|---:|---:|
| 50/50 | 0.6931 | 0.2500 | 59.8%* | 50.0% |
| Raw Elo | 0.6369 | 0.2229 | 64.1% | 62.6% |
| Elo + map pool | 0.6370 | 0.2204 | 64.9% | 71.4% |
| Elo + map pool + roster | 0.6363 | 0.2201 | 64.9% | 71.4% |

`*`50/50 accuracy uses a Team-A tie-break and is descriptive only.

## Per-fold log loss

| Fold | Raw Elo | Map | Map + roster |
|---|---:|---:|---:|
| 2022 H1 (3,517) | 0.6298 | 0.6244 | 0.6236 |
| 2022 H2 (118) | 0.7463 | 0.7971 | 0.7936 |
| 2023 (323) | 0.6144 | 0.6322 | 0.6327 |
| 2024 available (373) | 0.6886 | 0.7092 | 0.7097 |

The apparent 2022-H1 gain does not persist: every later fold loses to raw Elo.

## Date-cluster bootstrap

10,000 fixed-seed draws over 457 date clusters; negative deltas favor the challenger.

| Comparison | Mean Δ log loss | 95% interval | Fraction favorable | Mean Δ Brier |
|---|---:|---:|---:|---:|
| Map − raw Elo | +0.0001 | [-0.0133, +0.0142] | 48.3% | -0.0025 |
| Map + roster − map | -0.0007 | [-0.0016, +0.0002] | 93.1% | -0.0002 |
| Map + roster − raw Elo | -0.0006 | [-0.0137, +0.0133] | 52.3% | -0.0028 |

All log-loss intervals span zero. The roster increment is tiny and uncertain.

## Calibration and limitations

The map models are more overconfident than raw Elo. In their 0.7–0.8 favorite bins, they predict about 0.75 while observed favorite win rates are 0.64 (map) and 0.62 (map+roster). See `calibration.csv`.

Limitations include text-key map bridges, fixed un-tuned map update rate, series-level player unions that obscure substitutions, incomplete lower-tier data, and calendar-date batching. These motivate caution, not more tuning.

## Outputs and checks

Outputs in `data/processed/milestone_4/` include the feasibility audit, feature snapshot, OOF predictions, per-fold/aggregate metrics, paired bootstrap results, and calibration table. The existing 15 temporal-invariant tests pass; they cover prior-date state, date batching, row-order invariance, fold separation, and training-only preprocessing.

## Exact recommended next experiment

Stop generic and team-history feature expansion. Run a data-quality research milestone to locate a legally permitted timestamped historical roster/transfer source with announcement effective dates, then assess roster-change features prospectively. Do not implement it yet or add betting functionality.
