from datetime import datetime, timezone
import json

from valorant_quant import milestone6_runner as runner
from valorant_quant.prospective import append_ledger_record, parse_utc


def fixture(start, *, match_id=1, status="not_started", winner_id=None, forfeit=False):
    return {"id":match_id,"scheduled_at":start,"status":status,"winner_id":winner_id,"forfeit":forfeit,
            "opponents":[{"opponent":{"id":10,"name":"A"}},{"opponent":{"id":11,"name":"B"}}]}


def forecast(start, *, match_id="1"):
    return {"record_id":f"forecast:{match_id}:{start}","record_type":"forecast_generated",
            "generated_at_utc":"2026-07-26T10:00:00Z","pandascore_match_id":match_id,
            "scheduled_start_utc":start,"team_a_provider_id":10,"team_b_provider_id":11}


def candidate(start, captured, *, match_id="1", event_id="2"):
    return {"record_id":f"candidate:{event_id}:u:2.0:2.0","record_type":"market_candidate",
            "captured_at_utc":captured,"pandascore_match_id":match_id,"odds_api_event_id":event_id,
            "scheduled_start_utc":start,"lead_time_minutes":60.0,"bookmaker":"Bet365",
            "market_type":"ML","team_a_decimal_odds":"2.0","team_b_decimal_odds":"2.0",
            "raw_snapshot_hash":"hash"}


def primary(start, candidate_id, *, match_id="1"):
    return {"record_id":f"primary:{match_id}:{candidate_id}","record_type":"primary_market_selected",
            "pandascore_match_id":match_id,"candidate_record_id":candidate_id,
            "scheduled_start_utc":start}


def ledger_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def configure(monkeypatch, tmp_path):
    monkeypatch.setattr(runner,"ROOT",tmp_path)
    monkeypatch.setattr(runner,"LEDGER",tmp_path/"ledger.jsonl")
    monkeypatch.setattr(runner,"load_credentials",lambda:("p","o"))


def test_dry_run_is_idempotent_and_does_not_append(monkeypatch, tmp_path):
    monkeypatch.setattr(runner,"ROOT",tmp_path); monkeypatch.setattr(runner,"LEDGER",tmp_path/"ledger.jsonl")
    monkeypatch.setattr(runner,"load_credentials",lambda:("p","o"))
    monkeypatch.setattr(runner,"reconstruct_d1_state",lambda date:({}, {}, "2026-07-25"))
    fixture={"id":1,"scheduled_at":"2026-07-26T13:00:00Z","opponents":[{"opponent":{"id":10,"name":"A"}},{"opponent":{"id":11,"name":"B"}}]}
    event={"id":2,"home":"A","away":"B","date":"2026-07-26T13:00:00Z"}
    odds={"bookmakers":{"Bet365":[{"name":"ML","updatedAt":"x","odds":[{"home":"2.0","away":"2.0"}]}]}}
    def fake(url, **kwargs):
        if "upcoming" in url:return [fixture]
        if "leagues" in url:return [{"name":"Valorant","slug":"v"}]
        if "events" in url:return [event]
        return odds
    monkeypatch.setattr(runner,"get_json",fake)
    result=runner.run_once(dry_run=True,now=datetime(2026,7,26,12,tzinfo=timezone.utc))
    assert result["would_append"]==2 and not runner.LEDGER.exists()


def test_status_has_no_performance_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(runner,"LEDGER",tmp_path/"missing")
    assert set(runner.status())=={"target","forecasts_frozen","primary_snapshots","outcomes_attached","candidate_snapshots","eligible_completed","reschedules","superseded_forecasts","superseded_primaries","terminal_exclusions"}


