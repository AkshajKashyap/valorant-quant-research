# Milestone 3: leakage-safe historical feature baseline

## Decision: NO-GO FOR FEATURE EXPANSION

The small historical-feature logistic model does not improve forecasting beyond
raw K=64 Elo or Elo-only logistic regression. In fact, the full model is
materially worse than Elo-only logistic in aggregate date-cluster bootstrap
comparisons. Do not add further generic recent-form, player, map, or roster
features on this foundation yet.

The available 2024 period was already inspected in prior milestones and is not
an untouched holdout. This milestone uses rolling-origin, expanding-window
out-of-fold deployment predictions instead.

## Folds and leakage-safe features

| Fold | Model fitting history | Evaluation | N evaluated |
|---|---|---|---:|
| 1 | through 2021 | 2022-01-01 to 2022-06-30 | 3,517 |
| 2 | through 2022-06-30 | 2022-07-01 to 2022-12-31 | 118 |
| 3 | through 2022 | 2023 | 323 |
| 4 | through 2023 | available 2024 | 373 |

Each date's team state is frozen before feature construction. Ratings, win
histories, match counts, and last-match dates update only after all matches on
that date are processed. Features are:

- `elo_diff`: pre-date K=64 Elo difference.
- `last_5_win_rate_diff`, `last_10_win_rate_diff`: each team's available prior
  results only; no history defaults to neutral 0.5.
- `prior_match_count_diff`, `min_prior_match_count`.
- `days_since_last_match_diff`, with a missing-last-date indicator rather than
  interpreting unseen status as poor form.

No current-match, map, player, economy, odds, or post-match fields were used.
Logistic regression uses fixed C=1 with median imputation and standardization
fit only on each fold's training rows.

## Aggregate out-of-fold results

| Model | N | Log loss | Brier | Accuracy | Mean confidence |
|---|---:|---:|---:|---:|---:|
| 50/50 | 4,331 | 0.6931 | 0.2500 | 59.8%* | 50.0% |
| Raw Elo | 4,331 | **0.6369** | **0.2229** | 64.1% | 62.6% |
| Elo-only logistic | 4,331 | 0.6449 | 0.2230 | **64.7%** | 69.4% |
| Elo + recent win rates | 4,331 | 0.6472 | 0.2236 | 64.6% | 69.5% |
| Full historical logistic | 4,331 | 0.6744 | 0.2301 | 63.6% | 71.6% |

`*`50/50 accuracy uses a deterministic Team-A tie-break and is descriptive
only. Log loss is the primary metric.

Raw Elo wins the probability comparison. The logistic models are more
confident, not better calibrated in aggregate.

## Results by temporal fold

| Fold | Raw Elo LL | Elo-logistic LL | Full LL | Best probability model |
|---|---:|---:|---:|---|
| 2022 H1 | 0.6298 | **0.6292** | 0.6645 | Elo-only logistic, marginally |
| 2022 H2 | **0.7463** | 0.8019 | 0.8425 | Raw Elo, though all lose to 50/50 |
| 2023 | **0.6144** | 0.6453 | 0.6398 | Raw Elo |
| 2024 available | **0.6886** | 0.7430 | 0.7438 | Raw Elo |

The Elo-only calibration/shrinkage hypothesis is not supported outside the
first, much larger 2022-H1 fold. The full model helps only in 2023 and loses in
the other three folds.

## Paired date-cluster bootstrap comparisons

All results use 10,000 fixed-seed calendar-date bootstrap draws over 457 dates.
Negative deltas favor the challenger.

| Comparison | Mean Δ log loss | 95% interval | Favor challenger | Mean Δ Brier | 95% interval |
|---|---:|---:|---:|---:|---:|
| Raw Elo − 50/50 | -0.0562 | [-0.0719, -0.0396] | 100.0% | -0.0271 | [-0.0340, -0.0199] |
| Elo-logistic − raw Elo | +0.0080 | [-0.0036, +0.0213] | 9.0% | +0.0001 | [-0.0038, +0.0044] |
| Full − Elo-logistic | +0.0294 | [+0.0189, +0.0414] | 0.0% | +0.0071 | [+0.0038, +0.0110] |

This is a stability analysis rather than a formal significance claim. It gives
strong evidence that the full pre-registered feature bundle is worse than the
Elo-only logistic model, and no evidence that Elo-only logistic improves raw
Elo.

## Calibration, coefficients, and cold starts

Aggregate five-bin favorite calibration shows raw Elo is moderately
overconfident only at high probabilities (0.93 predicted versus 0.85 observed
in its 0.9–1.0 bin). Both logistic models are more overconfident: Elo-only
predicts 0.94 versus 0.84, and full history 0.95 versus 0.81, in their highest
bins. The full calibration table is in `calibration.csv`.

Full-model standardized coefficients have stable signs across folds but are
not causal evidence: Elo is dominant and positive (0.70–0.76); 10-match form
is positive (0.19–0.25), while 5-match form is negative (-0.15 to -0.20) after
conditioning on overlapping 10-match form. This collinearity and the model's
worse performance are reasons not to interpret these terms as signal.

For both-seen teams (3,199 predictions), full history has log loss 0.7057,
worse than raw Elo's 0.6391. For cold-start rows (1,132), full history is
better than raw Elo (0.5858 vs 0.6308), but this subgroup gain does not offset
the large both-seen deterioration and is not a basis for a new model.

## Outputs and checks

Generated in `data/processed/milestone_3/`:

- `historical_feature_snapshot.csv`
- `rolling_oof_predictions.csv`
- `per_fold_metrics.csv`, `aggregate_metrics.csv`
- `paired_bootstrap.json`, `calibration.csv`
- `coefficients.csv`, `cold_start_metrics.csv`

`pytest` passes 14 tests, including prior-only rolling features, same-date row
order invariance, neutral unseen history, prior-date fold separation,
single out-of-fold prediction coverage, training-only preprocessing through the
pipeline, and all previous Elo/date-batching invariants.

## Exact recommended next experiment

Do not expand generic features. Run a **pre-registered raw-Elo rolling-origin
stability study**: compare only fixed K={8,16,24,32,48,64} with the same daily
batching across multiple sequential pre-2024 folds, selecting K strictly from
earlier folds. Its purpose is to determine whether any raw-Elo configuration
has stable probability performance before pursuing a qualitatively different
data direction such as rigorously timestamped roster or matchup information.
