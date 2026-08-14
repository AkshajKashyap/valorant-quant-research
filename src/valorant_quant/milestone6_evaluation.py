"""Deterministic, read-only evaluation for the Milestone 6 prospective ledger.

The module never appends to or rewrites the prospective ledger.  It reconstructs
the sample from append-only events and writes only derived artifacts.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from valorant_quant.prospective import reconstruct_eligible_completed_matches, true_cold_start


ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data/processed/milestone_6/prospective_ledger.jsonl"
ARTIFACTS = ROOT / "artifacts"
REPORTS = ROOT / "reports"
BOOTSTRAP_SEED = 20260811
BOOTSTRAP_REPLICATES = 10_000
EPSILON = 1e-15
ACCURACY_TIE_RULE = "no_directional_prediction_excluded"
NEUTRAL_DIAGNOSTIC_PATH = REPORTS / "milestone_6_neutral_forecast_diagnostic.md"


class InsufficientEligibleMatchesError(ValueError):
    """Raised when a requested frozen checkpoint does not yet exist."""


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_ledger(path: Path = LEDGER) -> list[dict[str, Any]]:
    """Read ledger events without mutating the append-only source."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def neutral_forecast_reason(forecast: dict[str, Any]) -> str:
    """Classify an exact-neutral Elo forecast using only frozen pre-match state."""
    rating_a, rating_b = float(forecast["elo_a"]), float(forecast["elo_b"])
    if rating_a == rating_b == 1500.0:
        return "both teams at initial 1500"
    if rating_a == rating_b:
        return "equal non-initial ratings"
    return "some other deterministic reason"


