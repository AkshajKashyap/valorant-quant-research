# Milestone 5: prospective Valorant market-data feasibility

## Decision: NO-GO in the current environment

No configured or authorized API account exists in this workspace, so no live
Valorant fixtures, prices, bookmakers, player props, market timestamps, event
reconciliation, prospective Elo predictions, or real market snapshots could be
verified. The project must not claim a prospective collection pilot until an
authorized account returns actual live responses.

## Sources actually tested

| Source | Actual endpoint check | Result | What can be concluded |
|---|---|---|---|
| SportsGameOdds | `GET /v2/events?oddsAvailable=true`, no key | HTTP 401: `Missing API key` | No Valorant coverage, competition, bookmaker, moneyline, two-sided price, PrizePicks, timestamp, free-tier entitlement, or historical-access claim can be verified from this workspace. |
| PandaScore | `GET /valorant/matches/upcoming`, no token | HTTP 403: `Token is missing` | No live fixture, ID, opponent, scheduled UTC time, tournament, or format sample can be verified. |

The official SportsGameOdds site advertises a free tier, but its advertised
league/bookmaker set is plan-dependent; an unauthenticated endpoint cannot
establish that Valorant is currently included. Its documentation also describes
historical odds as a paid capability. PandaScore documents upcoming Valorant
fixtures as an all-plan endpoint and documents `scheduled_at` and `best_of`,
but an account token is still required to verify actual current coverage.

No PrizePicks Valorant player props were observed because no SportsGameOdds
response was available. No competitions or bookmakers were observed for the
same reason.

## Snapshot contract implemented

`src/valorant_quant/market_data.py` provides a deliberately small,
provider-neutral contract for an authorized future pilot:

- immutable canonical raw JSON named by capture UTC timestamp and SHA-256;
- no overwrite of an existing snapshot;
- normalized records requiring `captured_at_utc`, source/event IDs, optional
  canonical match ID when reconciled, scheduled start, both teams, bookmaker,
  market type, both decimal prices, source update timestamp, and raw hash;
- raw implied probabilities and explicitly normalized two-way no-vig
  probabilities, while preserving original decimal prices.

The schema is suitable for a future moneyline record such as:

`captured_at_utc`, `source`, `source_event_id`, `canonical_match_id`,
`scheduled_start_utc`, `team_a`, `team_b`, `bookmaker`, `market_type`,
`team_a_decimal_odds`, `team_b_decimal_odds`, `source_last_update_utc`, and
`raw_snapshot_hash`.

No sample real snapshots or frozen prospective Elo predictions exist. Creating
placeholder records would violate the project’s timestamp/provenance rules.

## Integrity and checks

`.env` is already ignored. API keys must remain in environment variables and
must never be placed in raw snapshots, normalized outputs, code, or Git.

The test suite passes with checks for two-way no-vig conversion, schema
validation, SHA-256 raw provenance, and immutable snapshot writes.

## Exact next step

Create or provide authorized free-tier credentials for both APIs as environment
variables (for example `SPORTS_GAME_ODDS_API_KEY` and `PANDASCORE_TOKEN`), then
run a single documented discovery call for each. Proceed to a small live pilot
only if SportsGameOdds returns timestamped two-sided Valorant match-winner
prices before start and PandaScore returns reconcilable upcoming fixtures. No
bets, ROI calculation, or price backfill should be added.
