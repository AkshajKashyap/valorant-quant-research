"""Rolling-origin logistic baselines over leakage-safe historical features."""

from __future__ import annotations

import argparse, json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from valorant_quant.historical_features import FEATURES_ELO, FEATURES_FULL, FEATURES_RECENT, build_historical_features
from valorant_quant.milestone2_5 import date_bootstrap, per_match_losses


FOLDS = (
    ("fold_1_2022_h1", "2022-01-01", "2022-07-01"),
    ("fold_2_2022_h2", "2022-07-01", "2023-01-01"),
    ("fold_3_2023", "2023-01-01", "2024-01-01"),
    ("fold_4_2024_available", "2024-01-01", "2025-01-01"),
)


def fold_assignments(features: pd.DataFrame) -> list[tuple[str, pd.DataFrame, pd.DataFrame]]:
    dates = pd.to_datetime(features.match_date)
    assignments = []
    for name, start, end in FOLDS:
        train = features.loc[dates < pd.Timestamp(start)].copy()
        evaluate = features.loc[(dates >= pd.Timestamp(start)) & (dates < pd.Timestamp(end))].copy()
        assignments.append((name, train, evaluate))
    return assignments


def _pipeline() -> Pipeline:
    return Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler()), ("model", LogisticRegression(C=1.0, max_iter=1000))])


def _model_metrics(predictions: pd.DataFrame, probability_column: str) -> dict[str, Any]:
    p = predictions[probability_column].clip(1e-15, 1 - 1e-15).to_numpy(float); y = predictions.team_a_won.to_numpy(float)
    return {"n_matches": int(len(predictions)), "log_loss": float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()), "brier_score": float(((p-y)**2).mean()), "accuracy": float(((p >= .5) == y.astype(bool)).mean()), "average_confidence": float(np.maximum(p, 1-p).mean())}