def test_runner_normal_lifecycle_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(runner,"ROOT",tmp_path); monkeypatch.setattr(runner,"LEDGER",tmp_path/"ledger.jsonl")
    monkeypatch.setattr(runner,"load_credentials",lambda:("p","o")); monkeypatch.setattr(runner,"reconstruct_d1_state",lambda date:({}, {}, "2026-07-25"))
    fixture={"id":1,"scheduled_at":"2026-07-26T13:00:00Z","opponents":[{"opponent":{"id":10,"name":"A"}},{"opponent":{"id":11,"name":"B"}}]}
    event={"id":2,"home":"A","away":"B","date":"2026-07-26T13:00:00Z"}; odds={"bookmakers":{"Bet365":[{"name":"ML","updatedAt":"u","odds":[{"home":"2.0","away":"2.0"}]}]}}
    completed={**fixture,"status":"finished","forfeit":False,"winner_id":10}
    phase={"value":0}
    def fake(url, **kwargs):
        if url.endswith('/1'): return completed if phase["value"]>=3 else fixture
        if "upcoming" in url:return [fixture] if phase["value"]<3 else []
        if "leagues" in url:return [{"name":"Valorant","slug":"v"}]
        if "events" in url:return [event]
        return odds
    monkeypatch.setattr(runner,"get_json",fake)
    capture_time=datetime(2026,7,26,12,tzinfo=timezone.utc)
    primary_time=datetime(2026,7,26,12,20,tzinfo=timezone.utc)
    completion_time=datetime(2026,7,26,13,10,tzinfo=timezone.utc)
    assert runner.lead_time_minutes(capture_time.isoformat(),fixture["scheduled_at"])==60
    assert runner.lead_time_minutes(primary_time.isoformat(),fixture["scheduled_at"])<45
    assert completion_time >= parse_utc(fixture["scheduled_at"])
    runner.run_once(now=capture_time)
    first_rows=[json.loads(x) for x in runner.LEDGER.read_text().splitlines()]
    frozen_forecast=next(x for x in first_rows if x["record_type"]=="forecast_generated")
    frozen_candidate=next(x for x in first_rows if x["record_type"]=="market_candidate")
    phase["value"]=1; runner.run_once(now=capture_time)
    phase["value"]=2; runner.run_once(now=primary_time)
    phase["value"]=3; runner.run_once(now=completion_time)
    rows=[json.loads(x) for x in runner.LEDGER.read_text().splitlines()]
    assert sum(x["record_type"]=="forecast_generated" for x in rows)==1
    assert sum(x["record_type"]=="market_candidate" for x in rows)==1
    assert sum(x["record_type"]=="primary_market_selected" for x in rows)==1
    assert sum(x["record_type"]=="outcome_attached" for x in rows)==1
    primary=next(x for x in rows if x["record_type"]=="primary_market_selected")
    outcome=next(x for x in rows if x["record_type"]=="outcome_attached")
    assert primary["candidate_record_id"]==frozen_candidate["record_id"]
    assert str(outcome["pandascore_match_id"])==str(frozen_forecast["pandascore_match_id"])=="1"
    assert next(x for x in rows if x["record_id"]==frozen_forecast["record_id"])==frozen_forecast
    assert next(x for x in rows if x["record_id"]==frozen_candidate["record_id"])==frozen_candidate
    assert runner.eligible_completed_count(rows)==1
    before=runner.LEDGER.read_text()
    before_counts={kind:sum(r["record_type"]==kind for r in rows) for kind in (
        "forecast_generated","market_candidate","primary_market_selected","reschedule",
        "forecast_superseded","outcome_attached","terminal_exclusion")}
    runner.run_once(now=datetime(2026,7,26,13,11,tzinfo=timezone.utc))
    assert runner.LEDGER.read_text()==before
    after=ledger_rows(runner.LEDGER)
    assert {kind:sum(r["record_type"]==kind for r in after) for kind in before_counts}==before_counts


