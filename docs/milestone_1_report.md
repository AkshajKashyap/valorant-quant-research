# Milestone 1: frozen dataset audit

## Decision

**No-go for a leakage-free historical match-winner forecasting baseline from
this snapshot alone.** The source contains no match date, time, or timestamp
in any match-result or identifier table. Its `Year` field/`vct_YYYY` folders
cannot order matches within a year, so using it for Elo, rolling form, or any
train/test split would silently introduce an arbitrary chronology.

The snapshot is still useful as an outcome/statistics archive and a candidate
to revisit only after a separately frozen, legally usable timestamp mapping is
validated at the match-ID level. No VLR scraper is part of this project.

## Frozen input

- Source: Kaggle `ryanluong1/valorant-champion-tour-2021-2023-data`
- Retrieved snapshot: version 47, titled *Valorant Champion Tour 2021-2026
  Data*, source last updated 2026-06-26; the source has expanded beyond the
  originally expected 2021-2025 coverage.
- Licence displayed by Kaggle: MIT.
- Archive SHA-256:
  `b3982b1270c33f86473cf70d174d487a963e77c9e829f6820bb1602ac0339e93`.
- Location: `data/raw/kaggle_ryanluong1_valorant_champion_tour/v47/`.
  The archive and extracted data are ignored by Git; its tracked manifest
  records the snapshot identity and immutability rule.

## Actual dataset structure

There are 131 CSV files: five global ID files plus 21 files for each of the
six year folders (`vct_2021` through `vct_2026`). Every year has the same
folder layout: `agents/` (3 files), `ids/` (4), `matches/` (13), and
`players_stats/` (1).

The deterministic complete file/column/null-count/hash inventory is generated
at `data/processed/milestone_1_v47/inspection.json`. The key tables are:

| Table(s) | Actual grain and key columns | Role |
|---|---|---|
| `matches/scores.csv` | one score record per apparent series; `Tournament`, `Stage`, `Match Type`, `Match Name`, teams, series score/result | only observed series-level outcome source |
| `all_ids/all_matches_games_ids.csv` | one map/game; tournament/stage/match-type/name plus `Match ID`, `Map`, `Game ID`, `Year` | match-to-map ID bridge |
| `matches/maps_scores.csv`, `maps_played.csv` | map within series | post-match map outcome/details |
| `matches/overview.csv`, `kills*.csv`, `eco*.csv`, `rounds_kills.csv`, `win_loss*` | player/map/round/event | post-match statistics |
| `matches/draft_phase.csv` | team action within a series | pick/ban data; not pre-match at series cutoff |
| `all_ids/all_teams_ids.csv`, `all_players_ids.csv`, `all_tournaments_stages_match_types_ids.csv` | name-to-ID mappings/context | identifiers, with ambiguity/missingness |
| `agents/*.csv`, `players_stats/players_stats.csv` | tournament/stage aggregate | retrospective aggregates; unsafe as supplied |

`scores.csv` has 12,676 rows overall. It is the closest available match-series
table, but not a complete canonical match table: it has no native ID, timestamp,
format, region, patch, venue, or roster field. It is linked to IDs by the five
text fields `(Tournament, Stage, Match Type, Match Name, Year)`.

## Match counts and time coverage

| Source year partition | Score records |
|---:|---:|
| 2021 | 7,224 |
| 2022 | 3,842 |
| 2023 | 331 |
| 2024 | 434 |
| 2025 | 503 |
| 2026 | 342 |
| **Total** | **12,676** |

The only temporal extent is the source partition range **2021–2026**. There
is **no actual date range** to report: `Match Start UTC` is missing for all
12,676 rows. This is not repaired with file order, source match ID, stage, or
tournament name. The generated audit table consequently sets every row's
`Chronology Eligible` to `False`.

## Linkage and quality findings

- 12,672 score records resolve to one source match ID; four do not. Two
  textual match keys each map to two distinct IDs (`BLUE BEES.ESP vs LFT` and
  `No Country vs Sengoku Gaming`, both in 2021). The score rows are otherwise
  indistinguishable, so IDs are intentionally left null rather than assigned by
  file position.
