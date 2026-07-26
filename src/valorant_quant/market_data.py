"""Minimal immutable prospective-market snapshot utilities; no betting logic."""
from __future__ import annotations
import hashlib, json, re, unicodedata
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


def normalized_team_name(name: str) -> str:
    """A conservative comparison key, never an alias or a team-ID assertion."""
    value=unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def exact_fixture_matches(
    fixtures_a: list[dict[str, Any]], fixtures_b: list[dict[str, Any]], *, time_tolerance_seconds: int = 7200
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return only unambiguous, unordered exact-name fixture matches within a time tolerance.

    This is intentionally strict: aliases, one-to-many candidates, and missing/invalid
    UTC timestamps remain unresolved for manual review.
    """
    def key(fixture: dict[str, Any]) -> tuple[str, str] | None:
        try:
            teams=sorted((normalized_team_name(fixture["team_a"]), normalized_team_name(fixture["team_b"])))
        except (KeyError, TypeError):
            return None
        return tuple(teams) if all(teams) and teams[0] != teams[1] else None

    def timestamp(fixture: dict[str, Any]) -> datetime | None:
        try:
            return datetime.fromisoformat(fixture["scheduled_start_utc"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            return None

    matched=[]
    for left in fixtures_a:
        left_key,left_time=key(left),timestamp(left)
        if not left_key or not left_time: continue
        candidates=[right for right in fixtures_b if key(right)==left_key and (right_time:=timestamp(right)) and abs((left_time-right_time).total_seconds())<=time_tolerance_seconds]
        if len(candidates)==1:
            right=candidates[0]
            reciprocal=[item for item in fixtures_a if key(item)==left_key and (item_time:=timestamp(item)) and abs((timestamp(right)-item_time).total_seconds())<=time_tolerance_seconds]
            if len(reciprocal)==1: matched.append((left,right))
    return matched


def unique_historical_team_ids(rows: list[dict[str, Any]], *, name_field: str = "team_name", id_field: str = "team_id") -> dict[str, str]:
    """Map only normalized names that point to exactly one historical ID."""
    candidates: dict[str, set[str]]={}
    for row in rows:
        name, identifier=row.get(name_field),row.get(id_field)
        if isinstance(name,str) and identifier is not None:
            candidates.setdefault(normalized_team_name(name),set()).add(str(identifier))
    return {name:next(iter(ids)) for name,ids in candidates.items() if name and len(ids)==1}
