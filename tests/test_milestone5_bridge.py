from datetime import datetime, timezone

import pandas as pd

from valorant_quant.elo import EloConfig, run_elo
from valorant_quant.milestone5_bridge import apply_historical_identity, panda_to_elo_rows


def _match(match_id, begin, end, *, status="finished", winner=2, name_a="A", name_b="B"):
    return {"id":match_id,"begin_at":begin,"end_at":end,"status":status,"forfeit":False,"winner_id":winner,"opponents":[{"opponent":{"id":1,"name":name_a}},{"opponent":{"id":2,"name":name_b}}]}


def test_bridge_excludes_cutoff_and_incomplete_matches():
    captured=datetime(2026, 1, 2, tzinfo=timezone.utc)
    bridge, _, excluded=panda_to_elo_rows([
        _match(1,"2024-08-25T01:00:00Z","2024-08-25T02:00:00Z"),
        _match(2,"2024-08-26T01:00:00Z","2024-08-26T02:00:00Z",status="not_started"),
        _match(3,"2024-08-26T01:00:00Z","2024-08-26T02:00:00Z"),
    ],cutoff_date="2024-08-25",captured_at=captured)
    assert bridge.pandascore_match_id.tolist()==[3]
    assert set(excluded.reason)=={"pre_or_at_cutoff","not_finished"}


def test_new_pandascore_team_is_not_silently_merged_and_daily_batching_holds():
    bridge=pd.DataFrame([{"pandascore_team_a_id":99,"team_a_name":"Unresolved Rename","pandascore_team_b_id":2,"team_b_name":"Other","match_id":"pandascore:1","match_date":"2025-01-01","year":2025,"team_a_won":True,"tournament_name":"x","source_snapshot_id":"x"}])
    historical=pd.DataFrame([{"team_a_id":"1","team_a_name":"Known","team_b_id":"3","team_b_name":"Other"}])
    mapped,audit=apply_historical_identity(bridge,historical)
    assert mapped.team_a_id.iloc[0]=="ps:99"
    assert audit.loc[audit.pandascore_team_id.eq("99"),"historical_team_id"].isna().all()
    new_prediction,_=run_elo(mapped[["match_id","match_date","year","team_a_id","team_a_name","team_b_id","team_b_name","team_a_won","tournament_name","source_snapshot_id"]],EloConfig(k=64),allow_post_2024=True)
    assert new_prediction.rating_a_pre.iloc[0]==1500
    day=pd.DataFrame([{"match_id":"a","match_date":"2025-01-01","year":2025,"team_a_id":"1","team_a_name":"A","team_b_id":"2","team_b_name":"B","team_a_won":True,"tournament_name":"x","source_snapshot_id":"x"},{"match_id":"b","match_date":"2025-01-01","year":2025,"team_a_id":"1","team_a_name":"A","team_b_id":"3","team_b_name":"C","team_a_won":True,"tournament_name":"x","source_snapshot_id":"x"}])
    predictions,_=run_elo(day,EloConfig(k=64),allow_post_2024=True)
    assert predictions.rating_a_pre.nunique()==1
