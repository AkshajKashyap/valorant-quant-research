"""Prior-date map-pool and roster-state features; never current-series inputs."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
from valorant_quant.milestone1 import build_match_table, read_csv
from valorant_quant.historical_features import build_historical_features

MAP_FEATURES=["elo_diff","map_mean_elo_diff","map_max_elo_diff","map_min_elo_diff","map_elo_std_diff","map_history_count_diff","min_map_history_count"]
ROSTER_FEATURES=MAP_FEATURES+["roster_last_size_diff","roster_continuity_diff","roster_recent_unique_diff","any_missing_roster_history"]

def _linked_raw(canonical: pd.DataFrame, raw: Path) -> tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    source,_=build_match_table(raw); source=source.rename(columns={"Source Match ID":"match_id","Year":"source_partition_year"}); source=source[source.match_id.notna()].drop_duplicates("match_id"); source["match_id"]=source.match_id.astype("string"); canonical=canonical.copy();canonical["match_id"]=canonical.match_id.astype("string")
    base=canonical.merge(source[["match_id","Tournament","Match Type","Match Name","Team A","Team B"]],on="match_id",validate="one_to_one")
    maps=[]; overview=[]
    for year in range(2021,2027):
        m=read_csv(raw/f"vct_{year}/matches/maps_scores.csv");m["source_partition_year"]=str(year);maps.append(m)
        o=read_csv(raw/f"vct_{year}/matches/overview.csv");o["source_partition_year"]=str(year);overview.append(o)
    base["source_partition_year"]=base.source_partition_year.astype("string")
    key=["source_partition_year","Tournament","Stage","Match Type","Match Name","Team A","Team B"]
    return base,base.merge(pd.concat(maps),on=key,how="left"),base.merge(pd.concat(overview).query("Side == 'both'"),on=["source_partition_year","Tournament","Stage","Match Type","Match Name"],how="left")

def build_valorant_features(canonical: pd.DataFrame, raw: Path) -> tuple[pd.DataFrame,dict[str,int]]:
    historical=build_historical_features(canonical)
    base,maps,overview=_linked_raw(historical,raw); base=base.copy();base["match_date"]=pd.to_datetime(base.match_date)
    # Per-series roster union, mapped only through the series' own historical team identity.
    team_to_id=pd.concat([base[["match_id","team_a_name","team_a_id"]].rename(columns={"team_a_name":"Team","team_a_id":"team_id"}),base[["match_id","team_b_name","team_b_id"]].rename(columns={"team_b_name":"Team","team_b_id":"team_id"})])
    roster=overview.merge(team_to_id,on=["match_id","Team"],how="inner").groupby(["match_id","team_id"],as_index=False).Player.agg(lambda x:frozenset(x.dropna()))
    roster_by_match=defaultdict(list)
    for r in roster.itertuples(index=False): roster_by_match[r.match_id].append((str(r.team_id),r.Player))
    maps=maps[maps.Map.notna()].copy();maps["a_score_num"]=pd.to_numeric(maps["Team A Score"],errors="coerce");maps["b_score_num"]=pd.to_numeric(maps["Team B Score"],errors="coerce")
    maps_by_match=defaultdict(list)
    for r in maps.dropna(subset=["a_score_num","b_score_num"]).itertuples(index=False): maps_by_match[r.match_id].append((str(r.Map),float(r.a_score_num)>float(r.b_score_num)))
    ratings=defaultdict(dict);map_counts=defaultdict(dict);roster_days=defaultdict(list); rows=[]
    for date,day in base.sort_values(["match_date","match_id"]).groupby("match_date",sort=True):
        frozen_r={t:v.copy() for t,v in ratings.items()}; frozen_c={t:v.copy() for t,v in map_counts.items()}; frozen_roster={t:v.copy() for t,v in roster_days.items()}
        map_updates=[]; roster_updates=defaultdict(set)
        def pool(team):
            values=list(frozen_r.get(team,{}).values()); counts=list(frozen_c.get(team,{}).values())
            vals=np.array(values if values else [1500.]); return vals.mean(),vals.max(),vals.min(),vals.std(),sum(c>0 for c in counts)
        def rost(team):
            hist=frozen_roster.get(team,[])
            if not hist:return 5.,1.,5.,1
            last=hist[-1]; continuity=len(last&hist[-2])/len(last|hist[-2]) if len(hist)>1 and last|hist[-2] else 1.
            unique=len(set().union(*hist[-5:]));return float(len(last)),continuity,float(unique),0
        for row in day.itertuples(index=False):
            a,b=str(row.team_a_id),str(row.team_b_id);ap,bp=pool(a),pool(b);ar,br=rost(a),rost(b)
            rows.append({**row._asdict(),"map_mean_elo_diff":ap[0]-bp[0],"map_max_elo_diff":ap[1]-bp[1],"map_min_elo_diff":ap[2]-bp[2],"map_elo_std_diff":ap[3]-bp[3],"map_history_count_diff":ap[4]-bp[4],"min_map_history_count":min(ap[4],bp[4]),"roster_last_size_diff":ar[0]-br[0],"roster_continuity_diff":ar[1]-br[1],"roster_recent_unique_diff":ar[2]-br[2],"any_missing_roster_history":int(ar[3] or br[3])})
            for map_name,a_won in maps_by_match[row.match_id]: map_updates.append((a,b,map_name,a_won))
            for team,lineup in roster_by_match[row.match_id]: roster_updates[team].update(lineup)
        for a,b,map_name,a_won in map_updates:
            ra=frozen_r.get(a,{}).get(map_name,1500.);rb=frozen_r.get(b,{}).get(map_name,1500.);p=1/(1+10**((rb-ra)/400));d=64*(float(a_won)-p)
            ratings[a][map_name]=ra+d;ratings[b][map_name]=rb-d;map_counts[a][map_name]=frozen_c.get(a,{}).get(map_name,0)+1;map_counts[b][map_name]=frozen_c.get(b,{}).get(map_name,0)+1
        for team,lineup in roster_updates.items(): roster_days[team].append(frozenset(lineup))
    out=pd.DataFrame(rows);out.match_date=pd.to_datetime(out.match_date).dt.date.astype("string")
    audit={"canonical_rows":len(base),"series_with_maps":int(base.match_id.isin(maps.match_id).sum()),"series_with_roster":int(base.match_id.isin(roster.match_id).sum()),"map_rows":len(maps)}
    return out.sort_values(["match_date","match_id"]).reset_index(drop=True),audit
