# Milestone 6 PREREGISTERED 30-MATCH CHECKPOINT: 30 eligible completed matches

> This is the separate preregistered 30-match checkpoint.

## 1. Sample construction

The sample is the first eligible completed fixtures ordered mechanically by scheduled-start UTC, active-forecast ledger position, and PandaScore match ID. No outcome, forecast, odds, or performance field is used to select fixtures.

PandaScore IDs: 1607774, 1607779, 1608111, 1608112, 1561528, 1561529, 1607776, 1561489, 1607778, 1561490, 1608113, 1561530, 1561531, 1607788, 1561491, 1608128, 1561532, 1561533, 1607786, 1561493, 1561494, 1607787, 1608137, 1608138, 1561534, 1607785, 1619609, 1619610, 1608163, 1608164.

## 2. Data integrity checks

- 30 fixtures have exactly one active forecast, one active primary market selection, one valid finished binary outcome, valid two-sided Bet365 ML odds, and no terminal exclusion.
- All no-vig probability pairs sum to 1 within floating-point tolerance; primary lead times range from 57.60 to 72.88 minutes.
- The ledger was read only; no prospective event was added, changed, or removed.

## 3. Elo vs Bet365 headline metrics

| Metric | Elo | Bet365 no-vig | Better |
| --- | ---: | ---: | --- |
| Log loss | 0.7649 | 0.7678 | Elo |
| Brier | 0.2700 | 0.2781 | Elo |
| Accuracy | 0.5625 | 0.5667 | Bet365 |

Mean probability assigned to the actual winner: Elo 0.5406; Bet365 0.5168. Mean absolute Elo–market disagreement: 0.2286.
Accuracy treats an exact 0.5 as no directional prediction excluded (Elo: 14; Bet365: 0).

## 4. Paired loss comparison

Differences are Elo minus Bet365; negative favors Elo.

| Difference | Mean | Median | Elo better | Market better | Ties |
| --- | ---: | ---: | ---: | ---: | ---: |
| Log loss | -0.0028 | -0.0783 | 17 | 13 | 0 |
| Brier | -0.0080 | -0.0207 | 17 | 13 | 0 |

## 5. Bootstrap uncertainty

A UTC calendar-date-clustered percentile bootstrap used fixed seed 20260811 and 10,000 replicates. The 95% interval for mean Elo-minus-market log-loss is [-0.2158, 0.2671]; for Brier it is [-0.0846, 0.0986]. Uncertainty is very high at this sample size.

## 6. Calibration

Across this small sample, mean team-A probability is Elo 0.5155, Bet365 0.5318, while the empirical team-A win rate is 0.4333. No granular calibration bins or strong calibration conclusions are reported for n=30.

## 7. Pre-registered disagreement buckets

These fixed diagnostic buckets are not betting strategies. Boundaries are <2 pp, [2,5) pp, [5,10] pp, and >10 pp.

| Bucket | n | Elo LL | Market LL | Elo Brier | Market Brier | Elo accuracy | Market accuracy | Win rate: Elo-favored side |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| < 2 pp | 1 | 0.1311 | 0.1435 | 0.0151 | 0.0179 | 1.0000 | 1.0000 | 1.0000 |
| 2–5 pp | 1 | 0.5151 | 0.5492 | 0.1620 | 0.1786 | 1.0000 | 1.0000 | 1.0000 |
| 5–10 pp | 4 | 1.1851 | 1.0929 | 0.4451 | 0.4282 | 0.0000 | 0.0000 | 0.5000 |
| > 10 pp | 24 | 0.7317 | 0.7487 | 0.2560 | 0.2681 | 0.6364 | 0.6250 | 0.5417 |

## 8. Major limitations

This is a descriptive 30-fixture comparison with very high uncertainty. It makes no statistical-significance, market-inefficiency, betting-edge, profitability, or production-readiness claim. No post-hoc subset is selected.

## 9. Next checkpoint

The preregistered 30-match checkpoint is complete. The collector continues
unchanged. The next larger descriptive checkpoint will use 100 strictly
eligible completed matches; no model or protocol changes will be made before
that checkpoint.
