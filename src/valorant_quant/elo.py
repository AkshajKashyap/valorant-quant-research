"""Minimal leakage-safe, calendar-date-batched Elo baseline."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


INITIAL_RATING = 1500.0
DEFAULT_SCALE = 400.0


def win_probability(rating_a: float, rating_b: float, scale: float = DEFAULT_SCALE) -> float:
    return float(1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / scale)))


def validate_canonical_matches(matches: pd.DataFrame) -> None:
    required = {
        "match_id", "match_date", "year", "team_a_id", "team_a_name",
        "team_b_id", "team_b_name", "team_a_won", "tournament_name",
        "source_snapshot_id",
    }
    missing = required - set(matches.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    if matches["match_id"].isna().any() or matches["match_id"].duplicated().any():
        raise ValueError("match_id must be present and unique")
    dates = pd.to_datetime(matches["match_date"], errors="coerce")
    if dates.isna().any() or dates.dt.year.eq(1970).any():
        raise ValueError("match_date must be valid and cannot use the 1970 placeholder")
    if not dates.dt.year.isin([2021, 2022, 2023, 2024]).all():
        raise ValueError("Only 2021-2024 records are permitted")
    if matches[["team_a_id", "team_b_id", "team_a_name", "team_b_name"]].isna().any().any():
        raise ValueError("Both teams must be present")
    if matches["team_a_id"].eq(matches["team_b_id"]).any():
        raise ValueError("Teams must be distinct")
    if matches[["team_a_name", "team_b_name"]].isin(["TBD"]).any().any():
        raise ValueError("Placeholder teams are not permitted")
    outcome = matches["team_a_won"]
    if outcome.isna().any() or not outcome.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("team_a_won must be binary")


@dataclass(frozen=True)
class EloConfig:
    k: float
    scale: float = DEFAULT_SCALE
    initial_rating: float = INITIAL_RATING


def run_elo(matches: pd.DataFrame, config: EloConfig) -> tuple[pd.DataFrame, dict[str, float]]:
    """Run online Elo with strictly simultaneous updates inside each date."""
    validate_canonical_matches(matches)
    frame = matches.copy()
    frame["match_date"] = pd.to_datetime(frame["match_date"])
    frame = frame.sort_values(["match_date", "match_id"], kind="stable")
    ratings: dict[str, float] = {}
    prior_matches: dict[str, int] = defaultdict(int)
    records: list[dict[str, Any]] = []

    for date, day in frame.groupby("match_date", sort=True):
        start_ratings = ratings.copy()
        start_history = prior_matches.copy()
        changes: dict[str, float] = defaultdict(float)
        for row in day.itertuples(index=False):
            team_a, team_b = str(row.team_a_id), str(row.team_b_id)
            rating_a = start_ratings.get(team_a, config.initial_rating)
            rating_b = start_ratings.get(team_b, config.initial_rating)
            probability_a = win_probability(rating_a, rating_b, config.scale)
            outcome_a = float(bool(row.team_a_won))
            delta_a = config.k * (outcome_a - probability_a)
            changes[team_a] += delta_a
            changes[team_b] -= delta_a
            unseen = start_history.get(team_a, 0) == 0 or start_history.get(team_b, 0) == 0
            records.append({
                "match_id": row.match_id,
                "match_date": date.date().isoformat(),
                "year": int(row.year),
                "team_a_id": team_a,
                "team_b_id": team_b,
                "team_a_won": bool(row.team_a_won),
                "rating_a_pre": rating_a,
                "rating_b_pre": rating_b,
                "probability_a": probability_a,
                "elo_delta_a": delta_a,
                "team_a_prior_matches": start_history.get(team_a, 0),
                "team_b_prior_matches": start_history.get(team_b, 0),
                "any_team_unseen": unseen,
                "both_teams_seen": not unseen,
            })
        for team, delta in changes.items():
            ratings[team] = start_ratings.get(team, config.initial_rating) + delta
        for row in day.itertuples(index=False):
            prior_matches[str(row.team_a_id)] += 1
            prior_matches[str(row.team_b_id)] += 1

    predictions = pd.DataFrame(records).sort_values(["match_date", "match_id"], kind="stable").reset_index(drop=True)
    return predictions, dict(sorted(ratings.items()))


def _metric_block(predictions: pd.DataFrame) -> dict[str, Any]:
    if predictions.empty:
        return {"n_matches": 0}
    probabilities = predictions["probability_a"].to_numpy(dtype=float)
    outcomes = predictions["team_a_won"].to_numpy(dtype=float)
    clipped = np.clip(probabilities, 1e-15, 1 - 1e-15)
    decisions = probabilities >= 0.5
    return {
        "n_matches": int(len(predictions)),
        "n_unique_teams": int(pd.concat([predictions.team_a_id, predictions.team_b_id]).nunique()),
        "log_loss": float(-(outcomes * np.log(clipped) + (1 - outcomes) * np.log(1 - clipped)).mean()),
        "brier_score": float(np.mean((probabilities - outcomes) ** 2)),
        "accuracy": float(np.mean(decisions == outcomes.astype(bool))),
        "fifty_fifty_log_loss": float(np.log(2.0)),
        "fifty_fifty_brier_score": 0.25,
        "fifty_fifty_accuracy_team_a_tiebreak": float(np.mean(outcomes == 1.0)),
        "pct_any_team_unseen": float(predictions["any_team_unseen"].mean()),
        "average_predicted_confidence": float(np.mean(np.maximum(probabilities, 1 - probabilities))),
    }


def calibration_table(predictions: pd.DataFrame) -> pd.DataFrame:
    bins = pd.IntervalIndex.from_breaks(np.linspace(0, 1, 11), closed="right")
    probability = predictions["probability_a"].clip(lower=np.nextafter(0, 1), upper=1.0)
    grouped = predictions.assign(probability_bin=pd.cut(probability, bins=bins)).groupby(
        "probability_bin", observed=False
    )
    table = grouped.agg(
        n_matches=("team_a_won", "size"),
        mean_predicted_probability=("probability_a", "mean"),
        observed_team_a_win_rate=("team_a_won", "mean"),
    ).reset_index()
    table["probability_bin"] = table["probability_bin"].astype(str)
    return table


def rating_distribution(ratings: dict[str, float]) -> dict[str, float]:
    values = np.array(list(ratings.values()), dtype=float)
    return {
        "n_teams": int(len(values)), "mean": float(values.mean()), "std": float(values.std()),
        "min": float(values.min()), "p25": float(np.quantile(values, .25)),
        "median": float(np.median(values)), "p75": float(np.quantile(values, .75)), "max": float(values.max()),
    }


def evaluate(predictions: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    metrics: dict[str, Any] = {}
    for label, subset in {
        "2022": predictions.query("year == 2022"),
        "2023": predictions.query("year == 2023"),
        "development_2022_2023": predictions.query("year in [2022, 2023]"),
        "test_2024": predictions.query("year == 2024"),
    }.items():
        metrics[label] = _metric_block(subset)
    metrics["cold_start"] = {
        "all_periods": {
            "both_teams_seen": _metric_block(predictions.loc[predictions.both_teams_seen]),
            "any_team_unseen": _metric_block(predictions.loc[predictions.any_team_unseen]),
        },
        "development_2022_2023": {
            "both_teams_seen": _metric_block(predictions.query("year in [2022, 2023] and both_teams_seen")),
            "any_team_unseen": _metric_block(predictions.query("year in [2022, 2023] and any_team_unseen")),
        },
        "test_2024": {
            "both_teams_seen": _metric_block(predictions.query("year == 2024 and both_teams_seen")),
            "any_team_unseen": _metric_block(predictions.query("year == 2024 and any_team_unseen")),
        },
    }
    return metrics, calibration_table(predictions.query("year == 2024"))
