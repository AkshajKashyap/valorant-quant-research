# Milestone 1.5: historical chronology source feasibility

## Decision: LIMITED GO

Proceed to a simple, leakage-controlled forecasting baseline only on the
**large-scale public CSV exact-ID subset**. It supplies a trustworthy-looking
calendar date (Tier B), not an exact time, for 11,790 of 12,672 resolved
Milestone 1 records (93.0%). Use mandatory same-day batching: construct every
feature for date *d* from records dated strictly before *d*, score all rows on
*d*, then update ratings/statistics once for the complete day.

This is a limited research go—not a provenance or betting-data go. It covers
2021–2024 only and inherits public VLR-derived-source and entity-quality risks.

## Frozen sources inspected

| Source | Frozen contents and actual schema | Date/time | VLR/source ID | Exact ID links to 12,672 source matches |
|---|---|---|---|---:|
| Kaggle `visualize25/valorant-pro-matches-full-data`, v1 | SQLite: `Matches` (7,818 series), `Games`, `Game_Rounds`, `Game_Scoreboard`. `Matches` fields include `MatchID`, `Date`, `Patch`, event/stage, teams/IDs, and map score. | Tier A-like datetime, all parseable, 2020-05-02 13:00 through 2022-01-08 15:30. Whether this is scheduled or actual start is undocumented. | `MatchID`; exact compatible IDs demonstrated. | 2,245 (17.7%) |
| Kaggle `hidious/valorant-vlrgg-results-and-stats`, v2 | `results.csv` (11,300 rows): teams, scores, `time_completed`, round/tournament, `match_page`; plus aggregate `stats.csv`. | Tier D. `time_completed` is relative text such as `7h 36m ago`, `5mo ago`, or `1y ago`; no absolute date appears. | Parsed from `/NNNNN/...` `match_page`; 11,300 valid IDs. | 3,162 (25.0%) |
| Public Google Drive `NewVLRDataRaw.csv` (“Large Scale Valorant Dataset 2020–2024”) | 129,156 map/game rows, 69,334 distinct `MatchID` series. Fields include `MatchID`, `GameID`, `EventID`, `Date`, team IDs/names, one `Series Odds`, one `Team1 Map Odds`, map outcome/statistics, round breakdown, and VOD link. | Tier B calendar date. 69,333 series have dates 2020-04-25 through 2024-09-04; one series uses the invalid `1970-01-01` placeholder. | `MatchID`; exact compatible IDs demonstrated. | 11,790 (93.0%) |

All archives/files were frozen under `data/raw/` with manifests and SHA-256
hashes. The two Kaggle sources are old snapshots. The Google Drive CSV is the
file linked from the public October 2025 Reddit post; it is map-level, not
series-level, despite the post's casual use of “games.”

## Linkage method and coverage

Only exact VLR/source-ID matching was used. No names, scores, event text,
ordering, fuzzy matching, or composite fallback was used. Therefore no join is
silently resolved and ambiguity from the original source remains visible.

| Existing-source partition | Resolved matches | Large-scale exact-date links | Coverage |
|---:|---:|---:|---:|
| 2021 | 7,220 | 7,192 | 99.6% |
| 2022 | 3,842 | 3,833 | 99.8% |
| 2023 | 331 | 331 | 100.0% |
| 2024 | 434 | 434 | 100.0% |
| 2025 | 503 | 0 | 0.0% |
| 2026 | 342 | 0 | 0.0% |
| **Total** | **12,672** | **11,790** | **93.0%** |

The large-scale candidate exceeds the 50%, 75%, and 90% coverage thresholds;
it does not reach 95% or 99%. This is acceptable for an explicitly restricted
2021–2024 experiment. The unmatched 882 records are not random over the full
snapshot: all 845 2025–2026 records are outside the candidate's end date, with
the remaining 37 concentrated in earlier partitions. The clean subset will
also underrepresent teams whose IDs are name-ambiguous in the original source.

Its date has no time component: all 129,156 rows are midnight calendar dates.
It is internally consistent within every `MatchID`; 695 linked calendar dates
contain 11,790 series, with up to 224 series on one day. Consequently, treating
same-day matches as sequential would be a major look-ahead risk.

The reproducible exact-ID date mapping is generated at
`data/processed/milestone_1_5/large_scale_exact_id_calendar_dates.csv`; it has
11,790 rows and labels every row `B_calendar_date` with the required batch rule.

## `time_completed` assessment

The hidious source does expose useful stable IDs, but it cannot establish
calendar chronology. All 11,300 `time_completed` values are relative to an
unspecified scrape instant/timezone; 3,593 use years, 7,295 use months, 248 use
weeks, 409 use days, and 164 use hours. Kaggle's publication timestamp is not
proof of the collection instant or timezone. Reconstructing dates from it would
be an unsupported assumption, so this source must not be used for chronology.

## Odds assessment: not useful for a historical betting backtest

The large CSV has only a single `Series Odds` scalar and a single `Team1 Map
Odds` scalar—there is no opposing series price, bookmaker, capture timestamp,
market timestamp, or evidence that the price was stored before play. `Series
Odds` is non-zero for only 4,393 of 69,334 series (6.34%), and is constant
within a series. The public post describes it as the pre-match odds for the
**eventual winning team**, which is outcome-selected semantics. That field is
therefore outcome-conditioned / leakage-risky, even if its numeric value
originated pre-match. It cannot price whichever team a model would have chosen,
and no opposing odds should be inferred. It is excluded from Milestone 2.

## Recommended Milestone 2 dataset and guardrails

Use the exact-ID joined 2021–2024 subset, then require:

1. valid Tier-B date from the large-scale mapping;
2. a resolved binary target in the Milestone 1 score table;
3. non-placeholder teams and unique IDs for both teams.

That produces **11,101** conservative candidate series (2021: 6,756; 2022:
3,649; 2023: 323; 2024: 373). Retain the wider 11,790 linked rows as an audit
population, not as a silent fallback. For each calendar date, compute pre-match
state only from earlier dates; do not update Elo, rolling statistics, or player
history until all matches on that date have received outcomes. Chronological
evaluation must use date-blocked train/test splits.

## Provenance and legal caveats

All three sources are publicly distributed derivatives of VLR.gg. This project
did not scrape VLR, but public availability does not independently establish
redistribution rights, data accuracy, or commercial/betting suitability.
Kaggle lists the visualize25 licence as unknown and the hidious licence as
“Data files © Original Authors”; the Google Drive source states no licence.
Use remains limited to internal research/prototyping pending an explicit
licensing review. Match dates and IDs are corroborated by exact cross-source
agreement, but their original collection semantics are not independently
audited.

## Reproduction

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m valorant_quant.milestone1_5 \
  --current-raw-files data/raw/kaggle_ryanluong1_valorant_champion_tour/v47/files \
  --visualize-db data/raw/kaggle_visualize25_valorant_pro_matches_full_data/v1/files/valorant.sqlite \
  --hidious-results data/raw/kaggle_hidious_valorant_vlrgg_results_and_stats/v2/files/results.csv \
  --large-csv data/raw/google_drive_benetheburrito_large_scale_valorant_2020_2024/v1/files/NewVLRDataRaw.csv \
  --output-dir data/processed/milestone_1_5
```

The command also emits `chronology_audit.json` with the exact counts above.
