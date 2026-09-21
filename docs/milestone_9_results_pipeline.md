# Milestone 9: Timestamped Completed-Match Ingestion

## Decision

**READY as a data-source component for a separately approved v2 shadow collector; not activated.**

The source, pagination, append-only versioning, bridge boundary, as-of reconstruction, and corrected-Elo integration are implemented and tested. No v2 forecast is being collected, no scheduler was added, and no v1 component was changed. A future operational milestone must register and activate a separate v2 results/forecast process.

## Source investigation

The existing repository used two PandaScore patterns:

- `/valorant/matches/upcoming` for fixture discovery;
- `/matches/{id}` for targeted v1 outcome checks; and
- `/valorant/matches/past` for the one-time Milestone 5 bridge.

The one-time bridge successfully paginated `/past`, but a live Milestone 9 cross-check showed why `/past` is unsafe as the sole ongoing audit source. It returned 366 currently finished matches in the new period, but omitted known v1 match `1661586`. A direct stable-ID lookup returned that match as `canceled` with no begin, end, or winner, even though v1 had previously observed it as finished. Thus the provider had corrected its status and the `/past` collection silently made the record disappear.

The pipeline therefore uses the all-status endpoint:

`GET https://api.pandascore.co/valorant/matches`

with:

- a fixed `range[scheduled_at]`;
- ascending `scheduled_at` ordering;
- `per_page=100`; and
- every page calculated from `X-Total`.

This retains finished matches plus later cancellations/status corrections. PandaScore did not return `X-Total-Pages` during the live check, so page count is calculated as `ceil(X-Total / per_page)`. Every page must repeat the same total; received rows must equal the total; page numbers must be sequential when reported; and stable IDs must be unique. Any mismatch aborts before persistence.

HTTP 200 is not treated as evidence of completeness.

## Coverage boundary

The frozen Milestone 5 bridge contains eligible matches through actual begin date **2026-07-25**. The new query begins at scheduled time **2026-07-25T00:00:00Z**, creating an intentional one-day overlap. Normalization uses actual `begin_at` for the Elo batching date and makes every match with `match_date <= 2026-07-25` ineligible with reason `pre_or_at_bridge_boundary`.

This provides two protections:

1. boundary schedule changes and cancellations remain visible in the new raw audit source; and
2. no overlap record can produce a second Elo update already represented in the frozen bridge.

Every poll re-queries the fixed boundary rather than trusting a moving high-water mark. This is less request-efficient but detects delayed results, changed winners/scores/timestamps, and provider status corrections. A future optimization may not replace this property without its own completeness proof.

For a v2 forecast, a successful poll must:

- start at the registered fixed boundary;
- complete no later than forecast generation;
- cover scheduled time through at least the start of fixture date D; and
- have zero normalization failures.

Otherwise `SourceGapError` blocks forecasting.

## Timestamp semantics

The source supplies `end_at`, which is stored as `completed_at_utc` when present. It does **not** supply the research system's availability time.

Each page therefore receives a local authenticated `received_at_utc`. The first valid normalized result version uses that page receipt as `first_observed_result_at_utc`. It is never replaced by `end_at`, `modified_at`, scheduled time, or an inferred timestamp.

Each result version also has `result_version_observed_at_utc`. The corrected Elo adapter exposes that value as `result_available_at_utc`. Therefore:

- a match that ended earlier but was first captured after forecast time is absent from that forecast's as-of state;
- a provider correction captured later cannot rewrite a prior forecast's information set; and
- a future forecast sees the latest result version actually observed by its generation time.

`first_observed_result_at_utc` remains unchanged across corrections once a valid result has been observed. If the first source version is incomplete/ineligible and a valid result appears later, the first-result timestamp is the later valid observation, not the incomplete page receipt.

## Raw and normalized storage

`completed_matches.py` defines two separate v2-only stores for eventual activation:

- immutable raw poll bundles under `data/raw/pandascore_completed_v2/v1/`;
- an append-only normalized ledger at `data/processed/milestone_9/completed_match_ledger.jsonl`.

These paths are not used by v1 and were not populated by the read-only feasibility check.

A raw bundle contains:

- endpoint and non-secret query;
- poll start/completion times;
- advertised total and calculated page count;
- every raw response page;
- per-page receipt timestamps; and
- observed pagination headers.

The canonical bundle bytes are SHA-256 addressed and written with exclusive-create semantics. Existing files are never overwritten.

Normalized result versions contain stable match ID, scheduled/start/completion times, canonically ordered team IDs/names, scores, winner, status, forfeit flag, tournament, provider modification time, eligibility decision, raw snapshot/page provenance, version observation time, and first-valid-result observation time.

