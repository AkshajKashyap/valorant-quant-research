"""Minimal immutable prospective-market snapshot utilities; no betting logic."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_NORMALIZED_FIELDS={"captured_at_utc","source","source_event_id","scheduled_start_utc","team_a","team_b","bookmaker","market_type","team_a_decimal_odds","team_b_decimal_odds","source_last_update_utc","raw_snapshot_hash"}

def two_way_probabilities(team_a_decimal_odds: float, team_b_decimal_odds: float) -> dict[str,float]:
    if team_a_decimal_odds <= 1 or team_b_decimal_odds <= 1: raise ValueError("Decimal odds must be greater than 1")
    raw_a,raw_b=1/team_a_decimal_odds,1/team_b_decimal_odds; total=raw_a+raw_b
    return {"team_a_raw_implied_probability":raw_a,"team_b_raw_implied_probability":raw_b,"team_a_no_vig_probability":raw_a/total,"team_b_no_vig_probability":raw_b/total}

def write_raw_snapshot(raw_response: Any, directory: Path, captured_at: datetime | None=None) -> tuple[Path,str]:
    """Write one canonical JSON response once; never overwrite a snapshot."""
    when=(captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload=json.dumps(raw_response,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()+b"\n"
    digest=hashlib.sha256(payload).hexdigest(); directory.mkdir(parents=True,exist_ok=True)
    path=directory/f"{when.strftime('%Y%m%dT%H%M%SZ')}_{digest[:12]}.json"
    if path.exists(): raise FileExistsError(f"Snapshot already exists: {path}")
    path.write_bytes(payload); return path,digest

def validate_normalized_record(record: dict[str,Any]) -> None:
    missing=REQUIRED_NORMALIZED_FIELDS-set(record)
    if missing: raise ValueError(f"Missing fields: {sorted(missing)}")
    probabilities=two_way_probabilities(float(record["team_a_decimal_odds"]),float(record["team_b_decimal_odds"]))
    if not abs(probabilities["team_a_no_vig_probability"]+probabilities["team_b_no_vig_probability"]-1)<1e-12: raise ValueError("No-vig probabilities must sum to one")
