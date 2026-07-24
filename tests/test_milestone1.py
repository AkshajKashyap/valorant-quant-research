from pathlib import Path

import pandas as pd

from valorant_quant.milestone1 import build_match_table, read_csv
from valorant_quant.milestone1_5 import parse_vlr_match_id, valid_calendar_date


def write_csv(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def fixture_snapshot(tmp_path: Path) -> Path:
    raw = tmp_path / "files"
    write_csv(
        raw / "vct_2021/matches/scores.csv",
        "Tournament,Stage,Match Type,Match Name,Team A,Team B,Team A Score,Team B Score,Match Result\n"
        "Event,Stage,Round 1,Alpha vs Beta,Alpha,Beta,2,1,Alpha won\n",
    )
    write_csv(
        raw / "all_ids/all_matches_games_ids.csv",
        "Tournament,Tournament ID,Stage,Stage ID,Match Type,Match Type ID,Match Name,Match ID,Map,Game ID,Year\n"
        "Event,1,Stage,2,Round 1,3,Alpha vs Beta,42,Bind,100,2021\n"
        "Event,1,Stage,2,Round 1,3,Alpha vs Beta,42,Haven,101,2021\n",
    )
    write_csv(raw / "all_ids/all_teams_ids.csv", "Team,Team ID\nAlpha,10\nBeta,20\n")
    return raw


def test_literal_nan_is_preserved_as_a_string(tmp_path: Path) -> None:
    source = tmp_path / "players.csv"
    source.write_text("Player\nnan\n")
    assert read_csv(source).loc[0, "Player"] == "nan"


def test_match_table_is_series_level_and_never_infers_chronology(tmp_path: Path) -> None:
    table, report = build_match_table(fixture_snapshot(tmp_path))
    assert len(table) == 1
    assert table.loc[0, "Source Match ID"] == "42"
    assert table.loc[0, "Source Match ID Status"] == "resolved"
    assert table.loc[0, "Team A ID"] == "10"
    assert table.loc[0, "Team A Won"] == True
    assert pd.isna(table.loc[0, "Match Start UTC"])
    assert not table.loc[0, "Chronology Eligible"]
    assert report["source_match_ids_missing"] == 0


def test_ambiguous_text_key_does_not_get_an_id_from_source_row_order(tmp_path: Path) -> None:
    raw = fixture_snapshot(tmp_path)
    ids = raw / "all_ids/all_matches_games_ids.csv"
    ids.write_text(ids.read_text() + "Event,1,Stage,2,Round 1,3,Alpha vs Beta,43,Haven,101,2021\n")
    table, report = build_match_table(raw)
    assert pd.isna(table.loc[0, "Source Match ID"])
    assert table.loc[0, "Source Match ID Status"] == "ambiguous_or_missing_source_key"
    assert report["identity_ambiguous_score_rows"] == 1


def test_vlr_path_and_calendar_date_validation_are_conservative() -> None:
    assert parse_vlr_match_id(pd.Series(["/60968/example", "not-a-path"])).tolist() == ["60968", pd.NA]
    dates = valid_calendar_date(pd.Series(["2024-09-04", "1970-01-01", "not a date"]))
    assert str(dates.iloc[0].date()) == "2024-09-04"
    assert pd.isna(dates.iloc[1])
    assert pd.isna(dates.iloc[2])