- All resolved IDs are unique in the audit table. The match-to-game bridge has
  27,450 rows, with one missing `Game ID` and two missing map names.
- 75 score records have tied/missing score semantics and therefore no binary
  target under the conservative rule. Some show `1–1` while naming a winner;
  others are explicitly draws. They must be excluded from a binary winner
  target unless a source-specific resolution rule is independently verified.
- Six score records have no player `overview` rows (five 2021 qualifier
  matches and one 2025 showmatch). This verifies incomplete post-match
  statistics exist, although this check does not independently establish the
  source's broader China-coverage claim.
- `TBD` occurs in one score record and should be excluded as a non-team
  placeholder.
- 36 team names map to multiple team IDs; `Exotic` is confirmed with IDs 4964
  and 1301. As a result, 369 Team-A and 338 Team-B records cannot be assigned
  a unique team ID by name alone. The abbreviation mapping confirms `TP` maps
  to both Typhoon and Typhone. Names/abbreviations are not stable entity keys.
- The literal player name `nan` is present with player ID 10207; the loader
  preserves it as text. The global player-ID table has 414 missing IDs; the
  global team-ID table has one missing ID.

## Field eligibility

| Classification | Fields / rule |
|---|---|
| Directly pre-match usable | None for a strict chronological experiment until a match timestamp exists. Team participants, tournament/stage, and match type are plausible pre-match context, but this snapshot cannot establish a cutoff or chronological position. |
| Derived from historical data only | Elo, prior match record, recent/map/player performance, opponent-adjusted strength, roster continuity. They may only be calculated after a valid strict match ordering is supplied; never from the current match. |
| Conditional / timing uncertain | Team IDs/names (only where uniquely resolved), tournament/stage/match type, map draft/picks, player lineup, format, patch, region, venue. The files do not prove their pre-match availability, and several fields are absent entirely. |
| Outcome-only | Series scores/result, map scores, duration, player overview, kills, economy, rounds, win/loss methods, post-match agent statistics. |
| Unusable / leakage risk as supplied | `agents/*.csv` and `players_stats/players_stats.csv` tournament/stage aggregates: their calculation window is undocumented and can include the current/later match. IDs cannot provide chronology; row order must not be used. |

## Audit output and proposed future schema

`data/processed/milestone_1_v47/match_level_audit.csv` is deterministic and
contains source-match ID/status, source year, explicitly null timestamp,
chronology flag/reason, tournament/stage/type/name, both teams and conservative
ID mappings, score/result, and `Team A Won`. It is an **audit artifact**, not a
training dataset.

If timestamp enrichment succeeds, the canonical table should add and validate:
`source_match_id`, `match_start_utc`, `timestamp_precision`,
`chronology_eligible`, event/stage/type IDs, team IDs, source ordering,
best-of/format, region/venue/patch where timestamped, and outcome fields held
separate from features. Rows without a unique match ID, two teams, resolved
binary outcome, and strict timestamp must stay excluded.

## Checks run

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m valorant_quant.milestone1 \
  --raw-files data/raw/kaggle_ryanluong1_valorant_champion_tour/v47/files \
  --output-dir data/processed/milestone_1_v47
```

The tests verify literal `nan` preservation, series-level match-ID linkage,
absence of inferred chronology, and rejection of ambiguous text-key-to-ID
assignments. The inspection command produces file hashes, schema/null inventory,
and the audit table deterministically.

## Exact recommended next experiment

Do **not** start forecasting baselines. Run a timestamp-enrichment feasibility
experiment first: obtain one permitted, frozen, timestamped match-ID mapping
from a non-scraping source; deterministically join it to the 12,672 resolved
source match IDs; then measure exact-ID coverage, timestamp precision, and the
number of same-timestamp matches. Proceed to baseline Elo only if the mapping
covers at least 99% of candidate binary-outcome matches and supplies a strict
UTC ordering or an explicit conservative simultaneous-match exclusion rule.