def neutral_forecast_diagnostic(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct active exact-0.5 forecasts without reading result or market fields."""
    superseded = {
        str(record["forecast_record_id"])
        for record in records
        if record.get("record_type") == "forecast_superseded" and record.get("forecast_record_id") is not None
    }
    rows = []
    for position, forecast in enumerate(records):
        if forecast.get("record_type") != "forecast_generated" or str(forecast.get("record_id")) in superseded:
            continue
        try:
            if float(forecast["p_team_a_wins"]) != 0.5:
                continue
            prior_a, prior_b = int(forecast["team_a_prior_eligible_matches"]), int(forecast["team_b_prior_eligible_matches"])
            row = {
                "pandascore_match_id": str(forecast["pandascore_match_id"]),
                "team_a": forecast["team_a"], "team_b": forecast["team_b"],
                "scheduled_start_utc": forecast["scheduled_start_utc"],
                "elo_a": float(forecast["elo_a"]), "elo_b": float(forecast["elo_b"]),
                "team_a_prior_eligible_matches": prior_a, "team_b_prior_eligible_matches": prior_b,
                "team_a_identity": forecast["team_a_identity"], "team_b_identity": forecast["team_b_identity"],
                "team_a_true_cold_start": true_cold_start(prior_a),
                "team_b_true_cold_start": true_cold_start(prior_b),
                "neutral_reason": neutral_forecast_reason(forecast),
                "_ledger_position": position,
            }
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: (row["scheduled_start_utc"], row["_ledger_position"], row["pandascore_match_id"]))


def _neutral_diagnostic_markdown(rows: list[dict[str, Any]]) -> str:
    counts = {reason: sum(row["neutral_reason"] == reason for row in rows) for reason in (
        "both teams at initial 1500", "equal non-initial ratings", "some other deterministic reason",
    )}
    lines = [
        "# Milestone 6 neutral Elo forecast diagnostic", "",
        "This diagnostic contains only frozen pre-match state for active exact-0.5 Elo forecasts.", "",
        "## Summary", "",
        "| Reason | Forecasts |", "| --- | ---: |",
        *[f"| {reason} | {count} |" for reason, count in counts.items()], "",
        "## Forecasts", "",
        "| PandaScore match ID | Team A | Team B | Scheduled start UTC | Elo A | Elo B | A prior eligible matches | B prior eligible matches | Team A identity | Team B identity | A true cold start | B true cold start | Deterministic reason |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['pandascore_match_id']} | {row['team_a']} | {row['team_b']} | {row['scheduled_start_utc']} | {row['elo_a']:.1f} | {row['elo_b']:.1f} | {row['team_a_prior_eligible_matches']} | {row['team_b_prior_eligible_matches']} | {row['team_a_identity']} | {row['team_b_identity']} | {row['team_a_true_cold_start']} | {row['team_b_true_cold_start']} | {row['neutral_reason']} |"
        )
    return "\n".join(lines) + "\n"


def write_neutral_forecast_diagnostic(*, ledger_path: Path = LEDGER, report_path: Path = NEUTRAL_DIAGNOSTIC_PATH) -> tuple[Path, list[dict[str, Any]]]:
    """Write the read-only neutral-forecast diagnostic, never touching the ledger."""
    rows = neutral_forecast_diagnostic(load_ledger(ledger_path))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_neutral_diagnostic_markdown(rows), encoding="utf-8")
    return report_path, rows


def no_vig_probabilities(odds_a: float, odds_b: float) -> tuple[float, float]:
    """Return the registered two-sided decimal-odds normalization."""
    if odds_a <= 1 or odds_b <= 1:
        raise ValueError("decimal odds must both be greater than 1")
    q_a, q_b = 1 / odds_a, 1 / odds_b
    return q_a / (q_a + q_b), q_b / (q_a + q_b)


def log_loss(probability_a: float, team_a_won: bool) -> float:
    probability = probability_a if team_a_won else 1 - probability_a
    return -math.log(min(max(probability, EPSILON), 1 - EPSILON))


def brier_score(probability_a: float, team_a_won: bool) -> float:
    return (probability_a - float(team_a_won)) ** 2


def directional_prediction(probability_a: float) -> bool | None:
    """Tie probabilities have no directional prediction under the registered rule."""
    if probability_a == 0.5:
        return None
    return probability_a > 0.5


def accuracy(probabilities_a: Iterable[float], outcomes_a: Iterable[bool]) -> dict[str, Any]:
    correct = 0
    directional = 0
    ties = 0
    for probability, outcome in zip(probabilities_a, outcomes_a, strict=True):
        prediction = directional_prediction(probability)
        if prediction is None:
            ties += 1
            continue
        directional += 1
        correct += prediction == outcome
    return {
        "value": correct / directional if directional else None,
        "correct": correct,
        "directional_predictions": directional,
        "no_directional_ties": ties,
        "tie_rule": ACCURACY_TIE_RULE,
    }


def reconstruct_eligible_observations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct only active, complete, two-sided prospective observations.

    Ordering is scheduled start UTC, ledger position of the active forecast, then
    PandaScore ID.  It is independent of model probabilities, odds values,
    winners, and any performance result.
    """
    observations: list[dict[str, Any]] = []
    for lifecycle in reconstruct_eligible_completed_matches(records):
        match_id = lifecycle["pandascore_match_id"]
        forecast = lifecycle["forecast"]
        primary = lifecycle["primary"]
        candidate = lifecycle["candidate"]
        outcome = lifecycle["outcome"]
        try:
            odds_a, odds_b = float(candidate["team_a_decimal_odds"]), float(candidate["team_b_decimal_odds"])
            market_a, market_b = no_vig_probabilities(odds_a, odds_b)
            elo_a = float(forecast["p_team_a_wins"])
            if not 0 <= elo_a <= 1:
                raise ValueError("Elo probability outside [0, 1]")
            scheduled_start = str(primary["scheduled_start_utc"])
            captured_at = str(candidate["captured_at_utc"])
            lead_minutes = (parse_utc(scheduled_start) - parse_utc(captured_at)).total_seconds() / 60
        except (KeyError, TypeError, ValueError):
            continue
        observations.append({
            "pandascore_match_id": match_id,
            "scheduled_start_utc": scheduled_start,
            "team_a": forecast.get("team_a"),
            "team_b": forecast.get("team_b"),
            "elo_probability_team_a": elo_a,
            "bet365_team_a_decimal_odds": odds_a,
            "bet365_team_b_decimal_odds": odds_b,
            "bet365_no_vig_probability_team_a": market_a,
            "bet365_no_vig_probability_team_b": market_b,
            "team_a_won": outcome["team_a_won"],
            "model_market_probability_disagreement": elo_a - market_a,
            "forecast_record_id": forecast["record_id"],
            "primary_snapshot_record_id": primary["record_id"],
            "candidate_snapshot_id": candidate["record_id"],
            "capture_timestamp_utc": captured_at,
            "lead_time_minutes": lead_minutes,
            "_forecast_ledger_position": lifecycle["forecast_ledger_position"],
        })
    return observations


def freeze_checkpoint(observations: list[dict[str, Any]], checkpoint: int) -> list[dict[str, Any]]:
    if len(observations) < checkpoint:
        raise InsufficientEligibleMatchesError(
            f"checkpoint {checkpoint} requires {checkpoint} eligible completed matches; reconstructed {len(observations)}"
        )
    return [dict(row) for row in observations[:checkpoint]]


def paired_deltas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = []
    for row in rows:
        y, elo, market = row["team_a_won"], row["elo_probability_team_a"], row["bet365_no_vig_probability_team_a"]
        values.append({
            "pandascore_match_id": row["pandascore_match_id"],
            "scheduled_start_utc": row["scheduled_start_utc"],
            "log_loss_difference": log_loss(elo, y) - log_loss(market, y),
            "brier_difference": brier_score(elo, y) - brier_score(market, y),
        })
    return values


def bootstrap_mean_date_clustered(rows: list[dict[str, Any]], field: str, *, seed: int = BOOTSTRAP_SEED, replicates: int = BOOTSTRAP_REPLICATES) -> tuple[float, float]:
    """Return a deterministic percentile interval by resampling UTC-date clusters."""
    if not rows:
        raise ValueError("cannot bootstrap an empty sample")
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        clusters[row["scheduled_start_utc"][:10]].append(float(row[field]))
    dates = sorted(clusters)
    rng = random.Random(seed)
    draws = []
    for _ in range(replicates):
        selected = [clusters[rng.choice(dates)] for _ in dates]
        flattened = [value for cluster in selected for value in cluster]
        draws.append(mean(flattened))
    draws.sort()
    def percentile(p: float) -> float:
        position = (len(draws) - 1) * p
        lower, upper = math.floor(position), math.ceil(position)
        return draws[lower] if lower == upper else draws[lower] + (draws[upper] - draws[lower]) * (position - lower)
    return percentile(0.025), percentile(0.975)


def disagreement_bucket(disagreement: float) -> str:
    absolute = abs(disagreement)
    if absolute < 0.02:
        return "< 2 pp"
    if absolute < 0.05:
        return "2–5 pp"
    if absolute <= 0.10:
        return "5–10 pp"
    return "> 10 pp"


def _metric_summary(rows: list[dict[str, Any]], probability_field: str) -> dict[str, Any]:
    probabilities = [row[probability_field] for row in rows]
    outcomes = [row["team_a_won"] for row in rows]
    return {
        "log_loss": mean(log_loss(probability, outcome) for probability, outcome in zip(probabilities, outcomes, strict=True)),
        "brier": mean(brier_score(probability, outcome) for probability, outcome in zip(probabilities, outcomes, strict=True)),
        "accuracy": accuracy(probabilities, outcomes),
        "mean_probability_assigned_to_actual_winner": mean(
            probability if outcome else 1 - probability
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        ),
    }


def _bucket_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for bucket in ("< 2 pp", "2–5 pp", "5–10 pp", "> 10 pp"):
        subset = [row for row in rows if disagreement_bucket(row["model_market_probability_disagreement"]) == bucket]
        entry: dict[str, Any] = {"bucket": bucket, "matches": len(subset)}
        if subset:
            entry["elo"] = _metric_summary(subset, "elo_probability_team_a")
            entry["market"] = _metric_summary(subset, "bet365_no_vig_probability_team_a")
            favored_outcomes = [
                row["team_a_won"] if row["model_market_probability_disagreement"] > 0 else not row["team_a_won"]
                for row in subset if row["model_market_probability_disagreement"] != 0
            ]
            entry["actual_win_rate_for_side_elo_favors_relative_to_market"] = mean(favored_outcomes) if favored_outcomes else None
        result.append(entry)
    return result


def evaluate(rows: list[dict[str, Any]], *, bootstrap_seed: int = BOOTSTRAP_SEED, bootstrap_replicates: int = BOOTSTRAP_REPLICATES) -> dict[str, Any]:
    """Calculate descriptive scores only after a sample has been frozen."""
    if not rows:
        raise ValueError("cannot evaluate an empty sample")
    elo, market = _metric_summary(rows, "elo_probability_team_a"), _metric_summary(rows, "bet365_no_vig_probability_team_a")
    paired = paired_deltas(rows)
    def comparison(field: str) -> dict[str, Any]:
        values = [row[field] for row in paired]
        return {
            "mean": mean(values), "median": median(values),
            "elo_better": sum(value < 0 for value in values),
            "market_better": sum(value > 0 for value in values),
            "ties": sum(value == 0 for value in values),
            "bootstrap_95_interval": bootstrap_mean_date_clustered(paired, field, seed=bootstrap_seed, replicates=bootstrap_replicates),
        }
    return {
        "sample_size": len(rows),
        "elo": elo,
        "market": market,
        "mean_absolute_model_market_probability_disagreement": mean(abs(row["model_market_probability_disagreement"]) for row in rows),
        "paired_log_loss": comparison("log_loss_difference"),
        "paired_brier": comparison("brier_difference"),
        "calibration": {
            "elo_mean_prediction": mean(row["elo_probability_team_a"] for row in rows),
            "market_mean_prediction": mean(row["bet365_no_vig_probability_team_a"] for row in rows),
            "empirical_team_a_win_rate": mean(row["team_a_won"] for row in rows),
        },
        "disagreement_buckets": _bucket_summary(rows),
        "bootstrap": {"method": "UTC calendar-date clustered percentile bootstrap", "seed": bootstrap_seed, "replicates": bootstrap_replicates},
    }


def _format_number(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _report_markdown(checkpoint: int, exploratory: bool, rows: list[dict[str, Any]], result: dict[str, Any]) -> str:
    label = "EXPLORATORY EARLY LOOK" if exploratory else "PREREGISTERED 30-MATCH CHECKPOINT" if checkpoint == 30 else "DESCRIPTIVE CHECKPOINT"
    elo, market = result["elo"], result["market"]
    paired_ll, paired_bs = result["paired_log_loss"], result["paired_brier"]
    lines = [
        f"# Milestone 6 {label}: {checkpoint} eligible completed matches", "",
        f"> **Exploratory-status warning:** This {checkpoint}-match analysis was requested before the preregistered 30-match checkpoint, is exploratory only, and cannot justify model or protocol changes."
        if exploratory else "> This is the separate preregistered 30-match checkpoint." if checkpoint == 30 else "> This is a separate descriptive checkpoint using the same frozen evaluation procedure.", "",
        "## 1. Sample construction", "",
        "The sample is the first eligible completed fixtures ordered mechanically by scheduled-start UTC, active-forecast ledger position, and PandaScore match ID. No outcome, forecast, odds, or performance field is used to select fixtures.", "",
        "PandaScore IDs: " + ", ".join(row["pandascore_match_id"] for row in rows) + ".", "",
        "## 2. Data integrity checks", "",
        f"- {len(rows)} fixtures have exactly one active forecast, one active primary market selection, one valid finished binary outcome, valid two-sided Bet365 ML odds, and no terminal exclusion.",
        f"- All no-vig probability pairs sum to 1 within floating-point tolerance; primary lead times range from {_format_number(min(row['lead_time_minutes'] for row in rows), 2)} to {_format_number(max(row['lead_time_minutes'] for row in rows), 2)} minutes.",
        "- The ledger was read only; no prospective event was added, changed, or removed.", "",
        "## 3. Elo vs Bet365 headline metrics", "",
        "| Metric | Elo | Bet365 no-vig | Better |", "| --- | ---: | ---: | --- |",
        f"| Log loss | {_format_number(elo['log_loss'])} | {_format_number(market['log_loss'])} | {'Elo' if elo['log_loss'] < market['log_loss'] else 'Bet365' if market['log_loss'] < elo['log_loss'] else 'Tie'} |",
        f"| Brier | {_format_number(elo['brier'])} | {_format_number(market['brier'])} | {'Elo' if elo['brier'] < market['brier'] else 'Bet365' if market['brier'] < elo['brier'] else 'Tie'} |",
        f"| Accuracy | {_format_number(elo['accuracy']['value'])} | {_format_number(market['accuracy']['value'])} | {'Elo' if (elo['accuracy']['value'] or 0) > (market['accuracy']['value'] or 0) else 'Bet365' if (market['accuracy']['value'] or 0) > (elo['accuracy']['value'] or 0) else 'Tie'} |",
        "",
        f"Mean probability assigned to the actual winner: Elo {_format_number(elo['mean_probability_assigned_to_actual_winner'])}; Bet365 {_format_number(market['mean_probability_assigned_to_actual_winner'])}. Mean absolute Elo–market disagreement: {_format_number(result['mean_absolute_model_market_probability_disagreement'])}.",
        f"Accuracy treats an exact 0.5 as {ACCURACY_TIE_RULE.replace('_', ' ')} (Elo: {elo['accuracy']['no_directional_ties']}; Bet365: {market['accuracy']['no_directional_ties']}).", "",
        "## 4. Paired loss comparison", "",
        "Differences are Elo minus Bet365; negative favors Elo.", "",
        "| Difference | Mean | Median | Elo better | Market better | Ties |", "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| Log loss | {_format_number(paired_ll['mean'])} | {_format_number(paired_ll['median'])} | {paired_ll['elo_better']} | {paired_ll['market_better']} | {paired_ll['ties']} |",
        f"| Brier | {_format_number(paired_bs['mean'])} | {_format_number(paired_bs['median'])} | {paired_bs['elo_better']} | {paired_bs['market_better']} | {paired_bs['ties']} |", "",
        "## 5. Bootstrap uncertainty", "",
        f"A UTC calendar-date-clustered percentile bootstrap used fixed seed {result['bootstrap']['seed']} and {result['bootstrap']['replicates']:,} replicates. The 95% interval for mean Elo-minus-market log-loss is [{_format_number(paired_ll['bootstrap_95_interval'][0])}, {_format_number(paired_ll['bootstrap_95_interval'][1])}]; for Brier it is [{_format_number(paired_bs['bootstrap_95_interval'][0])}, {_format_number(paired_bs['bootstrap_95_interval'][1])}]. Uncertainty is very high at this sample size.", "",
        "## 6. Calibration", "",
        f"Across this small sample, mean team-A probability is Elo {_format_number(result['calibration']['elo_mean_prediction'])}, Bet365 {_format_number(result['calibration']['market_mean_prediction'])}, while the empirical team-A win rate is {_format_number(result['calibration']['empirical_team_a_win_rate'])}. No granular calibration bins or strong calibration conclusions are reported for n={len(rows)}.", "",
        "## 7. Pre-registered disagreement buckets", "",
        "These fixed diagnostic buckets are not betting strategies. Boundaries are <2 pp, [2,5) pp, [5,10] pp, and >10 pp.", "",
        "| Bucket | n | Elo LL | Market LL | Elo Brier | Market Brier | Elo accuracy | Market accuracy | Win rate: Elo-favored side |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for bucket in result["disagreement_buckets"]:
        if not bucket["matches"]:
            lines.append(f"| {bucket['bucket']} | 0 | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {bucket['bucket']} | {bucket['matches']} | {_format_number(bucket['elo']['log_loss'])} | {_format_number(bucket['market']['log_loss'])} | {_format_number(bucket['elo']['brier'])} | {_format_number(bucket['market']['brier'])} | {_format_number(bucket['elo']['accuracy']['value'])} | {_format_number(bucket['market']['accuracy']['value'])} | {_format_number(bucket['actual_win_rate_for_side_elo_favors_relative_to_market'])} |"
        )
    lines.extend([
        "", "## 8. Major limitations", "",
        f"This is a descriptive {len(rows)}-fixture comparison with very high uncertainty. It makes no statistical-significance, market-inefficiency, betting-edge, profitability, or production-readiness claim. No post-hoc subset is selected.", "",
        "## 9. Next checkpoint", "",
        "The preregistered 30-match checkpoint is complete. The collector continues unchanged. The next larger descriptive checkpoint will use 100 strictly eligible completed matches; no model or protocol changes will be made before that checkpoint."
        if checkpoint == 30 else "The collector continues unchanged. This report is descriptive and does not alter the model or protocol.", "",
    ])
    return "\n".join(lines)


def run_evaluation(*, checkpoint: int, exploratory: bool, ledger_path: Path = LEDGER) -> tuple[Path, Path, dict[str, Any]]:
    if checkpoint < 30 and not exploratory:
        raise ValueError("a checkpoint below 30 must be explicitly marked --exploratory; it cannot be preregistered or confirmatory")
    observations = reconstruct_eligible_observations(load_ledger(ledger_path))
    sample = freeze_checkpoint(observations, checkpoint)
    result = evaluate(sample)
    for row in sample:
        row.pop("_forecast_ledger_position", None)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    suffix = f"early_look_{checkpoint}" if exploratory else f"checkpoint_{checkpoint}"
    dataset_path = ARTIFACTS / f"milestone_6_{suffix}_dataset.json"
    report_path = REPORTS / f"milestone_6_{suffix}.md"
    dataset_path.write_text(json.dumps({"checkpoint": checkpoint, "exploratory": exploratory, "rows": sample}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(_report_markdown(checkpoint, exploratory, sample, result), encoding="utf-8")
    return dataset_path, report_path, result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a frozen Milestone 6 prospective checkpoint.")
    parser.add_argument("--checkpoint", type=int)
    parser.add_argument("--exploratory", action="store_true")
    parser.add_argument("--neutral-forecast-diagnostic", action="store_true")
    args = parser.parse_args()
    if args.neutral_forecast_diagnostic:
        if args.checkpoint is not None or args.exploratory:
            parser.error("--neutral-forecast-diagnostic cannot be combined with checkpoint evaluation arguments")
        report, rows = write_neutral_forecast_diagnostic()
        print(json.dumps({"report": str(report), "neutral_forecasts": len(rows)}, sort_keys=True))
        return
    if args.checkpoint is None:
        parser.error("--checkpoint is required unless --neutral-forecast-diagnostic is used")
    try:
        dataset, report, _ = run_evaluation(checkpoint=args.checkpoint, exploratory=args.exploratory)
    except (InsufficientEligibleMatchesError, ValueError, FileNotFoundError) as error:
        parser.error(str(error))
    print(json.dumps({"dataset": str(dataset), "report": str(report), "checkpoint": args.checkpoint, "exploratory": args.exploratory}, sort_keys=True))


if __name__ == "__main__":
    main()
