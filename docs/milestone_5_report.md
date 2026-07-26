# Milestone 5: prospective Valorant market-data feasibility

## Decision: LIMITED GO

The research workflow can now collect timestamped, two-sided pre-match
Valorant moneylines, bridge the frozen K=64 Elo state forward with completed
matches, reconcile fixtures, and freeze descriptive model-market comparisons.
It is materially limited by name-only team continuity, a two-bookmaker account,
and a provider-specific live snapshot rather than historical market coverage.
No betting, ROI, staking, or Milestone 6 work was added.

## PandaScore Elo bridge

The frozen canonical corpus ends on **2024-08-25**. The authenticated
`/valorant/matches/past` request used a documented `range[begin_at]` filter,
ascending sort, `per_page=100`, and all 65 reported pages. It returned 6,478
raw matches from 2024-08-26 through the capture cutoff
`2026-07-26T04:45:34Z`.

Eligibility required `status=finished`, two distinct non-placeholder teams, a
winner among those teams, usable begin/end timestamps, a date after the frozen
cutoff, and completion no later than capture time. Forfeits would be excluded
conservatively (none occurred in this collection). Results: 6,401 eligible
matches, 77 excluded (57 invalid winner, 13 missing begin/end, 7 placeholder
team), and state updated through **2026-07-25**. Every date uses the existing
simultaneous daily-batch update; exact PandaScore timestamps do not alter that
methodology.

The bridge observed 717 PandaScore teams: 116 exact-normalized-name links to a
unique frozen historical ID, 0 aliases, and 601 provider-only cold-start teams.
New identities retain a `ps:<PandaScore ID>` key and initialize at 1500. No
renames, abbreviations, or duplicate historical names were silently merged.

The raw paginated response is preserved as one immutable SHA-256-addressed
snapshot under the ignored `data/raw/market_pilot_live/v1/` convention. The
processed bridge audit contains only match identity, completion/outcome, and
audit context—no player, map, score, or other predictive statistics.

## Odds-API.io prices

The authenticated selected-bookmakers response changed from zero to two:
**Bet365** and **GG.bet**. Three pending event-odds calls using the documented
`bookmakers=Bet365,GG.bet` parameter returned HTTP 200. Bet365 supplied a
market named `ML`, both decimal prices, and a market-level UTC `updatedAt`.
GG.bet did not appear in these three returned market payloads.

| Event | Fixture | Start UTC | Bet365 ML | Market update UTC |
|---:|---|---|---|---|
| 5793669383 | Paper Rex vs Kiwoom DRX | 2026-07-26 08:00 | 1.28 / 3.50 | 2026-07-26 04:47:51.083Z |
| 7604310191 | BBL Esports vs Natus Vincere | 2026-07-26 15:00 | 1.50 / 2.50 | 2026-07-26 04:47:51.083Z |
| 6674269176 | Team Heretics vs Karmine Corp | 2026-07-26 18:00 | 1.61 / 2.20 | 2026-07-26 04:47:51.083Z |

All three are genuine two-sided decimal pre-match prices. Event status was
`pending`; no explicit open/suspended field appeared. The responses contain
map/round/kill markets but no player-named prop market in this sample. PrizePicks
does not appear among the 266 public bookmaker names, nor among the two account
selections; this is not proof of universal prop non-availability.

Real price payloads were immutably snapshotted before normalization. Normalized
records preserve original decimal odds, raw implied probabilities, simple
two-sided no-vig probabilities, `captured_at_utc`, market `updatedAt`, and raw
SHA-256. The snapshot writer never overwrites a filename.

## Fixture reconciliation and frozen forecasts

The initial 20 PandaScore fixtures all reconciled one-to-one with 91 pending
Odds-API.io events by exact normalized unordered pair and identical UTC start;
none was ambiguous. The three priced fixtures are part of that reconciled set.
Competition text is retained as audit context, not treated as an ID bridge.

Three forecasts were frozen before their scheduled starts from the bridged
state through 2026-07-25. `prospective_elo_forecasts.csv` records generation
time, both provider IDs, team IDs/ratings, probabilities, K=64 model version,
state-through date, market provenance, and cold-start flags. The Paper
Rex–Kiwoom DRX forecast uses an established bridge-state PandaScore identity
for Kiwoom DRX rather than a fabricated historical alias.

| Fixture | Elo P(home) | No-vig market P(home) | Disagreement (Elo − market) |
|---|---:|---:|---:|
| Paper Rex vs Kiwoom DRX | 0.7065 | 0.7322 | -0.0257 |
| BBL Esports vs Natus Vincere | 0.6688 | 0.6250 | +0.0438 |
| Team Heretics vs Karmine Corp | 0.6111 | 0.5774 | +0.0337 |

These are descriptive model-market disagreements only, not betting edges or
recommendations.

## Outputs and checks

`data/processed/milestone_5/` (ignored) contains the eligible/excluded bridge
tables, mapping audit, bridged ratings, normalized moneylines, and frozen
forecasts. `.env` remains ignored; no secret was printed, persisted, or placed
in source control.

`pytest -q`: **21 passed**. Tests cover no pre-cutoff double count,
incomplete-match exclusion, new-team non-merging/cold-start identity, daily
batching, no-vig conversion, schema validation, and immutable raw snapshots.

## Exact next experiment

Run a short, scheduled prospective collection window: snapshot the same
selected-bookmaker moneyline and corresponding PandaScore fixture at a fixed
pre-start lead time, freeze the current bridge-state Elo forecast once, then
record only eventual match outcomes for later calibration assessment. Keep the
scope to timestamp/provenance and forecasting evaluation; do not add bets,
staking, ROI, or Milestone 6.
