# Milestone 10: Prospective v2 shadow protocol

## Decision and safety boundary

The v2 shadow collector is implemented and tested but **not activated or
scheduled**. It is a separate one-shot process. It does not import, call, or
modify the v1 runner, and it never writes the v1 ledger or v1 market snapshot
paths.

The registered activation boundary is:

`2026-09-22T07:00:00Z` (2026-09-22 00:00 PDT)

A persistent invocation before that timestamp raises `ActivationError` before
network access or any local write. A dry-run may be performed earlier, but all
result snapshots, result events, and forecast events are redirected to a
temporary directory and removed at exit. Dry-run therefore cannot create a
prospective forecast.

The protected paths remain:

- v1 ledger: `data/processed/milestone_6/prospective_ledger.jsonl`;
- v1 raw market captures: `data/raw/market_pilot_live/`;
- frozen 30- and 100-match artifacts and reports; and
- the existing Windows v1 task.

The separate v2 paths, created only by a future persistent run, are:

- completed-result ledger:
  `data/processed/milestone_9/completed_match_ledger.jsonl`;
- immutable raw result polls: `data/raw/pandascore_completed_v2/v1/`;
- v2 forecast/lifecycle ledger:
  `data/processed/milestone_10/v2_shadow_ledger.jsonl`;
- v2 operational log:
  `data/processed/milestone_10/operational_log.jsonl`; and
- single-process lock:
  `data/processed/milestone_10/v2_shadow.lock`.

No betting, staking, Kelly sizing, or profitability logic exists in this
collector.

## One-shot collection sequence

Each cycle performs these steps in order:

1. Acquire the v2-only exclusive lock. A second process fails explicitly.
2. Hash and read the v1 ledger as an immutable source of already-prospective
   fixture and market provenance.
3. Fetch every page of the fixed-boundary PandaScore all-status result query.
   The total, page sequence, stable IDs, and row count must reconcile before
   anything from the poll is persisted. The scheduled-time query ends 15
   minutes after cycle start so a poll spanning UTC midnight can still prove
   coverage through its eventual generation time; future unfinished rows are
   retained but cannot update Elo.
4. Append the immutable raw poll and timestamped result versions. Repeated
   semantic results are deduplicated; changed provider results append a
   correction version.
5. Require the current poll to have no normalization failures and require its
   fixed range to cover the information available by forecast generation.
6. Select only an active v1 forecast created on or after activation with its
   exact active Bet365 primary record and linked market candidate.
7. Reconstruct the corrected v2 Elo state from the frozen historical/bridge
   base plus result versions observed by the generation timestamp, then append
   an immutable v2 forecast.
8. Re-hash the v1 ledger and abort if it changed during the cycle. Append a
   non-performance operational summary and release the lock.

Fetching and complete pagination precede result writes. Result ingestion
precedes every Elo reconstruction. If the newest complete poll reports a
normalization failure, no older successful poll can silently authorize a new
forecast in that run. A source gap or per-fixture validation error is recorded
by class and message, while another valid fixture may still progress.

PandaScore 429, 5xx, URL, timeout, and connection errors receive at most three
attempts with bounded exponential waits of one and two seconds. Authentication
and other non-retryable HTTP failures fail immediately. A failed fetch cannot
append a result poll or v2 forecast; persistent failed-ingestion attempts are
recorded in the separate operational log without credentials.

## Information-time and Elo rules

The Milestone 9 result schema keeps these timestamps distinct:

- `completed_at_utc`: provider-reported match completion; and
- `result_version_observed_at_utc` / `first_observed_result_at_utc`: local
  authenticated page receipt.

Completion time is never substituted for local observation time. A result or
provider correction observed after a forecast cannot enter that forecast's
as-of state. The query boundary remains fixed at
`2026-07-25T00:00:00Z`; actual match dates through 2026-07-25 are retained for
audit but excluded from Elo so the frozen bridge cannot be updated twice.

For a fixture on date D, the engine:

- uses eligible results through D-1 that were completed and observed by the
  generation timestamp;
- applies the preserved exact PandaScore-to-historical mapping before rating
  lookup and assigns unmapped provider teams only their stable `ps:<id>` key;
- uses initial rating 1500, scale 400, and K=64;
- preserves daily same-date batching; and
- records state cutoff/date, state-input digest, identities, ratings, prior
  eligible counts, result-poll ID/raw digest, and model version.

No fuzzy identity matching is performed. Market prices, market probabilities,
and outcomes are not passed to the Elo engine. The active primary market is an
experiment-eligibility gate and provenance link only.

## Forecast and fixture lifecycle

`v2_forecast_generated` is immutable and stores the exact v1 forecast, primary
selection, and market-candidate record IDs that made the future comparison
possible.

- A material same-calendar-date move appends `v2_schedule_updated`; the frozen
  probability remains valid because the D-1 cutoff is unchanged. The event is
  deferred until an exact active replacement primary exists and stores that
  primary/candidate provenance. A move that makes the original forecast no
  longer pre-start is terminal instead.
- A material cross-date move appends `v2_forecast_superseded`. A replacement is
  allowed only when v1 itself has an active prospective replacement, an active
  primary snapshot for the new schedule, complete result coverage, and the
  match has not started.
- A v1 cancellation, forfeit, unresolved terminal result, or other terminal
  exclusion appends `v2_terminal_exclusion` and removes the fixture from the
  active v2 set.
- A forecast is never created after its effective start or after an outcome is
  already present. Restarting a cycle cannot duplicate any lifecycle event.

## Preregistered future comparison

The checkpoint is fixed at the first **100 eligible completed fixtures after
activation**. There is no interim performance reporting. Operational status
may report collection counts and failures, but explicitly computes no accuracy,
Brier score, log loss, ROI, or other model/market performance statistic.

