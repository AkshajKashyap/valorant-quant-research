from pathlib import Path
import pytest

from valorant_quant.elo import EloConfig, run_elo
from valorant_quant.prospective import append_ledger_record, mechanically_eligible, primary_snapshot_current, select_primary_snapshot, true_cold_start


def observation(captured, *, bookmaker="Bet365", odds_a=2.0, odds_b=2.0):
    return {"captured_at_utc":captured,"bookmaker":bookmaker,"market_type":"ML","team_a_decimal_odds":odds_a,"team_b_decimal_odds":odds_b,"raw_snapshot_hash":"x"}


def test_t60_selection_is_deterministic_and_rejects_post_start():
    start="2026-07-26T20:00:00Z"
    chosen=select_primary_snapshot([observation("2026-07-26T18:58:00Z"),observation("2026-07-26T19:02:00Z"),observation("2026-07-26T20:01:00Z")],start)
    assert chosen["captured_at_utc"]=="2026-07-26T18:58:00Z"
    assert select_primary_snapshot([observation("2026-07-26T20:01:00Z")],start) is None


def test_reschedule_eligibility_and_cold_start_are_mechanical():
    assert not primary_snapshot_current("2026-07-26T20:00:00Z","2026-07-26T21:01:00Z")
    assert primary_snapshot_current("2026-07-26T20:00:00Z","2026-07-26T20:59:00Z")
    assert true_cold_start(0) and not true_cold_start(1)
    fixture={field:True for field in ("professional","two_actual_teams","future_at_prediction","valid_elo_state","unambiguous_reconciliation","bet365_two_sided_ml","valid_primary_snapshot","forecast_before_start")}
    fixture["disagreement_a"]=999
    assert mechanically_eligible(fixture)


def test_ledger_is_append_only_duplicate_safe_and_secret_free(tmp_path: Path):
    ledger=tmp_path/"ledger.jsonl"
    append_ledger_record(ledger,{"record_id":"forecast:1","record_type":"forecast_generated","p":.5})
    with pytest.raises(ValueError): append_ledger_record(ledger,{"record_id":"forecast:1","record_type":"forecast_generated"})
    with pytest.raises(ValueError): append_ledger_record(ledger,{"record_id":"x","record_type":"forecast_generated","api_key":"no"})
    assert 'api_key' not in ledger.read_text()


def test_outcome_is_an_additional_event_and_same_day_results_do_not_change_pre_match_state(tmp_path: Path):
    ledger=tmp_path/"ledger.jsonl"
    forecast={"record_id":"forecast:1","record_type":"forecast_generated","p_team_a_wins":.5}
    append_ledger_record(ledger,forecast)
    append_ledger_record(ledger,{"record_id":"outcome:1","record_type":"outcome_attached","forecast_record_id":"forecast:1","team_a_won":True})
    assert ledger.read_text().splitlines()[0].endswith('"record_type":"forecast_generated"}')
    rows=[]
    for match_id, opponent in (("m1","B"),("m2","C")):
        rows.append({"match_id":match_id,"match_date":"2026-01-01","year":2026,"team_a_id":"A","team_a_name":"A","team_b_id":opponent,"team_b_name":opponent,"team_a_won":True,"tournament_name":"x","source_snapshot_id":"x"})
    predictions,_=run_elo(__import__('pandas').DataFrame(rows),EloConfig(k=64),allow_post_2024=True)
    assert predictions.rating_a_pre.tolist()==[1500,1500]
