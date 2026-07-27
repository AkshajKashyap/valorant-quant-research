"""One idempotent polling cycle for the pre-registered Milestone 6 ledger."""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from valorant_quant.market_data import exact_fixture_matches, two_way_probabilities, write_raw_snapshot
from valorant_quant.prospective import append_ledger_record, lead_time_minutes, select_primary_snapshot

ROOT=Path(__file__).resolve().parents[2]
LEDGER=ROOT/"data/processed/milestone_6/prospective_ledger.jsonl"

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

def read_ledger(path: Path=LEDGER) -> list[dict]:
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
    if candidates:
        leagues=get_json("https://api.odds-api.io/v3/leagues",query={"apiKey":odds,"sport":"esports"})
        events=[]
        for league in leagues:
            if "valorant" in league.get("name","").casefold():
                events+=get_json("https://api.odds-api.io/v3/events",query={"apiKey":odds,"sport":"esports","league":league["slug"],"status":"pending"})
        left=[{"id":str(x["id"]),"team_a":t[0].get("name"),"team_b":t[1].get("name"),"scheduled_start_utc":x["scheduled_at"]} for x,t in candidates]
        right=[{"id":str(x["id"]),"team_a":x.get("home"),"team_b":x.get("away"),"scheduled_start_utc":x.get("date")} for x in events]
        for pf,of in exact_fixture_matches(left,right):
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
    if not dry_run:
        for record,payload in actions:
            _,digest=write_raw_snapshot(payload,ROOT/"data/raw/market_pilot_live/v1/milestone6_odds")
            record["raw_snapshot_hash"]=digest; append_ledger_record(LEDGER,record)
    return {"fixtures_discovered":len(fixtures),"window_candidates":len(candidates),"records_appended":0 if dry_run else len(actions),"would_append":len(actions) if dry_run else 0}

def status() -> dict[str,int]:
    rows=read_ledger(); return {"target":30,"forecasts_frozen":sum(r["record_type"]=="forecast_generated" for r in rows),"primary_snapshots":sum(r["record_type"]=="primary_market_selected" for r in rows),"outcomes_attached":sum(r["record_type"]=="outcome_attached" for r in rows),"candidate_snapshots":sum(r["record_type"]=="market_candidate" for r in rows)}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--dry-run",action="store_true"); parser.add_argument("--status",action="store_true"); args=parser.parse_args()
    print(json.dumps(status() if args.status else run_once(dry_run=args.dry_run),sort_keys=True))
if __name__=="__main__": main()
