# Milestone 2.5: Elo robustness and signal validation

## Decision: LIMITED GO

The Elo signal is believable but weak and unstable. It beats 50/50 for every tested K from 8 through 64 on the already-inspected 2024 period, but the advantage is concentrated in part of the year, date-bootstrap uncertainty spans zero, and fixed K=64 is overconfident. This supports one careful robustness step before richer modeling, not feature expansion or betting research.

**2024 is not an untouched holdout.** It was evaluated in Milestone 2 and is used here only as a historical robustness set.

## K sensitivity on 2024

K=64 remains the Milestone 2 selected model; this table does not retune it.

| K | Log loss | Δ vs 50/50 | Brier | Δ vs 50/50 | Accuracy | Mean confidence |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0.6602 | -0.0330 | 0.2341 | -0.0159 | 57.6% | 57.2% |
| 16 | 0.6549 | -0.0382 | 0.2321 | -0.0179 | 57.6% | 61.0% |
| 24 | 0.6568 | -0.0364 | 0.2331 | -0.0169 | 59.5% | 63.5% |
| 32 | 0.6613 | -0.0318 | 0.2351 | -0.0149 | 59.0% | 65.3% |
| 48 | 0.6737 | -0.0194 | 0.2398 | -0.0102 | 59.5% | 68.1% |
| **64 primary** | **0.6886** | **-0.0045** | **0.2448** | **-0.0052** | **58.4%** | **70.1%** |
| 96 | 0.7236 | +0.0305 | 0.2547 | +0.0047 | 59.2% | 73.0% |
| 128 | 0.7640 | +0.0709 | 0.2642 | +0.0142 | 59.0% | 75.4% |

Elo beats 50/50 over the broad but bounded range K=8–64. K=64 is not unusually favorable on 2024—lower K values perform better there—but it was the development-selected model and remains frozen. Performance does not improve above K=64: K=96 and 128 are worse than 50/50 on both probability metrics and raise confidence substantially. This is consistent with larger K values being less calibrated.

## Year robustness at fixed K=64

| Year | N | Elo log loss | 50/50 log loss | Elo Brier | 50/50 Brier | Accuracy |
|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 3,635 | 0.6336 | 0.6931 | 0.2216 | 0.2500 | 64.5% |
| 2023 | 323 | 0.6144 | 0.6931 | 0.2123 | 0.2500 | 66.3% |
| 2024 available period | 373 | 0.6886 | 0.6931 | 0.2448 | 0.2500 | 58.4% |

The result is directionally positive in all three years, but 2023 and 2024 are much smaller samples than 2022. The 2024 advantage is far smaller than the earlier periods.

## 2024 chronological segments at K=64

No segment was used for tuning. There were no retained January or September series in the approved subset.

| Month | N | Δ log loss | Δ Brier | Accuracy |
|---|---:|---:|---:|---:|
| Feb | 62 | +0.0457 | +0.0161 | 53.2% |
| Mar | 19 | +0.0451 | +0.0217 | 52.6% |
| Apr | 94 | +0.0036 | -0.0023 | 55.3% |
| May | 47 | -0.0177 | -0.0052 | 53.2% |
| Jun | 59 | -0.0050 | -0.0116 | 67.8% |
| Jul | 64 | -0.0905 | -0.0410 | 70.3% |
| Aug | 28 | +0.0432 | +0.0155 | 46.4% |

Negative deltas favor Elo. The aggregate 2024 log-loss advantage is not broadly distributed: it is dominated by July, while February, March, and August are clearly worse than 50/50.

## Date-level bootstrap uncertainty

Using 10,000 fixed-seed (`20260724`) calendar-date cluster bootstrap draws over 127 dates—each sampled date carries all its matches:

| Metric delta (Elo − 50/50) | Observed mean | 95% bootstrap interval | Fraction below zero |
|---|---:|---:|---:|
| Log loss | -0.0045 | [-0.0600, +0.0530] | 56.4% |
| Brier | -0.0052 | [-0.0283, +0.0183] | 66.7% |

Both intervals span zero. This is an uncertainty/stability analysis, not a formal significance claim. It is compatible with weak positive signal and with sampling variation.

## Cold starts and experience depth

For 2024, both-seen matches comprise 368/373 records: log loss 0.6889, Brier 0.2449, accuracy 58.4%, confidence 70.2%. The five matches with an unseen team have log loss 0.6699, Brier 0.2404, accuracy 60.0%, confidence 62.0%; this is too small to interpret. Cold starts do not explain the aggregate result.

Experience buckets use the minimum prior-match count of the two teams before the date:

| Minimum prior matches | N | Δ log loss | Δ Brier | Accuracy |
|---|---:|---:|---:|---:|
| 0 | 5 | -0.0233 | -0.0096 | 60.0% |
| 1–4 | 24 | -0.1373 | -0.0522 | 62.5% |
| 5–19 | 66 | +0.0117 | -0.0045 | 63.6% |
| 20+ | 278 | +0.0035 | -0.0012 | 56.8% |

There is no evidence here that Elo becomes materially more useful once both teams have deep history. The apparently strong 1–4 bucket is only 24 matches.

## Five-bin favorite calibration

| Favorite probability bin | N | Mean predicted | Observed favorite win rate | Observed − predicted |
|---|---:|---:|---:|---:|
| 0.5–0.6 | 99 | 0.549 | 0.485 | -0.064 |
| 0.6–0.7 | 99 | 0.648 | 0.545 | -0.102 |
| 0.7–0.8 | 74 | 0.747 | 0.527 | -0.220 |
| 0.8–0.9 | 76 | 0.846 | 0.737 | -0.110 |
| 0.9–1.0 | 25 | 0.934 | 0.840 | -0.094 |

Observed favorite win rates rise in the upper bins overall, but every bin is overconfident; the 0.7–0.8 bin is the largest gap. This reinforces that no uncalibrated Elo probability should be interpreted as a market-ready estimate.

## Checks and outputs

`pytest` passes 11 focused tests. New checks cover fixed-seed date-bootstrap determinism, whole-date cluster preservation, per-match loss reproduction of aggregate metrics, and ensuring repeated K runs do not mutate the source table.

Generated files in `data/processed/milestone_2_5/`:

- `robustness_report.json`
- `k_sensitivity_2024.csv`
- `segments_2024.csv`
- `seen_team_2024.csv`
- `experience_2024.csv`
- `favorite_calibration_2024.csv`
- `losses_2024.csv`

## Exact Milestone 3 recommendation

Do **not** add player, map, or betting features yet. Run a pre-registered **rolling-origin temporal validation** of the same fixed Elo formulations K={8,16,24,32,48,64}, with calendar-date batching, across sequential pre-2024 cutoffs. Select one K solely from earlier folds and report later-fold log loss/Brier without using the 2024 robustness analysis for selection. If the advantage remains concentrated or disappears, stop before richer features.
