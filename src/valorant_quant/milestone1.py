"""Read-only inspection and conservative match-table construction for Milestone 1."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


MATCH_KEY = ["Tournament", "Stage", "Match Type", "Match Name", "Year"]
SCORE_COLUMNS = [
    "Tournament", "Stage", "Match Type", "Match Name", "Team A", "Team B",
    "Team A Score", "Team B Score", "Match Result",
]


def read_csv(path: Path) -> pd.DataFrame:
    """Read source CSVs without converting literal strings such as player `nan` to NA."""
    return pd.read_csv(path, dtype="string", keep_default_na=False, na_values=[""])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(raw_files: Path) -> list[dict[str, Any]]:
    """Return a deterministic, lightweight inventory of every raw CSV."""
    records: list[dict[str, Any]] = []
    for path in sorted(raw_files.rglob("*.csv")):
        frame = read_csv(path)
        records.append({
            "path": path.relative_to(raw_files).as_posix(),
            "rows": len(frame),
            "columns": list(frame.columns),
            "null_counts": {column: int(count) for column, count in frame.isna().sum().items()},
            "sha256": sha256(path),
        })
    return records


def _year_directories(raw_files: Path) -> list[tuple[int, Path]]:
    result = []
    for directory in raw_files.glob("vct_*"):
        try:
            result.append((int(directory.name.removeprefix("vct_")), directory))
        except ValueError:
            continue
    return sorted(result)


def build_match_table(raw_files: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Create an audit table from series-level scores without fabricating chronology.

    The source contains no match date/time in the inspected match or ID files.
    Therefore every emitted row has a missing timestamp and is ineligible for
    chronological feature construction. This function deliberately does not
    sort rows or infer an ordering from CSV order, IDs, stages, or names.
    """
    score_frames = []
    for year, directory in _year_directories(raw_files):
        score_path = directory / "matches" / "scores.csv"
        if score_path.exists():
            frame = read_csv(score_path)[SCORE_COLUMNS].copy()
            frame["Year"] = str(year)
            score_frames.append(frame)
    scores = pd.concat(score_frames, ignore_index=True)

    ids = read_csv(raw_files / "all_ids" / "all_matches_games_ids.csv")
    match_ids = ids[MATCH_KEY + ["Match ID"]].drop_duplicates()
    match_id_counts = match_ids.groupby(MATCH_KEY, dropna=False)["Match ID"].nunique()
    ambiguous_match_keys = int((match_id_counts > 1).sum())
    # Scores have no source match ID. When a textual key resolves to more than
    # one ID, source row order cannot justify assigning either ID.
    match_ids = match_ids.merge(
        match_id_counts.rename("id_count").reset_index(), on=MATCH_KEY, validate="many_to_one"
    )
    match_ids = match_ids.loc[match_ids["id_count"].eq(1), MATCH_KEY + ["Match ID"]]
    matches = scores.merge(match_ids, how="left", on=MATCH_KEY, validate="many_to_one")
    matches["Source Match ID Status"] = matches["Match ID"].notna().map(
        {True: "resolved", False: "ambiguous_or_missing_source_key"}
    )

    teams = read_csv(raw_files / "all_ids" / "all_teams_ids.csv")
    team_counts = teams.groupby("Team", dropna=False)["Team ID"].nunique()
    unique_teams = teams[teams["Team"].map(team_counts).eq(1)].drop_duplicates("Team")
    ambiguous_team_names = set(team_counts[team_counts > 1].index)
    team_lookup = unique_teams.set_index("Team")["Team ID"]
    matches["Team A ID"] = matches["Team A"].map(team_lookup)
    matches["Team B ID"] = matches["Team B"].map(team_lookup)

    score_a = pd.to_numeric(matches["Team A Score"], errors="coerce")
    score_b = pd.to_numeric(matches["Team B Score"], errors="coerce")
    matches["Team A Won"] = pd.Series(pd.NA, index=matches.index, dtype="boolean")
    resolved = score_a.notna() & score_b.notna() & score_a.ne(score_b)
    matches.loc[resolved, "Team A Won"] = score_a[resolved].gt(score_b[resolved])

    # Explicit audit fields: no timestamp is inferred from any source proxy.
    matches["Match Start UTC"] = pd.Series(pd.NaT, index=matches.index, dtype="datetime64[ns, UTC]")
    matches["Timestamp Quality"] = "missing_in_source"
    matches["Chronology Eligible"] = False
    matches["Chronology Exclusion Reason"] = "No match date or timestamp in frozen source files"
    matches["Team A ID Ambiguous"] = matches["Team A"].isin(ambiguous_team_names)
    matches["Team B ID Ambiguous"] = matches["Team B"].isin(ambiguous_team_names)
    matches = matches.rename(columns={"Match ID": "Source Match ID"})

    ordered_columns = [
        "Source Match ID", "Source Match ID Status", "Year", "Match Start UTC", "Timestamp Quality",
        "Chronology Eligible", "Chronology Exclusion Reason", "Tournament", "Stage",
        "Match Type", "Match Name", "Team A", "Team A ID", "Team A ID Ambiguous",
        "Team B", "Team B ID", "Team B ID Ambiguous", "Team A Score", "Team B Score",
        "Match Result", "Team A Won",
    ]
    report = {
        "score_rows": len(scores),
        "source_match_ids_missing": int(matches["Source Match ID"].isna().sum()),
        "source_match_ids_duplicated": int(
            matches.loc[matches["Source Match ID"].notna(), "Source Match ID"].duplicated().sum()
        ),
        "ambiguous_match_keys": ambiguous_match_keys,
        "identity_ambiguous_score_rows": int(matches["Source Match ID"].isna().sum()),
        "ambiguous_team_names": sorted(ambiguous_team_names),
        "unresolved_outcomes": int(matches["Team A Won"].isna().sum()),
        "year_counts": {str(year): int(count) for year, count in matches["Year"].value_counts().sort_index().items()},
    }
    return matches[ordered_columns], report


def write_outputs(raw_files: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    table, match_report = build_match_table(raw_files)
    table.to_csv(output_dir / "match_level_audit.csv", index=False)
    summary = {"inventory": inventory(raw_files), "match_audit": match_report}
    (output_dir / "inspection.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-files", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    write_outputs(args.raw_files, args.output_dir)


if __name__ == "__main__":
    main()
