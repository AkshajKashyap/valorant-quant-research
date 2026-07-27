"""One idempotent polling cycle for the pre-registered Milestone 6 ledger."""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import pandas as pd
from valorant_quant.elo import EloConfig, run_elo
from valorant_quant.elo import win_probability

from valorant_quant.market_data import exact_fixture_matches, two_way_probabilities, write_raw_snapshot
from valorant_quant.prospective import append_ledger_record, lead_time_minutes, select_primary_snapshot, primary_selection_record, primary_snapshot_current, terminal_or_outcome, eligible_completed_count

ROOT=Path(__file__).resolve().parents[2]
LEDGER=ROOT/"data/processed/milestone_6/prospective_ledger.jsonl"

def reconstruct_d1_state(fixture_date: str) -> tuple[dict[str,float],dict[str,int],str]:
    """Rebuild only from immutable canonical+eligible bridge rows strictly before D."""
    hist=pd.read_csv(ROOT/"data/processed/milestone_2/canonical_matches.csv",dtype={"team_a_id":"string","team_b_id":"string"})
    bridge=pd.read_csv(ROOT/"data/processed/milestone_5/pandascore_eligible_bridge_matches.csv",dtype={"team_a_id":"string","team_b_id":"string"})
    columns=["match_id","match_date","year","team_a_id","team_a_name","team_b_id","team_b_name","team_a_won","tournament_name","source_snapshot_id"]
    table=pd.concat([hist[columns],bridge[columns]],ignore_index=True)
    table=table.loc[table.match_date.astype(str)<fixture_date].copy()
    _,ratings=run_elo(table,EloConfig(k=64),allow_post_2024=True)
    counts=pd.concat([table.team_a_id,table.team_b_id]).value_counts().astype(int).to_dict()
    return ratings,counts,str(table.match_date.max()) if len(table) else "none"

def load_credentials() -> tuple[str,str]:
    for line in (ROOT/".env").read_text().splitlines() if (ROOT/".env").exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key,value=line.split("=",1); os.environ.setdefault(key.strip(),value.strip().strip("'\""))
    panda,odds=os.getenv("PANDASCORE_TOKEN"),os.getenv("ODDS_API_KEY")
    if not panda or not odds: raise RuntimeError("PANDASCORE_TOKEN and ODDS_API_KEY are required")
    return panda,odds

def get_json(url: str, *, headers: dict[str,str] | None=None, query: dict[str,str] | None=None):
    address=url+("?"+urlencode(query) if query else "")
    with urlopen(Request(address,headers=headers or {}),timeout=30) as response: return json.loads(response.read())

def read_ledger(path: Path | None=None) -> list[dict]:
    path = path or LEDGER
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []

