"""Conservative PandaScore-to-frozen-Elo bridge; no market or betting actions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from valorant_quant.market_data import normalized_team_name, unique_historical_team_ids


def panda_to_elo_rows(matches: list[dict[str, Any]], *, cutoff_date: str, captured_at: datetime) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return eligible Elo rows, team mapping audit, and excluded-match audit.

    A forfeit is excluded conservatively.  Names that are not unique exact
    historical matches retain a provider-prefixed ID and cold-start at 1500.
    """
    eligible, excluded=[], []
    for match in matches:
        opponents=[x.get("opponent", {}) for x in match.get("opponents") or []]
        begin, end=match.get("begin_at"),match.get("end_at")
        ids=[x.get("id") for x in opponents]
        reason=None
        if match.get("status") != "finished": reason="not_finished"
        elif match.get("forfeit") is True: reason="forfeit_excluded"
        elif len(opponents) != 2 or any(x is None for x in ids) or ids[0] == ids[1]: reason="invalid_opponents"
        elif not begin or not end: reason="missing_begin_or_end"
        else:
            begin_time=pd.to_datetime(begin, utc=True, errors="coerce")
            end_time=pd.to_datetime(end, utc=True, errors="coerce")
            if pd.isna(begin_time) or pd.isna(end_time): reason="invalid_timestamp"
            elif begin_time.date().isoformat() <= cutoff_date: reason="pre_or_at_cutoff"
            elif end_time.to_pydatetime() > captured_at: reason="not_completed_at_capture"
            elif match.get("winner_id") not in ids: reason="invalid_winner"
            elif any(normalized_team_name(str(x.get("name", ""))) in {"", "tbd"} for x in opponents): reason="placeholder_team"
        if reason:
            excluded.append({"pandascore_match_id":match.get("id"),"reason":reason})
            continue
        a,b=opponents
        eligible.append({
            "match_id":f"pandascore:{match['id']}", "pandascore_match_id":match["id"],
            "match_date":pd.to_datetime(begin, utc=True).date().isoformat(),
            "year":pd.to_datetime(begin, utc=True).year,
            "pandascore_team_a_id":a["id"], "team_a_name":a["name"],
            "pandascore_team_b_id":b["id"], "team_b_name":b["name"],
            "team_a_won":match["winner_id"] == a["id"],
            "tournament_name":(match.get("tournament") or {}).get("name", ""),
            "source_snapshot_id":"pandascore_valorant_matches_past/2026-07-26",
            "forfeit":bool(match.get("forfeit")), "status":match.get("status"),
        })
    return pd.DataFrame(eligible), pd.DataFrame(), pd.DataFrame(excluded)


def apply_historical_identity(bridge: pd.DataFrame, historical: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use only unique normalized historical names; no inferred aliases."""
    source=[]
    for row in historical.itertuples(index=False):
        source.extend((
            {"team_name":row.team_a_name,"team_id":row.team_a_id},
            {"team_name":row.team_b_name,"team_id":row.team_b_id},
        ))
    lookup=unique_historical_team_ids(source)
    mappings=[]
    for panda_id, name in pd.concat([
        bridge[["pandascore_team_a_id","team_a_name"]].rename(columns={"pandascore_team_a_id":"pandascore_team_id","team_a_name":"pandascore_team_name"}),
        bridge[["pandascore_team_b_id","team_b_name"]].rename(columns={"pandascore_team_b_id":"pandascore_team_id","team_b_name":"pandascore_team_name"}),
    ]).drop_duplicates().itertuples(index=False):
        historical_id=lookup.get(normalized_team_name(str(name)))
        mappings.append({"pandascore_team_id":str(panda_id),"pandascore_team_name":name,"historical_team_id":historical_id,"match_method":"exact_normalized_name" if historical_id else "new_pandascore_team","ambiguity_flag":False})
    audit=pd.DataFrame(mappings)
    map_id=dict(zip(audit.pandascore_team_id, audit.historical_team_id.fillna("ps:"+audit.pandascore_team_id)))
    output=bridge.copy()
    output["team_a_id"]=output.pandascore_team_a_id.astype(str).map(map_id)
    output["team_b_id"]=output.pandascore_team_b_id.astype(str).map(map_id)
    return output, audit
