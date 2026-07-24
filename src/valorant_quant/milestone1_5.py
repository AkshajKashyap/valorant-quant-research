"""Frozen-source chronology and exact-ID linkage audit for Milestone 1.5."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from valorant_quant.milestone1 import build_match_table, read_csv


def parse_vlr_match_id(match_page: pd.Series) -> pd.Series:
    """Extract an ID only from a canonical VLR relative match path."""
    return match_page.astype("string").str.extract(r"^/(\d+)/", expand=False)


def valid_calendar_date(values: pd.Series) -> pd.Series:
    """Parse date-only chronology, rejecting the source's 1970 placeholder."""
    parsed = pd.to_datetime(values, errors="coerce")
    return parsed.where(parsed.dt.year.ge(2020))


def _source_matches(current_raw_files: Path) -> pd.DataFrame:
    matches, _ = build_match_table(current_raw_files)
    matches = matches.loc[matches["Source Match ID"].notna()].copy()
    matches["Source Match ID"] = matches["Source Match ID"].astype("string")
    return matches


def _exact_link(source: pd.DataFrame, candidate_ids: pd.Series) -> pd.DataFrame:
    candidate = pd.DataFrame({"candidate_match_id": candidate_ids.astype("string")}).drop_duplicates()
    return source[["Source Match ID", "Year"]].merge(
        candidate, left_on="Source Match ID", right_on="candidate_match_id", how="left", indicator=True
    )


def audit(
    current_raw_files: Path,
    visualize_db: Path,
    hidious_results: Path,
    large_csv: Path,
) -> tuple[dict[str, Any], pd.DataFrame]:
    source = _source_matches(current_raw_files)

    with sqlite3.connect(visualize_db) as connection:
        visualize = pd.read_sql_query("SELECT MatchID, Date FROM Matches", connection, dtype_backend="numpy_nullable")
    visualize["MatchID"] = visualize["MatchID"].astype("string")
    visual_dates = pd.to_datetime(visualize["Date"], errors="coerce")
    visual_link = _exact_link(source, visualize["MatchID"])

    hidious = read_csv(hidious_results)
    hidious_ids = parse_vlr_match_id(hidious["match_page"])
    hidious_link = _exact_link(source, hidious_ids)

    large = read_csv(large_csv)
    large["MatchID"] = large["MatchID"].astype("string")
    large_dates = valid_calendar_date(large["Date"])
    series = pd.DataFrame({"MatchID": large["MatchID"], "match_date": large_dates})
    if series.groupby("MatchID")["match_date"].nunique(dropna=False).gt(1).any():
        raise ValueError("Large-scale source has conflicting dates within a MatchID")
    series = series.drop_duplicates("MatchID")
    large_link = source.merge(
        series, left_on="Source Match ID", right_on="MatchID", how="left", indicator=True
    )
    date_mapping = large_link.loc[large_link["match_date"].notna(), ["Source Match ID", "match_date"]].copy()
    date_mapping["match_date"] = date_mapping["match_date"].dt.date.astype("string")
    date_mapping["chronology_tier"] = "B_calendar_date"
    date_mapping["same_day_rule"] = "batch_updates_after_date"
    date_mapping = date_mapping.sort_values("Source Match ID", kind="stable")

    def link_metrics(link: pd.DataFrame, candidate_records: int, candidate_unique: int) -> dict[str, Any]:
        linked = link["_merge"].eq("both")
        return {
            "candidate_records": candidate_records,
            "candidate_unique_match_ids": candidate_unique,
            "exact_id_linked": int(linked.sum()),
            "composite_or_fuzzy_linked": 0,
            "unmatched_source_matches": int((~linked).sum()),
            "source_coverage": float(linked.mean()),
            "source_year_coverage": {
                str(year): {"linked": int(group["_merge"].eq("both").sum()), "total": int(len(group))}
                for year, group in link.groupby("Year", sort=True)
            },
        }

    report = {
        "source_resolved_matches": int(len(source)),
        "visualize25": {
            **link_metrics(visual_link, len(visualize), int(visualize["MatchID"].nunique())),
            "date_parseable": int(visual_dates.notna().sum()),
            "date_min": str(visual_dates.min()),
            "date_max": str(visual_dates.max()),
            "chronology_precision": "A_datetime (semantics not independently documented)",
        },
        "hidious": {
            **link_metrics(hidious_link, len(hidious), int(hidious_ids.nunique())),
            "relative_time_rows": int(hidious["time_completed"].notna().sum()),
            "absolute_date_rows": int(hidious["time_completed"].str.contains(r"\d{4}-\d{2}-\d{2}", regex=True, na=False).sum()),
            "chronology_precision": "D_relative_to_undocumented_collection_instant",
        },
        "large_scale": {
            **link_metrics(large_link, len(large), int(series["MatchID"].nunique())),
            "candidate_grain": "map/game row",
            "calendar_date_series": int(series["match_date"].notna().sum()),
            "calendar_date_min": str(series["match_date"].min().date()),
            "calendar_date_max": str(series["match_date"].max().date()),
            "chronology_precision": "B_calendar_date",
            "linked_calendar_date_rows": int(len(date_mapping)),
        },
    }
    return report, date_mapping


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-raw-files", type=Path, required=True)
    parser.add_argument("--visualize-db", type=Path, required=True)
    parser.add_argument("--hidious-results", type=Path, required=True)
    parser.add_argument("--large-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report, date_mapping = audit(
        args.current_raw_files, args.visualize_db, args.hidious_results, args.large_csv
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "chronology_audit.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    date_mapping.to_csv(args.output_dir / "large_scale_exact_id_calendar_dates.csv", index=False)


if __name__ == "__main__":
    main()
