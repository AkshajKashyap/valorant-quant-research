"""Strictly prior-date team-history features for Milestone 3."""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from valorant_quant.elo import DEFAULT_SCALE, INITIAL_RATING, win_probability


FEATURES_ELO = ["elo_diff"]
FEATURES_RECENT = ["elo_diff", "last_5_win_rate_diff", "last_10_win_rate_diff"]
FEATURES_FULL = FEATURES_RECENT + [
    "prior_match_count_diff", "min_prior_match_count", "days_since_last_match_diff", "any_missing_last_match_date",
]


def build_historical_features(matches: pd.DataFrame, k: float = 64.0) -> pd.DataFrame:
    """Create match features using states frozen before each calendar date."""
    frame = matches.copy()
    frame["match_date"] = pd.to_datetime(frame["match_date"])
    frame = frame.sort_values(["match_date", "match_id"], kind="stable")
    ratings: dict[str, float] = {}
    results: dict[str, list[bool]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    last_dates: dict[str, pd.Timestamp] = {}
    rows = []
    for date, day in frame.groupby("match_date", sort=True):
        frozen_ratings, frozen_results = ratings.copy(), {team: values.copy() for team, values in results.items()}
        frozen_counts, frozen_dates = counts.copy(), last_dates.copy()
        deltas: dict[str, float] = defaultdict(float)
        updates: list[tuple[str, str, bool]] = []
        for row in day.itertuples(index=False):
            a, b = str(row.team_a_id), str(row.team_b_id)
            ra, rb = frozen_ratings.get(a, INITIAL_RATING), frozen_ratings.get(b, INITIAL_RATING)
            a_history, b_history = frozen_results.get(a, []), frozen_results.get(b, [])
            a_count, b_count = frozen_counts.get(a, 0), frozen_counts.get(b, 0)
            a_days = (date - frozen_dates[a]).days if a in frozen_dates else None
            b_days = (date - frozen_dates[b]).days if b in frozen_dates else None
            p = win_probability(ra, rb, DEFAULT_SCALE)
            y = bool(row.team_a_won)
            delta = k * (float(y) - p)
            deltas[a] += delta; deltas[b] -= delta
            rows.append({
                **row._asdict(), "elo_rating_a_pre": ra, "elo_rating_b_pre": rb, "raw_elo_probability_a": p,
                "elo_diff": ra - rb,
                "last_5_win_rate_diff": (sum(a_history[-5:]) / len(a_history[-5:]) if a_history else .5) - (sum(b_history[-5:]) / len(b_history[-5:]) if b_history else .5),
                "last_10_win_rate_diff": (sum(a_history[-10:]) / len(a_history[-10:]) if a_history else .5) - (sum(b_history[-10:]) / len(b_history[-10:]) if b_history else .5),
                "prior_match_count_diff": a_count - b_count, "min_prior_match_count": min(a_count, b_count),
                "days_since_last_match_diff": None if a_days is None or b_days is None else a_days - b_days,
                "any_missing_last_match_date": int(a_days is None or b_days is None),
                "any_team_unseen": int(a_count == 0 or b_count == 0),
            })
            updates.append((a, b, y))
        for team, change in deltas.items(): ratings[team] = frozen_ratings.get(team, INITIAL_RATING) + change
        for a, b, a_won in updates:
            results[a].append(a_won); results[b].append(not a_won)
            counts[a] += 1; counts[b] += 1; last_dates[a] = date; last_dates[b] = date
    output = pd.DataFrame(rows)
    output["match_date"] = pd.to_datetime(output["match_date"]).dt.date.astype("string")
    return output.sort_values(["match_date", "match_id"], kind="stable").reset_index(drop=True)
