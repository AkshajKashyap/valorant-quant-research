"""Append-only, deterministic utilities for the Milestone 6 forward ledger."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PRIMARY_BOOKMAKER = "Bet365"
TARGET_LEAD_MINUTES = 60.0
MIN_LEAD_MINUTES = 45.0
MAX_LEAD_MINUTES = 75.0
MATERIAL_RESCHEDULE_MINUTES = 60.0


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def lead_time_minutes(captured_at_utc: str, scheduled_start_utc: str) -> float:
    return (parse_utc(scheduled_start_utc)-parse_utc(captured_at_utc)).total_seconds()/60


def select_primary_snapshot(observations: list[dict[str, Any]], scheduled_start_utc: str) -> dict[str, Any] | None:
    """Choose the valid pre-start Bet365 record closest to T-60, deterministically."""
    candidates=[]
    for observation in observations:
        if observation.get("bookmaker") != PRIMARY_BOOKMAKER: continue
        if observation.get("market_type") != "ML": continue
        try:
            lead=lead_time_minutes(observation["captured_at_utc"],scheduled_start_utc)
            valid_prices=float(observation["team_a_decimal_odds"]) > 1 and float(observation["team_b_decimal_odds"]) > 1
        except (KeyError, TypeError, ValueError):
            continue
        if MIN_LEAD_MINUTES <= lead <= MAX_LEAD_MINUTES and valid_prices:
            candidates.append((abs(lead-TARGET_LEAD_MINUTES), parse_utc(observation["captured_at_utc"]), observation))
    if not candidates: return None
    selected=min(candidates, key=lambda value:(value[0],value[1],str(value[2].get("raw_snapshot_hash",""))))[2].copy()
    selected["lead_time_minutes"]=lead_time_minutes(selected["captured_at_utc"],scheduled_start_utc)
    selected["scheduled_start_utc"]=scheduled_start_utc
    return selected


def primary_snapshot_current(snapshot_start_utc: str, current_start_utc: str) -> bool:
    """A change over 60 minutes supersedes, rather than rewrites, the snapshot."""
    return abs((parse_utc(current_start_utc)-parse_utc(snapshot_start_utc)).total_seconds()) <= MATERIAL_RESCHEDULE_MINUTES*60


def true_cold_start(prior_eligible_matches: int) -> bool:
    return prior_eligible_matches == 0


def mechanically_eligible(fixture: dict[str, Any]) -> bool:
    """Protocol eligibility deliberately excludes model/market-disagreement magnitude."""
    return all(bool(fixture.get(field)) for field in (
        "professional", "two_actual_teams", "future_at_prediction", "valid_elo_state",
        "unambiguous_reconciliation", "bet365_two_sided_ml", "valid_primary_snapshot",
        "forecast_before_start",
    ))


def append_ledger_record(path: Path, record: dict[str, Any]) -> None:
    """Append one immutable JSONL event and reject duplicate event IDs or secrets."""
    forbidden=("token", "api_key", "authorization", "password", "secret")
    if "record_id" not in record or "record_type" not in record: raise ValueError("record_id and record_type are required")
    serialized=json.dumps(record, sort_keys=True, separators=(",",":"), ensure_ascii=False)
    if any(word in key.casefold() for key in record for word in forbidden): raise ValueError("Secrets are forbidden in ledger records")
    if path.exists():
        existing={json.loads(line)["record_id"] for line in path.read_text().splitlines() if line}
        if record["record_id"] in existing: raise ValueError("Duplicate prospective record")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle: handle.write(serialized+"\n")


def lifecycle(records: list[dict[str, Any]], match_id: str) -> set[str]:
    return {r["record_type"] for r in records if str(r.get("pandascore_match_id")) == str(match_id)}


def reschedule_requires_new_forecast(old_start: str, new_start: str) -> bool:
    """Only a material move across calendar date can use a new D-1 state."""
    return not primary_snapshot_current(old_start,new_start) and parse_utc(old_start).date()!=parse_utc(new_start).date()


def primary_selection_record(candidates: list[dict[str, Any]], scheduled_start_utc: str, match_id: str) -> dict[str, Any] | None:
    selected=select_primary_snapshot(candidates,scheduled_start_utc)
    if not selected:return None
    return {"record_id":f"primary:{match_id}:{selected['record_id']}","record_type":"primary_market_selected","pandascore_match_id":str(match_id),"candidate_record_id":selected["record_id"],"scheduled_start_utc":scheduled_start_utc}


def terminal_or_outcome(match: dict[str, Any], forecast: dict[str, Any], observed_at_utc: str) -> dict[str, Any] | None:
    """Classify PandaScore without changing any earlier ledger event."""
    mid=str(forecast["pandascore_match_id"]); base={"pandascore_match_id":mid,"observed_at_utc":observed_at_utc}
    teams=[x.get("opponent",{}) for x in match.get("opponents") or []]; ids={x.get("id") for x in teams}
    if match.get("status") not in {"finished","canceled","cancelled","abandoned"} and not match.get("forfeit"):
        return None
    if match.get("status")=="finished" and not match.get("forfeit") and match.get("winner_id") in ids:
        return base|{"record_id":f"outcome:{mid}","record_type":"outcome_attached","completion_status":"finished","winner_team_id":match["winner_id"],"team_a_won":match["winner_id"]==forecast["team_a_provider_id"],"forfeit":False}
    reason="cancelled" if match.get("status") in {"canceled","cancelled"} else "forfeit_excluded" if match.get("forfeit") else "unresolved_or_invalid_result"
    return base|{"record_id":f"terminal:{mid}:{reason}","record_type":"terminal_exclusion","terminal_status":match.get("status"),"reason":reason}


def eligible_completed_count(records: list[dict[str, Any]]) -> int:
    by_match={}
    for r in records: by_match.setdefault(str(r.get("pandascore_match_id")),set()).add(r["record_type"])
    return sum({"forecast_generated","primary_market_selected","outcome_attached"} <= types and "terminal_exclusion" not in types for types in by_match.values())
