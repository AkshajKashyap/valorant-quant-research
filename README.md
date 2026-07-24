# Valorant Quant Research

Research project for building leakage-free probabilistic models of professional Valorant outcomes.

Initial objective:

> Determine how accurately professional Valorant match outcomes can be predicted using only information available before each match.

Longer-term research direction:

- probabilistic match forecasting
- player-performance forecasting
- calibration and uncertainty
- comparison against betting-market probabilities
- prospective paper betting
- testing whether any persistent market inefficiency exists

The project is research-first. Profitability is not assumed.

## Milestone 1: source audit

Milestone 1 inspects a frozen Kaggle snapshot and creates an audit-only
match-level table. It deliberately does not construct features or models.

```bash
.venv/bin/python -m valorant_quant.milestone1 \
  --raw-files data/raw/kaggle_ryanluong1_valorant_champion_tour/v47/files \
  --output-dir data/processed/milestone_1_v47
```

The generated `match_level_audit.csv` is not training-ready: it explicitly
marks every row ineligible for chronology unless a source timestamp exists.
See [`docs/milestone_1_report.md`](docs/milestone_1_report.md).

Milestone 1.5 found a Tier-B calendar-date mapping for a restricted 2021–2024
subset. The conservative same-day rule and source decision are documented in
[`docs/milestone_1_5_report.md`](docs/milestone_1_5_report.md).

Milestone 2 implements a date-batched Elo baseline only. Its frozen-test
result, constraints, and outputs are in
[`docs/milestone_2_report.md`](docs/milestone_2_report.md).

Milestone 2.5 stress-tested the Elo result and found weak, unstable 2024
evidence. See [`docs/milestone_2_5_report.md`](docs/milestone_2_5_report.md).
