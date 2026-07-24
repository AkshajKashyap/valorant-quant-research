import pandas as pd
import pytest

from valorant_quant.elo import EloConfig, evaluate, run_elo, validate_canonical_matches, win_probability
from valorant_quant.milestone2_5 import bootstrap_resample_dates, date_bootstrap, metrics, per_match_losses, run_robustness


def matches() -> pd.DataFrame:
    return pd.DataFrame({
        "match_id": ["m1", "m2", "m3"],
        "match_date": ["2021-01-01", "2021-01-01", "2021-01-02"],
        "year": [2021, 2021, 2021],
        "team_a_id": ["a", "b", "a"], "team_a_name": ["A", "B", "A"],
        "team_b_id": ["b", "c", "c"], "team_b_name": ["B", "C", "C"],
        "team_a_won": [True, False, True], "tournament_name": ["E"] * 3,
        "source_snapshot_id": ["s"] * 3,
    })


def test_probability_symmetry_and_equal_ratings() -> None:
    assert win_probability(1500, 1500) == pytest.approx(0.5)
    assert win_probability(1600, 1400) + win_probability(1400, 1600) == pytest.approx(1.0)


def test_winner_gains_loser_loses_and_total_is_zero() -> None:
    predictions, ratings = run_elo(matches().iloc[:1], EloConfig(k=32))
    assert predictions.loc[0, "elo_delta_a"] > 0
    assert ratings["a"] > 1500 and ratings["b"] < 1500
    assert sum(ratings.values()) == pytest.approx(3000)


def test_same_day_order_cannot_change_predictions_or_end_ratings() -> None:
    original_predictions, original_ratings = run_elo(matches(), EloConfig(k=32))
    shuffled = matches().iloc[[1, 0, 2]].reset_index(drop=True)
    shuffled_predictions, shuffled_ratings = run_elo(shuffled, EloConfig(k=32))
    left = original_predictions.sort_values("match_id").reset_index(drop=True)
    right = shuffled_predictions.sort_values("match_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)
    assert original_ratings == pytest.approx(shuffled_ratings)


def test_future_matches_cannot_change_prior_prediction_and_unseen_get_initial_rating() -> None:
    one_prediction, _ = run_elo(matches().iloc[:1], EloConfig(k=32))
    all_predictions, _ = run_elo(matches(), EloConfig(k=32))
    assert one_prediction.loc[0, "probability_a"] == all_predictions.query("match_id == 'm1'").iloc[0]["probability_a"]
    assert one_prediction.loc[0, "rating_a_pre"] == 1500
    assert one_prediction.loc[0, "rating_b_pre"] == 1500


def test_validation_rejects_duplicate_ids_invalid_outcome_and_placeholder_date() -> None:
    invalid = matches().copy()
    invalid.loc[1, "match_id"] = "m1"
    with pytest.raises(ValueError, match="unique"):
        validate_canonical_matches(invalid)
    invalid = matches().copy()
    invalid.loc[0, "match_date"] = "1970-01-01"
    with pytest.raises(ValueError, match="1970"):
        validate_canonical_matches(invalid)
    invalid = matches().copy()
    invalid["team_a_won"] = pd.Series([True, None, False], dtype="boolean")
    with pytest.raises(ValueError, match="binary"):
        validate_canonical_matches(invalid)


def test_date_bootstrap_is_deterministic_and_keeps_complete_date_clusters() -> None:
    predictions, _ = run_elo(matches(), EloConfig(k=32))
    losses = per_match_losses(predictions)
    first = date_bootstrap(losses, seed=7, replicates=100)
    second = date_bootstrap(losses, seed=7, replicates=100)
    assert first == second
    sample = bootstrap_resample_dates(losses, seed=7, replicates=1)[0]
    source_sizes = losses.groupby("match_date").size()
    for date, count in sample.groupby("match_date").size().items():
        assert count % source_sizes[date] == 0


def test_per_match_losses_reproduce_aggregate_metrics_and_k_runs_do_not_mutate_input() -> None:
    table = matches().copy()
    table["match_date"] = ["2024-01-01", "2024-01-01", "2024-01-02"]
    table["year"] = 2024
    original = table.copy(deep=True)
    predictions, _ = run_elo(table, EloConfig(k=64))
    losses = per_match_losses(predictions)
    standard_metrics, _ = evaluate(predictions)
    assert metrics(losses)["elo_log_loss"] == pytest.approx(standard_metrics["test_2024"]["log_loss"])
    assert metrics(losses)["elo_brier"] == pytest.approx(standard_metrics["test_2024"]["brier_score"])
    run_robustness(table)
    pd.testing.assert_frame_equal(table, original)
    invalid = matches().copy()
    invalid["team_a_won"] = ["yes", "no", "yes"]
    with pytest.raises(ValueError, match="binary"):
        validate_canonical_matches(invalid)
