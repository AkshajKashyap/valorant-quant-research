"""Rolling-origin map-pool and roster-state ablation."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
from valorant_quant.milestone3 import _model_metrics,_pipeline,calibration,fold_assignments,paired
from valorant_quant.valorant_features import MAP_FEATURES,ROSTER_FEATURES,build_valorant_features

def run(features:pd.DataFrame):
    pred=[];metrics=[]
    for fold,train,evaluation in fold_assignments(features):
        if evaluation.empty:continue
        x=evaluation[["match_id","match_date","year","team_a_won","any_team_unseen","raw_elo_probability_a"]].copy();x["fold"]=fold;x["fifty_fifty_probability_a"]=.5
        for name,cols in {"map_logistic":MAP_FEATURES,"map_roster_logistic":ROSTER_FEATURES}.items():
            p=_pipeline();p.fit(train[cols],train.team_a_won);x[name+"_probability_a"]=p.predict_proba(evaluation[cols])[:,1]
        pred.append(x)
    p=pd.concat(pred).sort_values(["match_date","match_id"]).reset_index(drop=True)
    cols={"fifty_fifty":"fifty_fifty_probability_a","raw_elo":"raw_elo_probability_a","map_logistic":"map_logistic_probability_a","map_roster_logistic":"map_roster_logistic_probability_a"}
    for fold,g in p.groupby("fold",sort=False):
        for name,col in cols.items():metrics.append({"fold":fold,"model":name,**_model_metrics(g,col)})
    aggregate=pd.DataFrame([{"model":n,**_model_metrics(p,c)}for n,c in cols.items()])
    pairs={"map_vs_raw_elo":paired(p,"map_logistic_probability_a","raw_elo_probability_a"),"map_roster_vs_map":paired(p,"map_roster_logistic_probability_a","map_logistic_probability_a"),"map_roster_vs_raw_elo":paired(p,"map_roster_logistic_probability_a","raw_elo_probability_a")}
    calibration_input=p.rename(columns={"map_logistic_probability_a":"elo_logistic_probability_a","map_roster_logistic_probability_a":"full_historical_logistic_probability_a"})
    return p,pd.DataFrame(metrics),aggregate,pairs,calibration(calibration_input)

def main():
    a=argparse.ArgumentParser();a.add_argument("--canonical-matches",type=Path,required=True);a.add_argument("--raw-files",type=Path,required=True);a.add_argument("--output-dir",type=Path,required=True);args=a.parse_args()
    table=pd.read_csv(args.canonical_matches);table.team_a_won=table.team_a_won.astype("string").str.lower().map({"true":True,"false":False}).astype(bool)
    features,audit=build_valorant_features(table,args.raw_files);p,fold,aggregate,pairs,cal=run(features);args.output_dir.mkdir(parents=True,exist_ok=True)
    features.to_csv(args.output_dir/"valorant_feature_snapshot.csv",index=False);p.to_csv(args.output_dir/"rolling_oof_predictions.csv",index=False);fold.to_csv(args.output_dir/"per_fold_metrics.csv",index=False);aggregate.to_csv(args.output_dir/"aggregate_metrics.csv",index=False);cal.to_csv(args.output_dir/"calibration.csv",index=False);(args.output_dir/"paired_bootstrap.json").write_text(json.dumps(pairs,indent=2,sort_keys=True)+"\n");(args.output_dir/"feasibility_audit.json").write_text(json.dumps(audit,indent=2,sort_keys=True)+"\n")
if __name__=="__main__":main()
