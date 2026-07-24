# Milestone 2: leakage-safe chronological Elo baseline

## Decision: LIMITED GO

A minimal online Elo model beat the 50/50 probability baseline on the frozen
available-2024 test period: log loss **0.6886** versus **0.6931** and Brier
score **0.2448** versus **0.2500**. The effect is modest, the test has 373
series, and calibration is imperfect at high confidence. The chronology and
same-day methodology are sound enough to continue, but this is not evidence of
an exploitable betting edge.

## Canonical modeling table

`data/processed/milestone_2/canonical_matches.csv` contains one match series
per row, preserving source-side team ordering. It joins the Milestone 1 source
to the frozen Tier-B calendar-date mapping only by exact source/VLR match ID.
Required fields include `match_id`, `match_date`, calendar `year`, source
partition year, both team IDs/names, binary `team_a_won`, tournament/stage/type,
and both source snapshot identifiers.

Rows were retained only when they had a unique match ID, valid 2021–2024
calendar date, two distinct non-`TBD` teams with unambiguous IDs, and a binary
series result. It rejects duplicate IDs and the `1970-01-01` date placeholder.

| Calendar year | Series |
|---:|---:|
| 2021 warm-up | 6,770 |
| 2022 development | 3,635 |
| 2023 development | 323 |
| 2024 frozen test (available through Sep. 4) | 373 |
| **Total** | **11,101** |

No map, player, economy, kill, round, map-draft, roster, patch, region, or odds
field was used.

## Methodology

Every unseen team begins at Elo 1500. For each match:

`P(A wins) = 1 / (1 + 10^((R_B - R_A) / 400))`

`delta_A = K * (team_a_won - P(A wins))`, with the opposite delta for Team B.

The rating state is frozen at the start of a calendar date. Predictions and
deltas for every match on that date use those frozen ratings; team deltas are
summed and applied only after the date completes. This prevents arbitrary
within-day source order from leaking results into ratings.

2021 initializes ratings and is excluded from headline metrics. The fixed,
predeclared grid `K = {8, 16, 24, 32, 48, 64}` was selected solely by combined
2022–2023 log loss. Scale=400 and initial rating=1500 were fixed, with no other
parameters tuned. The K grid was not changed after the 2024 result was seen.

| K | 2022–2023 log loss | Brier | Accuracy |
|---:|---:|---:|---:|
| 8 | 0.6731 | 0.2401 | 62.5% |
| 16 | 0.6609 | 0.2342 | 62.7% |
| 24 | 0.6522 | 0.2302 | 63.2% |
| 32 | 0.6457 | 0.2272 | 63.6% |
| 48 | 0.6370 | 0.2232 | 64.1% |
| **64 selected** | **0.6320** | **0.2208** | **64.6%** |

K=64 was selected because it had the lowest development log loss. Its being the
largest value in the small grid is a limitation, not a reason to tune further
using the frozen 2024 test.

## Probability results

| Period | N | Elo log loss | 50/50 log loss | Elo Brier | 50/50 Brier | Elo accuracy | Mean confidence | Any unseen team |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 3,635 | 0.6336 | 0.6931 | 0.2216 | 0.2500 | 64.5% | 61.3% | 30.7% |
| 2023 | 323 | 0.6144 | 0.6931 | 0.2123 | 0.2500 | 66.3% | 68.5% | 3.7% |
| Development 2022–23 | 3,958 | 0.6320 | 0.6931 | 0.2208 | 0.2500 | 64.6% | 61.9% | 28.5% |
| Frozen 2024 test | 373 | 0.6886 | 0.6931 | 0.2448 | 0.2500 | 58.4% | 70.1% | 1.3% |

The 50/50 accuracy shown in outputs uses a deterministic Team-A tie-break and
is not a meaningful decision benchmark; log loss and Brier are the relevant
comparisons. Higher-current-Elo is the Elo accuracy decision rule used above.

## Calibration and cold starts

2024 calibration has limited samples per bin. It is broadly directional but
shows overconfidence: predictions in the 0.70–0.80 bin averaged 0.744 with a
0.500 observed Team-A win rate (46 matches); 0.90–1.00 averaged 0.936 versus
0.789 observed (19 matches). See
`data/processed/milestone_2/calibration_2024.csv` for all bins.

Across all years, both-seen matches (7,297) performed better than matches with
an unseen team (3,804): log loss 0.6212 vs 0.6421 and Brier 0.2158 vs 0.2262.
On the frozen 2024 period there were only five unseen-team matches, so no
strong cold-start conclusion is warranted there.

End-of-year rating distributions remain zero-sum around a 1500 mean. At the
end of 2024: 3,875 teams, standard deviation 80.9, range 1120.5–2140.2. The
complete yearly distributions are in `evaluation_metrics.json`.

## Outputs and checks

Generated deterministic outputs in `data/processed/milestone_2/`:

- `canonical_matches.csv`
- `development_k_grid.csv`
- `predictions_2024.csv`
- `evaluation_metrics.json`
- `calibration_2024.csv`
- `final_ratings.csv`

`pytest` passes 9 focused tests. They cover probability symmetry, equal-rating
probability, winner/loser and zero-sum updates, unseen-team initialization,
prior-prediction isolation from future matches, same-day row-order invariance,
and canonical-table rejection of duplicate IDs, invalid outcomes, and
placeholder dates.

## Limitations and next experiment

- Calendar dates require conservative batching; intraday information cannot be
  used.
- The test is only the available 2024 period through September 4, not a full
  calendar year.
- Team-ID ambiguity excluded 689 date-linked records, potentially changing
  scene/region composition.
- Public VLR-derived data retains provenance and completeness limitations.
- K selection hit the upper tested boundary, while frozen-test calibration is
  weak at high confidence.

The exact next experiment is a **pre-registered Elo robustness check**: retain
the same canonical table and daily batching; compare the already-fixed K=64
against the fixed development alternatives K=32 and K=48 on the same frozen
2024 output, reporting only probability metrics and calibration. Do not tune
new parameters, add features, or use odds. If the tiny 2024 advantage is not
stable across those predeclared alternatives, stop before feature expansion.
