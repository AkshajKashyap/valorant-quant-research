# Milestone 6 EXPLORATORY EARLY LOOK: 25 eligible completed matches

> **Exploratory-status warning:** This 25-match analysis was requested before the preregistered 30-match checkpoint, is exploratory only, and cannot justify model or protocol changes.

## 1. Sample construction

The sample is the first eligible completed fixtures ordered mechanically by scheduled-start UTC, active-forecast ledger position, and PandaScore match ID. No outcome, forecast, odds, or performance field is used to select fixtures.

PandaScore IDs: 1607774, 1607779, 1608111, 1608112, 1561528, 1561529, 1607776, 1561489, 1607778, 1561490, 1608113, 1561530, 1561531, 1607788, 1561491, 1608128, 1561532, 1561533, 1607786, 1561493, 1561494, 1607787, 1608137, 1608138, 1561534.

## 2. Data integrity checks

- 25 fixtures have exactly one active forecast, one active primary market selection, one valid finished binary outcome, valid two-sided Bet365 ML odds, and no terminal exclusion.
- All no-vig probability pairs sum to 1 within floating-point tolerance; primary lead times range from 57.60 to 72.88 minutes.
- The ledger was read only; no prospective event was added, changed, or removed.

## 3. Elo vs Bet365 headline metrics

| Metric | Elo | Bet365 no-vig | Better |
| --- | ---: | ---: | --- |
| Log loss | 0.8092 | 0.7861 | Bet365 |
| Brier | 0.2870 | 0.2852 | Bet365 |
| Accuracy | 0.5000 | 0.5600 | Bet365 |

Mean probability assigned to the actual winner: Elo 0.5294; Bet365 0.5128. Mean absolute Elo–market disagreement: 0.2439.
Accuracy treats an exact 0.5 as no directional prediction excluded (Elo: 11; Bet365: 0).

## 4. Paired loss comparison

Differences are Elo minus Bet365; negative favors Elo.

| Difference | Mean | Median | Elo better | Market better | Ties |
| --- | ---: | ---: | ---: | ---: | ---: |
| Log loss | 0.0231 | -0.0123 | 13 | 12 | 0 |
| Brier | 0.0018 | -0.0028 | 13 | 12 | 0 |

## 5. Bootstrap uncertainty

A UTC calendar-date-clustered percentile bootstrap used fixed seed 20260811 and 10,000 replicates. The 95% interval for mean Elo-minus-market log-loss is [-0.2320, 0.3758]; for Brier it is [-0.0834, 0.1447]. Uncertainty is very high at this sample size.

## 6. Calibration

Across this small sample, mean team-A probability is Elo 0.4993, Bet365 0.5243, while the empirical team-A win rate is 0.4400. No granular calibration bins or strong calibration conclusions are reported for n=25.

## 7. Pre-registered disagreement buckets

These fixed diagnostic buckets are not betting strategies. Boundaries are <2 pp, [2,5) pp, [5,10] pp, and >10 pp.

| Bucket | n | Elo LL | Market LL | Elo Brier | Market Brier | Elo accuracy | Market accuracy | Win rate: Elo-favored side |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| < 2 pp | 1 | 0.1311 | 0.1435 | 0.0151 | 0.0179 | 1.0000 | 1.0000 | 1.0000 |
| 2–5 pp | 0 | — | — | — | — | — | — | — |
| 5–10 pp | 4 | 1.1851 | 1.0929 | 0.4451 | 0.4282 | 0.0000 | 0.0000 | 0.5000 |
| > 10 pp | 20 | 0.7679 | 0.7569 | 0.2690 | 0.2700 | 0.6000 | 0.6500 | 0.5000 |

## 8. Major limitations

This is a descriptive 25-fixture comparison with very high uncertainty. It makes no statistical-significance, market-inefficiency, betting-edge, profitability, or production-readiness claim. No post-hoc subset is selected.

## 9. What happens at 30 matches

The collector continues unchanged. When 30 eligible completed fixtures exist, run `python -m valorant_quant.milestone6_evaluation --checkpoint 30`; it freezes the first 30 in the same ordering and writes a separate report without overwriting this early look.
