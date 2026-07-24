"""Robustness analysis for the fixed, leakage-safe Elo implementation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from valorant_quant.elo import EloConfig, run_elo


K_GRID = (8, 16, 24, 32, 48, 64, 96, 128)
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20260724


def per_match_losses(predictions: pd.DataFrame) -> pd.DataFrame:
    result = predictions.copy()
    probability = result["probability_a"].clip(1e-15, 1 - 1e-15)
    outcome = result["team_a_won"].astype(float)
    result["elo_log_loss"] = -(outcome * np.log(probability) + (1 - outcome) * np.log(1 - probability))
    result["baseline_log_loss"] = np.log(2.0)
    result["delta_log_loss"] = result["elo_log_loss"] - result["baseline_log_loss"]
    result["elo_brier"] = (probability - outcome) ** 2
    result["baseline_brier"] = 0.25
    result["delta_brier"] = result["elo_brier"] - result["baseline_brier"]
    result["accuracy"] = (probability.ge(0.5) == result["team_a_won"].astype(bool)).astype(float)
    result["confidence"] = np.maximum(probability, 1 - probability)
    return result


def metrics(losses: pd.DataFrame) -> dict[str, float | int]:
    if losses.empty:
        return {"n_matches": 0}
    return {
        "n_matches": int(len(losses)),
        "elo_log_loss": float(losses.elo_log_loss.mean()),
        "fifty_fifty_log_loss": float(losses.baseline_log_loss.mean()),
        "delta_log_loss": float(losses.delta_log_loss.mean()),
        "elo_brier": float(losses.elo_brier.mean()),
        "fifty_fifty_brier": float(losses.baseline_brier.mean()),
        "delta_brier": float(losses.delta_brier.mean()),
        "accuracy": float(losses.accuracy.mean()),
        "average_confidence": float(losses.confidence.mean()),
    }


def bootstrap_resample_dates(losses: pd.DataFrame, seed: int, replicates: int) -> list[pd.DataFrame]:
    """Sample whole date clusters; never split same-date matches across a draw."""
    grouped = [group for _, group in losses.groupby("match_date", sort=True)]
    generator = np.random.default_rng(seed)
    return [pd.concat([grouped[index] for index in generator.integers(0, len(grouped), len(grouped))]) for _ in range(replicates)]


def date_bootstrap(losses: pd.DataFrame, seed: int = BOOTSTRAP_SEED, replicates: int = BOOTSTRAP_REPLICATES) -> dict[str, Any]:
    # Aggregate first: draws remain whole date clusters without materializing
    # 10,000 copies of the match-level frame.
    daily = losses.groupby("match_date", sort=True).agg(
        n_matches=("match_id", "size"), log_sum=("delta_log_loss", "sum"), brier_sum=("delta_brier", "sum")
    )
    generator = np.random.default_rng(seed)
    draws = generator.integers(0, len(daily), size=(replicates, len(daily)))
    counts = daily.n_matches.to_numpy()[draws]
    log_deltas = daily.log_sum.to_numpy()[draws].sum(axis=1) / counts.sum(axis=1)
    brier_deltas = daily.brier_sum.to_numpy()[draws].sum(axis=1) / counts.sum(axis=1)
    def summary(values: np.ndarray, observed: float) -> dict[str, float]:
        lower, upper = np.quantile(values, [0.025, 0.975])
        return {
            "observed_mean_delta": observed,
            "bootstrap_mean_delta": float(values.mean()),
            "interval_95_lower": float(lower),
            "interval_95_upper": float(upper),
            "fraction_delta_below_zero": float((values < 0).mean()),
        }
    return {
        "method": "calendar-date cluster bootstrap; each sampled date includes all of its matches",
        "seed": seed,
        "replicates": replicates,
        "n_unique_dates": int(losses.match_date.nunique()),
        "log_loss": summary(log_deltas, float(losses.delta_log_loss.mean())),
        "brier": summary(brier_deltas, float(losses.delta_brier.mean())),
    }


def favorite_calibration(losses: pd.DataFrame) -> pd.DataFrame:
    probability = losses.probability_a.to_numpy(float)
    outcome = losses.team_a_won.to_numpy(bool)
    favorite_probability = np.maximum(probability, 1 - probability)
    favorite_won = np.where(probability >= 0.5, outcome, ~outcome)
    bins = pd.IntervalIndex.from_breaks(np.linspace(0.5, 1.0, 6), closed="right")
    bucketed = pd.DataFrame({
        "favorite_probability": np.clip(favorite_probability, np.nextafter(0.5, 1), 1.0),
        "favorite_won": favorite_won,
    })
    table = bucketed.assign(bin=pd.cut(bucketed.favorite_probability, bins=bins)).groupby("bin", observed=False).agg(
        n_matches=("favorite_won", "size"),
        mean_predicted_favorite_probability=("favorite_probability", "mean"),
        observed_favorite_win_rate=("favorite_won", "mean"),
    ).reset_index()
    table["calibration_gap_observed_minus_predicted"] = (
        table.observed_favorite_win_rate - table.mean_predicted_favorite_probability
    )
    table["bin"] = table["bin"].astype(str)
    return table


def experience_table(losses: pd.DataFrame) -> pd.DataFrame:
    result = losses.copy()
    result["minimum_prior_matches"] = result[["team_a_prior_matches", "team_b_prior_matches"]].min(axis=1)
    result["experience_bucket"] = pd.cut(
        result.minimum_prior_matches, bins=[-1, 0, 4, 19, np.inf], labels=["0", "1-4", "5-19", "20+"]
    )
    rows = []
    for bucket, group in result.groupby("experience_bucket", observed=False):
        row = {"experience_bucket": str(bucket), **metrics(group)}
        rows.append(row)
    return pd.DataFrame(rows)


def run_robustness(table: pd.DataFrame) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    sensitivity_rows = []
    all_predictions: dict[int, pd.DataFrame] = {}
    for k in K_GRID:
        predictions, _ = run_elo(table, EloConfig(k=k))
        all_predictions[k] = predictions
        sensitivity_rows.append({"k": k, **metrics(per_match_losses(predictions.query("year == 2024")))})
    predictions = all_predictions[64]
    losses = per_match_losses(predictions)
    test = losses.query("year == 2024").copy()
    yearly = pd.DataFrame([{"year": year, **metrics(losses.query("year == @year"))} for year in (2022, 2023, 2024)])
    segments = test.assign(segment=pd.to_datetime(test.match_date).dt.to_period("M").astype(str)).groupby("segment", sort=True).apply(
        lambda group: pd.Series(metrics(group)), include_groups=False
    ).reset_index()
    seen = pd.DataFrame([
        {"group": "both_teams_seen", **metrics(test.loc[test.both_teams_seen])},
        {"group": "any_team_unseen", **metrics(test.loc[test.any_team_unseen])},
    ])
    report = {
        "k_grid": list(K_GRID),
        "primary_k": 64,
        "yearly_k64": yearly.to_dict(orient="records"),
        "date_bootstrap_2024": date_bootstrap(test),
        "interpretation_guardrail": "2024 is a previously inspected historical robustness set, not an untouched holdout.",
    }
    outputs = {
        "k_sensitivity_2024": pd.DataFrame(sensitivity_rows),
        "segments_2024": segments,
        "seen_team_2024": seen,
        "experience_2024": experience_table(test),
        "favorite_calibration_2024": favorite_calibration(test),
        "losses_2024": test,
    }
    return report, outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-matches", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    table = pd.read_csv(args.canonical_matches, dtype={"match_id": "string", "team_a_id": "string", "team_b_id": "string"})
    table["team_a_won"] = table["team_a_won"].astype("string").str.lower().map({"true": True, "false": False})
    if table["team_a_won"].isna().any():
        raise ValueError("Canonical team_a_won values must be True or False")
    table["team_a_won"] = table["team_a_won"].astype(bool)
    report, outputs = run_robustness(table)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "robustness_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for name, frame in outputs.items():
        frame.to_csv(args.output_dir / f"{name}.csv", index=False)


if __name__ == "__main__":
    main()