A comparison unit exists only when the same PandaScore match ID has all of:

1. one exact active post-activation v1 forecast;
2. one exact active Bet365 primary record and its exact candidate snapshot;
3. one exact active post-activation v2 forecast whose stored provenance links
   match those v1 record IDs;
4. one valid non-forfeit finished outcome; and
5. no terminal exclusion in either lifecycle.

No later fixture, market snapshot, or forecast may substitute for a missing
component. No retrospective v1 forecast may be fabricated. The 100 match IDs
in `artifacts/milestone_6_checkpoint_100_dataset.json` are explicitly excluded,
in addition to the post-activation timestamp checks. Eligible comparison rows
are ordered deterministically by scheduled start, v2 forecast ledger position,
and PandaScore ID. The first 100 in that order form the sole registered
checkpoint.

The three arms are the already-frozen v1 Elo probability, the corrected v2 Elo
probability, and the linked Bet365 no-vig probability, evaluated against the
same exact outcome and fixture orientation.

## Operational interface

Read-only status:

```bash
.venv/bin/python -m valorant_quant.milestone10_shadow --status
```

Live dry-run with zero persistent writes:

```bash
.venv/bin/python -m valorant_quant.milestone10_shadow --dry-run
```

The status surface contains only activation/protocol registration, successful
and failed run counts, result poll coverage, total/active/superseded/terminal v2
counts, fixture coverage-failure counts, and three-way eligible sample count.

The exact manual command for the first persistent one-shot activation, **not
executed in this milestone**, is:

```bash
cd /home/akshaj/Building/valorant-quant-research
.venv/bin/python -m valorant_quant.milestone10_shadow --run
```

The equivalent checked-in wrapper is:

```bash
/home/akshaj/Building/valorant-quant-research/scripts/run_milestone10_v2_once.sh
```

Before scheduling, run `--dry-run`, verify the advertised/received totals and
zero forecast failures, then execute one `--run` after the activation boundary
and inspect `--status`.

## Proposed Windows task (not created or enabled)

The proposed task is intentionally distinct from v1 and offset to run after
the existing quarter-hour v1 cycle:

| Field | Exact value |
| --- | --- |
| Task name | `Valorant Quant V2 Shadow` |
| Program/script | `C:\Windows\System32\wsl.exe` |
| Arguments | `-d Ubuntu -- bash -lc '/home/akshaj/Building/valorant-quant-research/scripts/run_milestone10_v2_once.sh'` |
| Start in | empty |
| Trigger | Daily at `00:07`, repeat every 15 minutes for 1 day |
| Overlap policy | Do not start a new instance |
| Retry | Every 5 minutes, at most 2 retries |
| Maximum run time | 10 minutes |

An administrator can create the basic trigger after explicit approval with the
following command. It is documented only and was not run:

```powershell
schtasks.exe /Create /TN "Valorant Quant V2 Shadow" /SC MINUTE /MO 15 /ST 00:07 /TR "C:\Windows\System32\wsl.exe -d Ubuntu -- bash -lc '/home/akshaj/Building/valorant-quant-research/scripts/run_milestone10_v2_once.sh'"
```

The no-overlap, retry, and maximum-duration settings must then be applied in
Task Scheduler's Settings tab before enabling the task. The collector's own
exclusive lock is a second overlap defense.

## Validation coverage

Focused Milestone 10 tests cover the normal results-first lifecycle, exact and
provider-only identities, fixture orientation, activation and no-retrospective
rules, absent v1/market components, source-gap blocking, one-fixture failure
isolation, restart/idempotency and duplicate prevention, material same- and
cross-date rescheduling, cancellation/terminal handling, dry-run zero-write
behavior, v1 immutability, bounded retry behavior, and exact three-way
eligibility/exclusion.

Milestone 8/9 regression tests additionally cover historical exact identity
continuity, true cold starts, two distinct historical identities, deterministic
Elo reconstruction, daily batching, consecutive-date state advancement,
late-arriving result availability, provider correction versioning, complete
pagination, bridge overlap, and restart-equivalent result materialization.

### Live zero-write dry-run

At `2026-09-21T17:51:56Z`, the real one-shot command completed an authenticated
dry-run against PandaScore:

| Check | Result |
| --- | ---: |
| Advertised/received source matches | 381 / 381 |
| Reconciled pages | 4 |
| Result versions that would be captured on first activation | 381 |
| Candidate post-activation v1+market fixtures | 0 |
| Forecast/source failures | 0 |
| Persistent result/v2/log writes | 0 |

The 260 otherwise active v1 forecast lifecycles were rejected because their
forecast timestamps precede the registered activation boundary; 18 additional
fixture lifecycles lacked one unambiguous active v1 forecast. The collector
would append only its protocol-registration event on an equivalent persistent
first run. This is the intended no-retrospective behavior, not a source gap.

## Remaining operational risks

- PandaScore pagination consistency proves completeness relative to the API's
  advertised result set, not every real-world match.
- Local first-observation history begins only with actual persistent activation;
  it cannot be backdated for the already-inspected period.
- Provider rows missing required timestamps or valid teams remain explicit
  exclusions and can block a run if normalization itself fails.
- A fixture without the exact active v1 forecast or Bet365 primary cannot enter
  the paired experiment; the collector will not repair that absence
  retrospectively.
- The full fixed-boundary poll is intentionally conservative and may become
  slower as the range grows. Pagination and the ten-minute task limit must be
  monitored before changing that design.

Subject to a successful final live dry-run and unchanged protected hashes, the
implementation is ready for a separately approved manual v2 shadow activation.
It is not yet collecting v2 forecasts.