Team ordering is canonical by stable PandaScore team ID. This prevents a provider opponent-array reorder from creating a false result correction while preserving the winner orientation.

## Deterministic deduplication and provider corrections

A semantic result signature covers match date, schedule/start/completion, teams, scores, winner, status, forfeit, eligibility, exclusion reason, and tournament.

- If the latest signature is identical, no result event is appended.
- If no prior version exists, `v2_result_observed` is appended.
- If the semantic result changes, `v2_result_corrected` is appended with `supersedes_record_id`.
- If a provider later returns to an older value, a new timestamped version is still appended; old bytes are not reused as current state.

Each successful complete poll appends `v2_results_poll_completed`, even if every match result is unchanged. This proves which fixed range and raw snapshot were completely checked. Replaying the exact same poll bundle after restart is byte-idempotent.

Malformed matches with stable IDs are retained as ineligible audit records when possible. A missing stable ID, failed page request, changing total, incomplete page sequence, duplicate ID, raw-write failure, or ledger-ID collision fails closed. Page/network failures occur before any raw bundle or poll event is written.

## Eligibility and identity

An Elo-eligible result requires:

- `status=finished`;
- non-forfeit;
- exactly two distinct non-placeholder teams;
- valid scheduled, actual-begin, and completion timestamps;
- actual begin date after 2026-07-25;
- completion no later than local observation; and
- winner ID among the two teams.

The ingestion layer retains PandaScore IDs. `forecast_from_result_ledger()` materializes one latest version per ID as of the forecast-generation timestamp, proves poll coverage, and passes the frame to the Milestone 8 `CorrectedEloEngine`. That engine applies the preserved exact identity mapping before rating/count lookup. No fuzzy mapping is introduced.

Same-day batching remains in the unchanged `run_elo()` path. Results with `match_date == D` cannot enter a forecast for D, even if an earlier same-day result has already been observed.

## Live read-only feasibility check

At **2026-09-21T05:20:41Z**, an authenticated in-memory query covered scheduled time from 2026-07-25T00:00:00Z through 2026-09-21T05:20:36Z.

| Check | Result |
| --- | ---: |
| Advertised/received matches | 381 / 381 |
| Pages read and reconciled | 4 |
| Stable unique IDs | 381 |
| Scheduled timestamps | 381 |
| Two distinct teams | 381 |
| Finished / canceled statuses | 379 / 2 |
| Winners among teams | 379 |
| Completion timestamps | 373 |
| Elo-eligible after boundary/rules | 363 |
| Boundary-overlap exclusions | 10 |
| Missing/invalid timestamp exclusions | 6 |
| Non-finished exclusions | 2 |
| Known v1 outcome IDs checked/missing | 259 / 0 |

The earliest returned actual begin was 2026-07-25T03:28:28Z; overlap rows are retained but cannot enter Elo. The latest completion was 2026-09-20T12:03:07Z.

The live source is sufficient for this pipeline because the all-status query exposes corrections/cancellations, pagination reconciles, stable IDs and fixture identities are complete in the observed range, every known v1 outcome ID is present, and unusable records are explicitly excluded rather than silently dropped.

Remaining limitations:

- Completeness is proven against PandaScore's advertised total, page consistency, and known local outcomes; the API cannot prove that PandaScore itself knows every real-world match.
- Six observed rows lacked timestamps required by the registered Elo eligibility rules and therefore cannot update state.
- Local page receipt is the only valid first-observed time; it begins when this separate pipeline is actually activated and cannot be backdated for the already-inspected period.
- Repeated fixed-boundary polling is required to observe later provider corrections.

## Tests

Focused tests cover:

- incremental polling and append-only normalization;
- total-derived multi-page retrieval;
- changing/incomplete pagination failure;
- repeated identical responses;
- delayed result availability distinct from completion;
- provider corrections and as-of version selection;
- missing coverage blocking forecasts;
- bridge-overlap exclusion;
- same-day exclusion and consecutive-date Elo advancement;
- process-restart idempotence;
- network/page failure before writes; and
- isolation of one invalid match without discarding valid peers.

The existing Milestone 8 tests continue to cover exact identity mapping, same-day order-invariant Elo batching, deterministic state reconstruction, and restart-equivalent forecasts.

## Activation boundary

No Windows task, cron job, service, v2 forecast ledger, or live v2 forecast was activated. Before a shadow collector begins, a separate operational change must:

1. register its activation timestamp and cadence;
2. create the v2-only raw and normalized paths;
3. complete and persist the first full-boundary poll;
4. verify source coverage/freshness immediately before each forecast cycle;
5. write forecasts to a separate v2 append-only ledger; and
6. leave the v1 collector and all frozen evaluations untouched.

The 100 inspected v1 outcomes remain ineligible for untouched v2 validation.