def run_rolling(features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_predictions, metric_rows, coefficients = [], [], []
    specifications = {"elo_logistic": FEATURES_ELO, "elo_recent_logistic": FEATURES_RECENT, "full_historical_logistic": FEATURES_FULL}
    for fold, train, evaluation in fold_assignments(features):
        if evaluation.empty:
            continue
        base = evaluation[["match_id", "match_date", "year", "team_a_won", "any_team_unseen", "raw_elo_probability_a"]].copy(); base["fold"] = fold; base["fifty_fifty_probability_a"] = .5
        for model_name, columns in specifications.items():
            pipeline = _pipeline(); pipeline.fit(train[columns], train.team_a_won)
            base[model_name + "_probability_a"] = pipeline.predict_proba(evaluation[columns])[:, 1]
            values = pipeline.named_steps["model"].coef_[0]
            coefficients.extend({"fold": fold, "model": model_name, "feature": feature, "standardized_coefficient": float(value)} for feature, value in zip(columns, values))
        all_predictions.append(base)
    predictions = pd.concat(all_predictions, ignore_index=True).sort_values(["match_date", "match_id"], kind="stable")
    for fold, group in predictions.groupby("fold", sort=False):
        for model, col in {"fifty_fifty": "fifty_fifty_probability_a", "raw_elo": "raw_elo_probability_a", "elo_logistic": "elo_logistic_probability_a", "elo_recent_logistic": "elo_recent_logistic_probability_a", "full_historical_logistic": "full_historical_logistic_probability_a"}.items(): metric_rows.append({"fold": fold, "model": model, **_model_metrics(group, col)})
    return predictions, pd.DataFrame(metric_rows), pd.DataFrame(coefficients)


def paired(predictions: pd.DataFrame, challenger: str, incumbent: str) -> dict[str, Any]:
    frame = predictions[["match_id", "match_date", "team_a_won", challenger, incumbent]].copy()
    y = frame.team_a_won.astype(float)
    for name, col in [("challenger", challenger), ("incumbent", incumbent)]:
        p = frame[col].clip(1e-15, 1-1e-15); frame[name+"_log"] = -(y*np.log(p)+(1-y)*np.log(1-p)); frame[name+"_brier"] = (p-y)**2
    frame["delta_log_loss"] = frame.challenger_log-frame.incumbent_log; frame["delta_brier"] = frame.challenger_brier-frame.incumbent_brier
    return date_bootstrap(frame)


def calibration(predictions: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for model, col in {"raw_elo":"raw_elo_probability_a", "elo_logistic":"elo_logistic_probability_a", "full_historical_logistic":"full_historical_logistic_probability_a"}.items():
        p=predictions[col]; favorite=np.maximum(p,1-p); won=np.where(p>=.5,predictions.team_a_won,~predictions.team_a_won)
        bins=pd.cut(np.clip(favorite,np.nextafter(.5,1),1),np.linspace(.5,1,6),include_lowest=False)
        t=pd.DataFrame({"bin":bins,"p":favorite,"won":won}).groupby("bin",observed=False).agg(n_matches=("won","size"),mean_predicted=("p","mean"),observed_rate=("won","mean")).reset_index(); t["model"]=model; t["gap"]=t.observed_rate-t.mean_predicted; t["bin"]=t.bin.astype(str); rows.append(t)
    return pd.concat(rows,ignore_index=True)


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--canonical-matches",type=Path,required=True); parser.add_argument("--output-dir",type=Path,required=True); args=parser.parse_args()
    table=pd.read_csv(args.canonical_matches); table["team_a_won"]=table.team_a_won.astype("string").str.lower().map({"true":True,"false":False})
    if table.team_a_won.isna().any(): raise ValueError("team_a_won must be True or False")
    table["team_a_won"]=table.team_a_won.astype(bool)
    features=build_historical_features(table); predictions, per_fold, coefficients=run_rolling(features)
    aggregate=[]
    for model,col in {"fifty_fifty":"fifty_fifty_probability_a","raw_elo":"raw_elo_probability_a","elo_logistic":"elo_logistic_probability_a","elo_recent_logistic":"elo_recent_logistic_probability_a","full_historical_logistic":"full_historical_logistic_probability_a"}.items(): aggregate.append({"model":model,**_model_metrics(predictions,col)})
    comparisons={"raw_elo_vs_fifty_fifty":paired(predictions,"raw_elo_probability_a","fifty_fifty_probability_a"),"elo_logistic_vs_raw_elo":paired(predictions,"elo_logistic_probability_a","raw_elo_probability_a"),"full_vs_elo_logistic":paired(predictions,"full_historical_logistic_probability_a","elo_logistic_probability_a")}
    cold=[]
    for group, data in predictions.groupby("any_team_unseen"):
        for model,col in {"raw_elo":"raw_elo_probability_a","elo_logistic":"elo_logistic_probability_a","full_historical_logistic":"full_historical_logistic_probability_a"}.items(): cold.append({"group":"any_unseen" if group else "both_seen","model":model,**_model_metrics(data,col)})
    args.output_dir.mkdir(parents=True,exist_ok=True); features.to_csv(args.output_dir/"historical_feature_snapshot.csv",index=False); predictions.to_csv(args.output_dir/"rolling_oof_predictions.csv",index=False); per_fold.to_csv(args.output_dir/"per_fold_metrics.csv",index=False); pd.DataFrame(aggregate).to_csv(args.output_dir/"aggregate_metrics.csv",index=False); coefficients.to_csv(args.output_dir/"coefficients.csv",index=False); calibration(predictions).to_csv(args.output_dir/"calibration.csv",index=False); pd.DataFrame(cold).to_csv(args.output_dir/"cold_start_metrics.csv",index=False); (args.output_dir/"paired_bootstrap.json").write_text(json.dumps(comparisons,indent=2,sort_keys=True)+"\n")


if __name__ == "__main__": main()
