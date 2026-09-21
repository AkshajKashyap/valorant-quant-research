# Milestone 8: Corrected Elo Forecasting System

## Status and scope

The v2 engine is implemented and tested but **not activated**. It has no network access, scheduler integration, ledger writer, or live-collection entry point. The running v1 collector, Windows scheduling, v1 ledger, raw market snapshots, historical forecasts, and frozen 30/100-match evaluations remain unchanged.

V2 changes only the two failure modes confirmed by Milestone 7: identity lookup and temporal state. It retains raw Elo with K=64, scale 400, initial rating 1500, and simultaneous calendar-date updates. It adds no feature, market input, fitted parameter, ML model, betting rule, or profitability objective.

## Precise root causes

### Identity failure

The authoritative bridge crosswalk is `data/processed/milestone_5/pandascore_team_mapping.csv`. `apply_historical_identity()` correctly used its exact normalized-name matches when producing `pandascore_eligible_bridge_matches.csv`. Consequently, a mapped team's bridge matches and final rating were keyed by its historical team ID.

Both forecast paths in `milestone6_runner.py`, however, independently constructed `ai` and `bi` as `ps:<PandaScore ID>`. They never read or applied the mapping table. A lookup such as `ratings.get("ps:128541", 1500)` therefore missed Team Liquid's rating stored under historical ID `474` and fell back to 1500/zero matches. This was a key-space mismatch, not a failure to discover the mapping.

V2 has one `ExactIdentityResolver`. It reads only unambiguous rows whose method is `exact_normalized_name`. A mapped provider ID resolves to the preserved historical ID; an unmapped provider ID resolves to `ps:<provider-id>`. Non-exact or ambiguous mapped rows are rejected. There is no fuzzy matching.

### Frozen temporal state

`milestone6_runner.reconstruct_d1_state()` loaded two local CSV files: the historical corpus and the single Milestone 5 bridge snapshot. The bridge snapshot was captured on 2026-07-26 and its last eligible match date is 2026-07-25. The function filtered those static rows to dates before the future fixture but never refreshed the bridge, appended later completed matches, or reconstructed results from another complete time-stamped source. The v1 outcome ledger was also not part of its input. A later fixture-date filter cannot advance beyond the maximum row already in the file, so all 100 forecasts remained at 2026-07-25.

V2 separates the immutable base from incremental completed results:

1. The immutable historical plus Milestone 5 bridge tables provide the base through 2026-07-25.
2. Before eventual live activation, a separate v2 result collector must preserve a complete PandaScore finished-match response in an append-only raw/result store. It must not reuse the market collector as a completeness source.
3. Each normalized completed result records `result_available_at_utc`, the first immutable capture time at which its valid outcome was actually available to v2.
4. For a forecast generated at time T for a fixture on UTC calendar date D, the engine includes a dynamic result only when it is eligible, finished, non-forfeit, has `match_date < D`, and has both `completed_at_utc <= T` and `result_available_at_utc <= T`.
5. Thus D-1 is the maximum permissible calendar date, while T is the information-availability boundary. A late-posted D-1 result is excluded if it was not available at forecast generation.

No live result collector is implemented or activated in this milestone. Activation is blocked until that complete, immutable source exists and its completeness/freshness checks pass.

## Corrected state construction

`valorant_quant.corrected_elo.CorrectedEloEngine` accepts:

- an immutable canonical base table;
- a versioned `ExactIdentityResolver`; and
- an optional table of newly completed, timestamped results.

For each fixture it:

1. verifies generation precedes scheduled start;
2. sets the allowed calendar cutoff to D-1;
3. filters the base to that cutoff;
4. filters incremental results by eligibility, D-1, and actual result availability at generation time;
5. resolves both result and fixture provider IDs before rating lookup;
6. rejects duplicate match IDs or a fixture whose two providers resolve to one identity;
7. runs the existing `run_elo()` with the unchanged K=64 configuration;
8. retrieves ratings and actual prior eligible match counts by resolved identity; and
9. freezes the probability plus state provenance into a v2 record.

`run_elo()` groups by `match_date`, snapshots ratings at the start of each date, accumulates all daily changes, and applies them only after every match on that date. V2 reuses this path, preserving same-day batching. Input order cannot change the state.

The state records both:

- `state_cutoff_date`: the permitted D-1 boundary; and
- `state_through_date`: the latest match date actually included, which may be earlier when no eligible available result exists on D-1.

`state_as_of_utc` gives the availability boundary, and `state_input_sha256` fingerprints the complete ordered state input for restart reproduction.

## Incremental result contract

The inactive engine expects these columns from a future complete v2 result source:

