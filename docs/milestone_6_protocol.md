# Milestone 6: pre-registered prospective forecasting pilot

## Status: registered, collecting

The primary model is frozen as raw daily-batched Elo, K=64, scale 400, and
initial rating 1500. Identity and same-calendar-date batching rules are those
used in Milestone 5. No model tuning, new features, betting logic, or outcome
metric is permitted before 30 fully eligible completed fixtures exist.

## Historical boundary verification

The chronology lookup contains 11,790 exact-ID calendar-date rows and ends on
2024-08-25. The canonical modeling table consumes 11,101 rows after its
pre-existing outcome/team/placeholder/ID validity filters; it also ends on
2024-08-25 (source match ID 378829). Thus the historical Elo state consumed no
source match after 2024-08-25. The PandaScore bridge starts on 2024-08-26,
contains 6,401 eligible matches, and uses provider-prefixed IDs for non-exact
names. The two sources have adjacent, non-overlapping date domains: no eligible
match can be double-counted across their boundary. The 689 chronology rows not
in canonical are documented source candidates rejected by the existing
canonical filters, not a date gap to fill.

## Mechanical protocol

- Discover every PandaScore professional two-team future fixture.
- Reconcile only one-to-one normalized team-pair/time matches to Odds-API.io.
- Use Bet365 `ML` only for primary evaluation; GG.bet is observational only.
- Capture exactly one primary observation at T-60, accepting 45–75 minutes
  before scheduled start. If several are valid, select smallest absolute
  distance to 60 minutes, then earliest capture, then raw hash.
- Require both decimal prices >1 and preserve raw response before normalization.
- A schedule move greater than 60 minutes supersedes—not deletes—the old
  primary snapshot. Cancelled, forfeit, abandoned, or unresolved outcomes do
  not enter outcome metrics.
- Freeze the forecast before start from state through the preceding calendar
  date. A PandaScore-only identity is a true cold start only with zero prior
  eligible matches.
- Attach completion as a separate append-only ledger event; never rewrite a
  forecast or primary-market event.

## Existing observations are not primary pilot snapshots

The three Milestone 5 moneyline captures occurred 189.9, 609.9, and 789.9
minutes before their scheduled starts. They remain preserved research
observations but are outside the pre-registered 45–75 minute window and cannot
enter primary Milestone 6 evaluation.

## Decision rule after 30 completed eligible fixtures

Report Elo and Bet365 no-vig log loss, Brier, accuracy, calibration, paired
per-match differences, and the fixed <2pp / 2–5pp / 5–10pp / >10pp
disagreement diagnostics. A 30-match result cannot establish profitability,
market inefficiency, or production readiness. The decision concerns pipeline
reliability only: PIPELINE GO, LIMITED GO, or NO-GO.