def run_once(*, dry_run: bool=False, now: datetime | None=None) -> dict[str,int]:
    panda,odds=load_credentials(); now=now or datetime.now(timezone.utc); now_s=now.isoformat().replace("+00:00","Z")
    ledger=read_ledger(); existing={r["record_id"] for r in ledger}; actions=[]
    fixtures=get_json("https://api.pandascore.co/valorant/matches/upcoming",headers={"Authorization":f"Bearer {panda}"},query={"per_page":"100"})
    candidates=[]
    for x in fixtures:
        teams=[o.get("opponent",{}) for o in x.get("opponents") or []]
        if len(teams)!=2 or not x.get("scheduled_at"): continue
        lead=lead_time_minutes(now_s,x["scheduled_at"])
        if 45<=lead<=75: candidates.append((x,teams))
        # Schedule shifts are append-only audit events, never a rewrite.
        old=[r for r in ledger if str(r.get("pandascore_match_id"))==str(x["id"]) and r.get("scheduled_start_utc")]
        if not old and lead <= 0:
            key=f"terminal:{x['id']}:missed_prospective_forecast"
            if key not in existing:
                actions.append(({"record_id":key,"record_type":"terminal_exclusion","pandascore_match_id":str(x["id"]),"observed_at_utc":now_s,"terminal_status":"ineligible","reason":"missed_prospective_forecast"},None)); existing.add(key)
        if old and not primary_snapshot_current(old[0]["scheduled_start_utc"],x["scheduled_at"]):
            key=f"reschedule:{x['id']}:{x['scheduled_at']}"
            if key not in existing:
                actions.append(({"record_id":key,"record_type":"reschedule","pandascore_match_id":str(x['id']),"old_scheduled_start_utc":old[0]["scheduled_start_utc"],"scheduled_start_utc":x["scheduled_at"],"observed_at_utc":now_s},None)); existing.add(key)
                for primary in [r for r in old if r.get("record_type")=="primary_market_selected"]:
                    stale_key=f"primary_superseded:{primary['record_id']}:{x['scheduled_at']}"
                    if stale_key not in existing:
                        actions.append(({"record_id":stale_key,"record_type":"primary_market_superseded","pandascore_match_id":str(x["id"]),"primary_record_id":primary["record_id"],"scheduled_start_utc":x["scheduled_at"]},None)); existing.add(stale_key)
                old_forecasts=[r for r in old if r.get("record_type")=="forecast_generated"]
                if old_forecasts and old_forecasts[0]["scheduled_start_utc"][:10]!=x["scheduled_at"][:10]:
                    prior=old_forecasts[0]; replacement=f"forecast:{x['id']}:{x['scheduled_at']}"
                    supersede={"record_id":f"forecast_superseded:{prior['record_id']}:{x['scheduled_at']}","record_type":"forecast_superseded","pandascore_match_id":str(x['id']),"forecast_record_id":prior["record_id"],"scheduled_start_utc":x["scheduled_at"]}
                    actions.append((supersede,None)); existing.add(supersede["record_id"])
                    if now < __import__('valorant_quant.prospective',fromlist=['parse_utc']).parse_utc(x["scheduled_at"]) and replacement not in existing:
                        ratings,counts,state_date=reconstruct_d1_state(x["scheduled_at"][:10]); a,b=teams; ai,bi=f"ps:{a['id']}",f"ps:{b['id']}"; ra,rb=ratings.get(ai,1500.),ratings.get(bi,1500.); pa=win_probability(ra,rb)
                        actions.append(({"record_id":replacement,"record_type":"forecast_generated","supersedes_forecast_id":prior["record_id"],"generated_at_utc":now_s,"pandascore_match_id":str(x['id']),"scheduled_start_utc":x["scheduled_at"],"team_a":a["name"],"team_b":b["name"],"team_a_provider_id":a["id"],"team_b_provider_id":b["id"],"team_a_identity":ai,"team_b_identity":bi,"team_a_prior_eligible_matches":counts.get(ai,0),"team_b_prior_eligible_matches":counts.get(bi,0),"elo_a":ra,"elo_b":rb,"p_team_a_wins":pa,"p_team_b_wins":1-pa,"state_through_date":state_date,"model_version":"raw_elo_daily_batched_k64_bridge_v1","k":64},None)); existing.add(replacement)
                    elif now >= __import__('valorant_quant.prospective',fromlist=['parse_utc']).parse_utc(x["scheduled_at"]):
                        late_key=f"terminal:{x['id']}:missed_rescheduled_forecast:{x['scheduled_at']}"
                        if late_key not in existing:
                            actions.append(({"record_id":late_key,"record_type":"terminal_exclusion","pandascore_match_id":str(x["id"]),"observed_at_utc":now_s,"terminal_status":"ineligible","reason":"missed_rescheduled_forecast","scheduled_start_utc":x["scheduled_at"]},None)); existing.add(late_key)
    # Poll only previously registered fixtures after their start; outcomes are append-only.
    superseded_forecast_ids={
        r.get("forecast_record_id") for r in ledger+[record for record,_ in actions]
        if r.get("record_type")=="forecast_superseded"
    }
    for forecast in [r for r in ledger if r.get("record_type")=="forecast_generated" and r.get("record_id") not in superseded_forecast_ids]:
        mid=str(forecast["pandascore_match_id"])
        if f"outcome:{mid}" in existing or any(r.get("record_type")=="terminal_exclusion" and str(r.get("pandascore_match_id"))==mid for r in ledger): continue
        schedule_updates=[
            r["scheduled_start_utc"] for r in ledger+[record for record,_ in actions]
            if str(r.get("pandascore_match_id"))==mid and r.get("record_type")=="reschedule"
        ]
        effective_start=schedule_updates[-1] if schedule_updates else forecast["scheduled_start_utc"]
        if now >= __import__('valorant_quant.prospective',fromlist=['parse_utc']).parse_utc(effective_start):
            result=get_json(f"https://api.pandascore.co/valorant/matches/{mid}",headers={"Authorization":f"Bearer {panda}"})
            action=terminal_or_outcome(result,forecast,now_s)
            if action and action["record_id"] not in existing: actions.append((action,None)); existing.add(action["record_id"])
    if candidates:
        leagues=get_json("https://api.odds-api.io/v3/leagues",query={"apiKey":odds,"sport":"esports"})
        events=[]
        for league in leagues:
            if "valorant" in league.get("name","").casefold():
                events+=get_json("https://api.odds-api.io/v3/events",query={"apiKey":odds,"sport":"esports","league":league["slug"],"status":"pending"})
        left=[{"id":str(x["id"]),"team_a":t[0].get("name"),"team_b":t[1].get("name"),"scheduled_start_utc":x["scheduled_at"]} for x,t in candidates]
        right=[{"id":str(x["id"]),"team_a":x.get("home"),"team_b":x.get("away"),"scheduled_start_utc":x.get("date")} for x in events]
        for pf,of in exact_fixture_matches(left,right):
            source=next((x for x,t in candidates if str(x["id"])==pf["id"]),None)
            teams=[o["opponent"] for o in source.get("opponents",[])] if source else []
            forecast_id=f"forecast:{pf['id']}:{pf['scheduled_start_utc']}"
            active_forecast_exists=any(
                str(r.get("pandascore_match_id"))==pf["id"]
                and r.get("record_type")=="forecast_generated"
                and r.get("record_id") not in superseded_forecast_ids
                for r in ledger
            ) or any(
                str(r.get("pandascore_match_id"))==pf["id"] and r.get("record_type")=="forecast_generated"
                for r,_ in actions
            )
            if source and not active_forecast_exists and forecast_id not in existing and now < __import__('valorant_quant.prospective',fromlist=['parse_utc']).parse_utc(pf["scheduled_start_utc"]):
                ratings,counts,state_date=reconstruct_d1_state(pf["scheduled_start_utc"][:10])
                a,b=teams[0],teams[1]; ai,bi=f"ps:{a['id']}",f"ps:{b['id']}"; ra,rb=ratings.get(ai,1500.0),ratings.get(bi,1500.0); pa=win_probability(ra,rb)
                actions.append(({"record_id":forecast_id,"record_type":"forecast_generated","generated_at_utc":now_s,"pandascore_match_id":pf["id"],"odds_api_event_id":of["id"],"scheduled_start_utc":pf["scheduled_start_utc"],"team_a":a["name"],"team_b":b["name"],"team_a_provider_id":a["id"],"team_b_provider_id":b["id"],"team_a_identity":ai,"team_b_identity":bi,"team_a_prior_eligible_matches":counts.get(ai,0),"team_b_prior_eligible_matches":counts.get(bi,0),"elo_a":ra,"elo_b":rb,"p_team_a_wins":pa,"p_team_b_wins":1-pa,"state_through_date":state_date,"model_version":"raw_elo_daily_batched_k64_bridge_v1","k":64},None)); existing.add(forecast_id)
            event=get_json("https://api.odds-api.io/v3/odds",query={"apiKey":odds,"eventId":of["id"],"bookmakers":"Bet365"})
            for market in event.get("bookmakers",{}).get("Bet365",[]):
                quote=(market.get("odds") or [{}])[0]
                if market.get("name")!="ML" or not quote.get("home") or not quote.get("away"): continue
                digest=hashlib.sha256(json.dumps(event,sort_keys=True,separators=(",",":")).encode()).hexdigest()
                key=f"candidate:{of['id']}:{market.get('updatedAt')}:{quote['home']}:{quote['away']}"
                if key not in existing:
                    probs=two_way_probabilities(float(quote['home']),float(quote['away']))
                    record={"record_id":key,"record_type":"market_candidate","captured_at_utc":now_s,"pandascore_match_id":pf["id"],"odds_api_event_id":of["id"],"scheduled_start_utc":of["scheduled_start_utc"],"lead_time_minutes":lead_time_minutes(now_s,of["scheduled_start_utc"]),"bookmaker":"Bet365","market_type":"ML","team_a_decimal_odds":quote['home'],"team_b_decimal_odds":quote['away'],"source_last_update_utc":market.get("updatedAt"),"raw_snapshot_hash":digest,**probs}
                    actions.append((record,event)); existing.add(key)
    # Progress stored candidates independently of the current discovery feed.
    projected=ledger+[record for record,_ in actions]
    for match_id in {str(r.get("pandascore_match_id")) for r in projected if r.get("record_type")=="market_candidate"}:
        match_records=[r for r in projected if str(r.get("pandascore_match_id"))==match_id]
        reschedules=[r for r in match_records if r.get("record_type")=="reschedule"]
        active_start=reschedules[-1]["scheduled_start_utc"] if reschedules else next(r["scheduled_start_utc"] for r in match_records if r.get("record_type")=="market_candidate")
        subset=[r for r in match_records if r.get("record_type")=="market_candidate" and r.get("scheduled_start_utc")==active_start]
        existing_primary={r.get("scheduled_start_utc") for r in match_records if r.get("record_type")=="primary_market_selected"}
        if subset and active_start not in existing_primary and lead_time_minutes(now_s,active_start)<45:
            primary=primary_selection_record(subset,active_start,match_id)
            if primary and primary["record_id"] not in existing:
                actions.append((primary,None)); existing.add(primary["record_id"])
    if not dry_run:
        for record,payload in actions:
            if record["record_id"] in {row["record_id"] for row in read_ledger()}:
                continue
            if payload is not None:
                try: _,digest=write_raw_snapshot(payload,ROOT/"data/raw/market_pilot_live/v1/milestone6_odds")
                except FileExistsError: digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()+b"\n").hexdigest()
                record["raw_snapshot_hash"]=digest
            try: append_ledger_record(LEDGER,record)
            except ValueError as error:
                if "Duplicate prospective record" not in str(error): raise
    return {"fixtures_discovered":len(fixtures),"window_candidates":len(candidates),"records_appended":0 if dry_run else len(actions),"would_append":len(actions) if dry_run else 0,"action_types":[record["record_type"] for record,_ in actions]}

def status() -> dict[str,int]:
    rows=read_ledger(); return {"target":30,"forecasts_frozen":sum(r["record_type"]=="forecast_generated" for r in rows),"primary_snapshots":sum(r["record_type"]=="primary_market_selected" for r in rows),"outcomes_attached":sum(r["record_type"]=="outcome_attached" for r in rows),"candidate_snapshots":sum(r["record_type"]=="market_candidate" for r in rows),"eligible_completed":eligible_completed_count(rows),"reschedules":sum(r["record_type"]=="reschedule" for r in rows),"superseded_forecasts":sum(r["record_type"]=="forecast_superseded" for r in rows),"superseded_primaries":sum(r["record_type"]=="primary_market_superseded" for r in rows),"terminal_exclusions":sum(r["record_type"]=="terminal_exclusion" for r in rows)}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--dry-run",action="store_true"); parser.add_argument("--status",action="store_true"); args=parser.parse_args()
    print(json.dumps(status() if args.status else run_once(dry_run=args.dry_run),sort_keys=True))
if __name__=="__main__": main()