| Field | Meaning |
| --- | --- |
| `pandascore_match_id` | Stable source fixture ID; unique in the supplied state snapshot |
| `match_date` | UTC calendar date used for batching |
| `team_a_provider_id`, `team_b_provider_id` | Ordered source team IDs |
| `team_a_name`, `team_b_name` | Ordered source team names |
| `team_a_won` | Boolean outcome in the same orientation |
| `tournament_name` | Audit context only |
| `source_snapshot_id` | Immutable source provenance |
| `completion_status` | Must be `finished` for an eligible row |
| `forfeit` | Must be false for an eligible row |
| `eligible` | Mechanical inclusion flag |
| `completed_at_utc` | Source completion timestamp; must not be after availability or forecast generation |
| `result_available_at_utc` | First captured time the valid result was available to v2 |

The engine does not accept a source request time retroactively invented from match metadata. The availability timestamp must come from an immutable capture. Ineligible records may be retained by a future collector for audit, but they never enter state.

## V2 forecast record schema

Every record uses `record_type=v2_forecast_generated` and `model_version=raw_elo_daily_batched_k64_identity_temporal_v2`.

Required forecast and fixture fields:

- `record_id`
- `record_type`
- `model_version`
- `generated_at_utc`
- `fixture_id`
- `scheduled_start_utc`
- `team_a_provider_id`, `team_b_provider_id`
- `team_a_name`, `team_b_name`
- `team_a_identity`, `team_b_identity`
- `elo_a`, `elo_b`
- `team_a_prior_eligible_matches`, `team_b_prior_eligible_matches`
- `p_team_a_wins`, `p_team_b_wins`

Required state/version provenance:

- `state_cutoff_date`
- `state_through_date`
- `state_as_of_utc`
- `state_input_sha256`
- `state_eligible_match_count`
- `identity_mapping_version`
- `k`
- `scale`
- `initial_rating`

The validator requires complementary probabilities, generation before start, a D-1 cutoff, no state date after D-1, distinct resolved identities, nonnegative counts, and the frozen K=64/scale-400/initial-1500 parameters.

V2 records must eventually use their own append-only ledger namespace. They must never be written as `forecast_generated` v1 events or overwrite an existing record. A schedule move would append a v2 supersession event under a separately registered lifecycle; that collector behavior is intentionally not implemented yet.

## Validation coverage

Focused regression tests demonstrate:

- exact historical mapping is consumed for rating and count lookup;
- provider-only IDs preserve their bridge continuity;
- genuinely new provider IDs remain 1500/zero-count cold starts;
- ambiguous or non-exact mappings are rejected rather than fuzzily merged;
- two providers resolve to different historical identities;
- team A/B orientation is preserved and a reversed fixture produces the complementary probability;
- a D2 forecast changes after an eligible D1 result becomes available;
- a same-date result cannot affect a same-day forecast, even if already observed;
- a prior-date result captured after generation cannot leak into state;
- same-date input order cannot change ratings or the state digest;
- equivalent engine reconstruction after process restart emits the identical record; and
- schema, timing, parameter, and probability invariants fail closed.

## Future shadow deployment design

The first 100 v1 outcomes were inspected during Milestones 6 and 7. They may be used for debugging and diagnostic replay only; they cannot be presented as untouched validation of v2.

Before activation:

1. Register the exact v2 code revision, mapping-table hash, result-source schema, eligibility rules, UTC batching rule, market window, reschedule lifecycle, metrics, uncertainty method, and target sample size.
2. Establish a prospective activation timestamp after registration. Reject every fixture scheduled or forecast before that boundary from the v2 evaluation sample.
3. Run a complete result-source freshness/completeness rehearsal without emitting live v2 forecasts.
4. Create a separate append-only v2 forecast ledger and immutable result snapshot area. Do not modify the v1 ledger or raw snapshot directories.

For each newly eligible future fixture after activation, freeze three aligned probabilities before start:

- the unchanged v1 model forecast;
- the registered v2 corrected-Elo forecast; and
- the same Bet365 no-vig primary selected under one common registered market rule.

The comparison table must be an inner join on the same fixture ID, active schedule, primary snapshot, and valid outcome. No model may receive a different fixture subset. Report v2 versus v1 and v2 versus market with paired log-loss and Brier differences, date-clustered uncertainty, directional accuracy under the registered tie rule, cold-start counts, and state/identity integrity checks. Do not tune from interim outcomes, replace forecasts, select favorable disagreement subsets, or inspect profitability.

The current v1 collector may continue unchanged during development. Future v2 shadow activation requires a separate explicit milestone and operational approval; this implementation does not activate it.

## Reproduction and safety boundary

The v2 engine is pure with respect to repository state: it reads supplied frames and returns state/forecast values. `build_default_engine()` only loads preserved local inputs. No function in `corrected_elo.py` writes a ledger, edits a forecast, calls an API, changes Windows scheduling, reads market odds, or produces a wager.
