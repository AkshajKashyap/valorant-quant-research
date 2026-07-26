from datetime import datetime, timezone
import pytest
from valorant_quant.market_data import (
    exact_fixture_matches,
    normalized_team_name,
    two_way_probabilities,
    unique_historical_team_ids,
    validate_normalized_record,
    write_raw_snapshot,
)

def test_two_way_no_vig_conversion_is_transparent():
    value=two_way_probabilities(2.0,2.5)
    assert value["team_a_raw_implied_probability"]==.5
    assert value["team_a_no_vig_probability"]+value["team_b_no_vig_probability"]==pytest.approx(1)

def test_snapshot_is_immutable_and_normalized_schema_is_checked(tmp_path):
    path,digest=write_raw_snapshot({"event":"example"},tmp_path,datetime(2026,7,24,tzinfo=timezone.utc))
    assert path.exists() and len(digest)==64
    with pytest.raises(FileExistsError): write_raw_snapshot({"event":"example"},tmp_path,datetime(2026,7,24,tzinfo=timezone.utc))
    record={"captured_at_utc":"2026-07-24T00:00:00Z","source":"x","source_event_id":"e","scheduled_start_utc":"2026-07-25T00:00:00Z","team_a":"A","team_b":"B","bookmaker":"book","market_type":"moneyline","team_a_decimal_odds":2.0,"team_b_decimal_odds":2.0,"source_last_update_utc":"2026-07-24T00:00:00Z","raw_snapshot_hash":digest}
    validate_normalized_record(record)


def test_fixture_reconciliation_requires_exact_unambiguous_names_and_times():
    panda=[{"team_a":"G2 Esports","team_b":"Cloud9","scheduled_start_utc":"2026-07-25T21:00:00Z"}]
    odds=[{"team_a":"Cloud9","team_b":"G2 Esports","scheduled_start_utc":"2026-07-25T21:00:00Z"}]
    assert exact_fixture_matches(panda, odds)==[(panda[0], odds[0])]
    assert exact_fixture_matches(panda, odds+[{**odds[0]}])==[]
    assert normalized_team_name("QT DIG∞")=="qtdig"


def test_historical_team_mapping_excludes_duplicate_normalized_names():
    rows=[
        {"team_name":"Cloud9", "team_id":1},
        {"team_name":"Cloud 9", "team_id":1},
        {"team_name":"TBD", "team_id":2},
        {"team_name":"TBD", "team_id":3},
    ]
    assert unique_historical_team_ids(rows)=={"cloud9":"1"}
