"""Build and evaluate the Milestone 2 calendar-date-batched Elo baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from valorant_quant.elo import EloConfig, evaluate, rating_distribution, run_elo, validate_canonical_matches
from valorant_quant.milestone1 import build_match_table


K_GRID = (8, 16, 24, 32, 48, 64)
SOURCE_SNAPSHOT_ID = "kaggle_ryanluong1_valorant_champion_tour/v47"
CHRONOLOGY_SNAPSHOT_ID = "google_drive_benetheburrito_large_scale_valorant_2020_2024/v1"


def build_canonical_table(current_raw_files: Path, calendar_dates_csv: Path) -> pd.DataFrame:
    """Build the approved exact-ID, date-batched series-level modeling table."""
    source, _ = build_match_table(current_raw_files)
    dates = pd.read_csv(calendar_dates_csv, dtype="string")
    dates = dates.rename(columns={"Source Match ID": "match_id", "match_date": "match_date"})
    source = source.rename(columns={
        "Source Match ID": "match_id", "Year": "source_partition_year",
        "Team A ID": "team_a_id", "Team A": "team_a_name",
        "Team B ID": "team_b_id", "Team B": "team_b_name",
        "Team A Won": "team_a_won", "Tournament": "tournament_name",
    })
    source = source.loc[source["match_id"].notna()].copy()
    table = source.merge(dates[["match_id", "match_date"]], how="inner", on="match_id", validate="one_to_one")
    table["match_date"] = pd.to_datetime(table["match_date"], errors="coerce")
    table["year"] = table["match_date"].dt.year.astype("Int64")
    valid = (
        table["team_a_won"].notna()
        & table["team_a_id"].notna() & table["team_b_id"].notna()
        & table["team_a_name"].ne("TBD") & table["team_b_name"].ne("TBD")
        & table["team_a_id"].ne(table["team_b_id"])
        & table["year"].isin([2021, 2022, 2023, 2024])
    )
    table = table.loc[valid].copy()
    table["team_a_won"] = table["team_a_won"].astype(bool)
    table["source_snapshot_id"] = SOURCE_SNAPSHOT_ID
    table["chronology_snapshot_id"] = CHRONOLOGY_SNAPSHOT_ID
    table["match_date"] = table["match_date"].dt.date.astype("string")
    columns = [
        "match_id", "match_date", "year", "source_partition_year", "team_a_id", "team_a_name",
        "team_b_id", "team_b_name", "team_a_won", "tournament_name", "Stage", "Match Type",
        "source_snapshot_id", "chronology_snapshot_id",
    ]
    table = table[columns].sort_values(["match_date", "match_id"], kind="stable").reset_index(drop=True)
    validate_canonical_matches(table)
    return table


def run_experiment(
    table: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, dict[str, float], pd.DataFrame]:
    """Choose K solely on 2022-23 and evaluate the frozen 2024 period once."""
    grid_rows = []
    for k in K_GRID:
        predictions, _ = run_elo(table, EloConfig(k=k))
        development, _ = evaluate(predictions)
        grid_rows.append({
            "k": k,
            "development_log_loss": development["development_2022_2023"]["log_loss"],
            "development_brier_score": development["development_2022_2023"]["brier_score"],
            "development_accuracy": development["development_2022_2023"]["accuracy"],
        })
    grid = pd.DataFrame(grid_rows).sort_values(["development_log_loss", "k"], kind="stable").reset_index(drop=True)
    selected_k = int(grid.loc[0, "k"])
    predictions, final_ratings = run_elo(table, EloConfig(k=selected_k))
    metrics, calibration = evaluate(predictions)
    metrics["selected_k"] = selected_k
    metrics["selection_metric"] = "minimum 2022-2023 log loss over predetermined K grid"
    metrics["rating_distributions_end_of_year"] = {}
    for year in (2021, 2022, 2023, 2024):
        _, ratings = run_elo(table.loc[table.year <= year], EloConfig(k=selected_k))
        metrics["rating_distributions_end_of_year"][str(year)] = rating_distribution(ratings)
    return metrics, grid, calibration, final_ratings | {"__selected_k__": float(selected_k)}, predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-raw-files", type=Path, required=True)
    parser.add_argument("--calendar-dates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    table = build_canonical_table(args.current_raw_files, args.calendar_dates)
    metrics, grid, calibration, ratings_with_k, predictions = run_experiment(table)
    selected_k = ratings_with_k.pop("__selected_k__")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output_dir / "canonical_matches.csv", index=False)
    grid.to_csv(args.output_dir / "development_k_grid.csv", index=False)
    predictions.loc[predictions.year.eq(2024)].to_csv(args.output_dir / "predictions_2024.csv", index=False)
    calibration.to_csv(args.output_dir / "calibration_2024.csv", index=False)
    pd.DataFrame({"team_id": list(ratings_with_k), "elo_rating": list(ratings_with_k.values())}).to_csv(
        args.output_dir / "final_ratings.csv", index=False
    )
    metrics["selected_k"] = int(selected_k)
    (args.output_dir / "evaluation_metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
