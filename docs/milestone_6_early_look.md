# Milestone 6 exploratory early look — pre-analysis record

**25-fixture freeze timestamp:** 2026-08-11T05:57:56Z

- **Original preregistered checkpoint:** 30 eligible completed matches; it
  remains unchanged.
- **Initial early-look plan:** inspect at 26 operationally reported eligible
  fixtures.
- **Reconciliation:** deterministic reconstruction found only 25 strictly
  eligible fixtures. Fixture `1607789` is excluded because its only primary
  market selection was superseded (twice), leaving no active primary snapshot.
- **Authorized early look:** before any forecasting or market performance
  metrics were calculated, the operator elected to inspect the first 25
  strictly eligible completed fixtures.
- **Reason and decision basis:** the operator elected to begin analysis before
  the preregistered 30-match checkpoint; that decision was not based on
  observed forecasting or betting performance.
- **Status:** this is a protocol deviation and an explicitly exploratory
  analysis. No model or protocol changes are permitted based solely on it.
- **Operations:** the collector and scheduling continue unchanged.

## Frozen 25-fixture sample

The sample is mechanically ordered by scheduled-start UTC, active-forecast
ledger position, then PandaScore match ID. This ordering does not use
forecasts, odds, outcomes, or performance results.

`1607774`, `1607779`, `1608111`, `1608112`, `1561528`, `1561529`, `1607776`,
`1561489`, `1607778`, `1561490`, `1608113`, `1561530`, `1561531`, `1607788`,
`1561491`, `1608128`, `1561532`, `1561533`, `1607786`, `1561493`, `1561494`,
`1607787`, `1608137`, `1608138`, `1561534`.

The collector, forecasts, market snapshots, Elo model, K, bookmaker, capture
window, ledger records, stored snapshots, protocol, and schedule have not been
modified by this declaration.