def test_runner_same_date_material_reschedule_preserves_forecast(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    old_start="2026-07-26T14:00:00Z"; new_start="2026-07-26T16:00:00Z"
    old_forecast=forecast(old_start); old_candidate=candidate(old_start,"2026-07-26T13:00:00Z")
    for record in (old_forecast,old_candidate,primary(old_start,old_candidate["record_id"])): append_ledger_record(runner.LEDGER,record)
    event={"id":3,"home":"A","away":"B","date":new_start}
    odds={"bookmakers":{"Bet365":[{"name":"ML","updatedAt":"new","odds":[{"home":"1.8","away":"2.1"}]}]}}
    def fake(url,**kwargs):
        if "upcoming" in url:return [fixture(new_start)]
        if "leagues" in url:return [{"name":"Valorant","slug":"v"}]
        if "events" in url:return [event]
        return odds
    monkeypatch.setattr(runner,"get_json",fake)
    runner.run_once(now=datetime(2026,7,26,15,tzinfo=timezone.utc))
    state=ledger_rows(runner.LEDGER)
    assert sum(r["record_type"]=="reschedule" for r in state)==1
    assert sum(r["record_type"]=="forecast_generated" for r in state)==1
    assert sum(r["record_type"]=="primary_market_superseded" for r in state)==1
    assert sum(r["record_type"]=="market_candidate" and r["scheduled_start_utc"]==new_start for r in state)==1
    assert next(r for r in state if r["record_id"]==old_forecast["record_id"])==old_forecast
    assert next(r for r in state if r["record_id"]==old_candidate["record_id"])==old_candidate
    runner.run_once(now=datetime(2026,7,26,15,20,tzinfo=timezone.utc))
    state=ledger_rows(runner.LEDGER)
    new_candidates=[r for r in state if r["record_type"]=="market_candidate" and r["scheduled_start_utc"]==new_start]
    new_primaries=[r for r in state if r["record_type"]=="primary_market_selected" and r["scheduled_start_utc"]==new_start]
    assert len(new_candidates)==len(new_primaries)==1
    assert new_primaries[0]["candidate_record_id"]==new_candidates[0]["record_id"]
    before=runner.LEDGER.read_text(); runner.run_once(now=datetime(2026,7,26,15,20,tzinfo=timezone.utc)); assert runner.LEDGER.read_text()==before


def test_runner_cross_date_reschedule_replaces_forecast_once(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    old_start="2026-07-26T14:00:00Z"; new_start="2026-07-27T16:00:00Z"
    old_forecast=forecast(old_start); append_ledger_record(runner.LEDGER,old_forecast)
    calls=[]
    def state(date): calls.append(date); return ({"ps:10":1600.0,"ps:11":1400.0},{"ps:10":5,"ps:11":4},"2026-07-26")
    monkeypatch.setattr(runner,"reconstruct_d1_state",state)
    event={"id":3,"home":"A","away":"B","date":new_start}
    odds={"bookmakers":{"Bet365":[{"name":"ML","updatedAt":"new","odds":[{"home":"1.8","away":"2.1"}]}]}}
    def fake(url,**kwargs):
        if "upcoming" in url:return [fixture(new_start)]
        if "leagues" in url:return [{"name":"Valorant","slug":"v"}]
        if "events" in url:return [event]
        return odds
    monkeypatch.setattr(runner,"get_json",fake)
    runner.run_once(now=datetime(2026,7,27,15,tzinfo=timezone.utc))
    state=ledger_rows(runner.LEDGER); replacements=[r for r in state if r["record_type"]=="forecast_generated" and r["record_id"]!=old_forecast["record_id"]]
    assert calls==["2026-07-27"] and len(replacements)==1
    assert sum(r["record_type"]=="reschedule" for r in state)==1
    assert sum(r["record_type"]=="forecast_generated" for r in state)==2
    assert next(r for r in state if r["record_id"]==old_forecast["record_id"])==old_forecast
    assert replacements[0]["state_through_date"]=="2026-07-26"
    assert replacements[0]["supersedes_forecast_id"]==old_forecast["record_id"]
    assert parse_utc(replacements[0]["generated_at_utc"]) < parse_utc(new_start)
    assert sum(r["record_type"]=="forecast_superseded" for r in state)==1
    superseded={r["forecast_record_id"] for r in state if r["record_type"]=="forecast_superseded"}
    active={r["record_id"] for r in state if r["record_type"]=="forecast_generated"}-superseded
    assert active=={replacements[0]["record_id"]}
    before=runner.LEDGER.read_text(); runner.run_once(now=datetime(2026,7,27,15,tzinfo=timezone.utc)); assert runner.LEDGER.read_text()==before


def test_runner_late_cross_date_reschedule_is_terminal(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    old_start="2026-07-26T14:00:00Z"; new_start="2026-07-27T10:00:00Z"
    old_forecast=forecast(old_start); old_candidate=candidate(old_start,"2026-07-26T13:00:00Z")
    old_primary=primary(old_start,old_candidate["record_id"])
    for record in (old_forecast,old_candidate,old_primary): append_ledger_record(runner.LEDGER,record)
    lookups=[]
    def fake(url,**kwargs):
        if "upcoming" in url:return [fixture(new_start)]
        lookups.append(url); return {}
    monkeypatch.setattr(runner,"get_json",fake)
    runner.run_once(now=datetime(2026,7,27,11,tzinfo=timezone.utc))
    state=ledger_rows(runner.LEDGER)
    assert sum(r["record_type"]=="forecast_generated" for r in state)==1
    assert sum(r["record_type"]=="reschedule" for r in state)==1
    assert sum(r["record_type"]=="forecast_superseded" for r in state)==1
    assert sum(r["record_type"]=="primary_market_superseded" for r in state)==1
    assert next(r for r in state if r["record_id"]==old_forecast["record_id"])==old_forecast
    assert next(r for r in state if r["record_id"]==old_candidate["record_id"])==old_candidate
    assert next(r for r in state if r["record_id"]==old_primary["record_id"])==old_primary
    assert any(r.get("reason")=="missed_rescheduled_forecast" for r in state)
    assert not any(r["record_type"]=="outcome_attached" for r in state)
    assert not lookups
    assert runner.eligible_completed_count(state)==0
    before=runner.LEDGER.read_text(); runner.run_once(now=datetime(2026,7,27,11,tzinfo=timezone.utc)); assert runner.LEDGER.read_text()==before


def test_runner_cancellation_and_invalid_winner_are_terminal(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    start="2026-07-26T13:00:00Z"; frozen=forecast(start); append_ledger_record(runner.LEDGER,frozen)
    cancelled=fixture(start,status="cancelled")
    def fake(url,**kwargs): return [] if "upcoming" in url else cancelled
    monkeypatch.setattr(runner,"get_json",fake)
    runner.run_once(now=datetime(2026,7,26,14,tzinfo=timezone.utc))
    state=ledger_rows(runner.LEDGER)
    assert sum(r["record_type"]=="terminal_exclusion" for r in state)==1
    assert not any(r["record_type"]=="outcome_attached" for r in state)
    assert runner.eligible_completed_count(state)==0
    before=runner.LEDGER.read_text(); runner.run_once(now=datetime(2026,7,26,14,tzinfo=timezone.utc)); assert runner.LEDGER.read_text()==before


def test_runner_finished_invalid_winner_is_terminal_but_running_is_not(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    start="2026-07-26T13:00:00Z"; append_ledger_record(runner.LEDGER,forecast(start))
    response={"value":fixture(start,status="running")}
    monkeypatch.setattr(runner,"get_json",lambda url,**kwargs:[] if "upcoming" in url else response["value"])
    runner.run_once(now=datetime(2026,7,26,13,10,tzinfo=timezone.utc))
    assert not any(r["record_type"]=="terminal_exclusion" for r in ledger_rows(runner.LEDGER))
    response["value"]=fixture(start,status="finished",winner_id=999)
    runner.run_once(now=datetime(2026,7,26,14,tzinfo=timezone.utc))
    state=ledger_rows(runner.LEDGER)
    assert [r.get("reason") for r in state if r["record_type"]=="terminal_exclusion"]==["unresolved_or_invalid_result"]
    assert not any(r["record_type"]=="outcome_attached" for r in state)


def test_runner_post_start_first_discovery_is_terminal(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    past=fixture("2026-07-26T13:00:00Z")
    monkeypatch.setattr(runner,"get_json",lambda url,**kwargs:[past] if "upcoming" in url else {})
    runner.run_once(now=datetime(2026,7,26,14,tzinfo=timezone.utc))
    state=ledger_rows(runner.LEDGER)
    assert not any(r["record_type"]=="forecast_generated" for r in state)
    assert [r.get("reason") for r in state]==["missed_prospective_forecast"]
    before=runner.LEDGER.read_text(); runner.run_once(now=datetime(2026,7,26,14,tzinfo=timezone.utc)); assert runner.LEDGER.read_text()==before


def test_runner_dry_run_projects_all_lifecycle_actions_without_writes(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    monkeypatch.setattr(runner,"reconstruct_d1_state",lambda date:({}, {}, "2026-07-25"))
    start="2026-07-26T13:00:00Z"
    event={"id":2,"home":"A","away":"B","date":start}
    odds={"bookmakers":{"Bet365":[{"name":"ML","updatedAt":"u","odds":[{"home":"2.0","away":"2.0"}]}]}}
    def discovery(url,**kwargs):
        if "upcoming" in url:return [fixture(start)]
        if "leagues" in url:return [{"name":"Valorant","slug":"v"}]
        if "events" in url:return [event]
        return odds
    monkeypatch.setattr(runner,"get_json",discovery)
    result=runner.run_once(dry_run=True,now=datetime(2026,7,26,12,tzinfo=timezone.utc))
    assert {"forecast_generated","market_candidate"} <= set(result["action_types"])
    assert not runner.LEDGER.exists()
    assert not (runner.ROOT/"data/raw").exists()

    frozen=forecast(start); stored_candidate=candidate(start,"2026-07-26T12:00:00Z")
    for record in (frozen,stored_candidate): append_ledger_record(runner.LEDGER,record)
    completed=fixture(start,status="finished",winner_id=10)
    monkeypatch.setattr(runner,"get_json",lambda url,**kwargs:[] if "upcoming" in url else completed)
    original=runner.LEDGER.read_text()
    result=runner.run_once(dry_run=True,now=datetime(2026,7,26,13,10,tzinfo=timezone.utc))
    assert {"primary_market_selected","outcome_attached"} <= set(result["action_types"])
    assert runner.LEDGER.read_text()==original

    runner.LEDGER=tmp_path/"reschedule.jsonl"
    old=forecast("2026-07-26T14:00:00Z"); append_ledger_record(runner.LEDGER,old)
    new_start="2026-07-27T16:00:00Z"
    monkeypatch.setattr(runner,"get_json",lambda url,**kwargs:[fixture(new_start)] if "upcoming" in url else [])
    original=runner.LEDGER.read_text()
    result=runner.run_once(dry_run=True,now=datetime(2026,7,27,14,tzinfo=timezone.utc))
    assert {"reschedule","forecast_superseded","forecast_generated"} <= set(result["action_types"])
    assert runner.LEDGER.read_text()==original

    runner.LEDGER=tmp_path/"late.jsonl"
    monkeypatch.setattr(runner,"get_json",lambda url,**kwargs:[fixture("2026-07-27T10:00:00Z")] if "upcoming" in url else [])
    result=runner.run_once(dry_run=True,now=datetime(2026,7,27,11,tzinfo=timezone.utc))
    assert "terminal_exclusion" in result["action_types"] and not runner.LEDGER.exists()


def test_status_is_operational_only_with_completed_records(monkeypatch, tmp_path):
    configure(monkeypatch,tmp_path)
    start="2026-07-26T13:00:00Z"; frozen=forecast(start); stored=candidate(start,"2026-07-26T12:00:00Z")
    records=(frozen,stored,primary(start,stored["record_id"]),
             {"record_id":"outcome:1","record_type":"outcome_attached","pandascore_match_id":"1","team_a_won":True},
             {"record_id":"reschedule:2:x","record_type":"reschedule","pandascore_match_id":"2"},
             {"record_id":"terminal:3:cancelled","record_type":"terminal_exclusion","pandascore_match_id":"3","reason":"cancelled"})
    for record in records: append_ledger_record(runner.LEDGER,record)
    result=runner.status()
    assert result["eligible_completed"]==1 and result["target"]==30
    prohibited=("accuracy","win","loss","brier","calibration","disagreement","roi","profit","bet")
    assert not any(term in key.casefold() for key in result for term in prohibited)
