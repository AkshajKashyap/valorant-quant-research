import pytest

from valorant_quant.milestone7_audit import (
    cold_start_classification,
    fixture_orientation,
    loss_decomposition,
    resolved_identity,
    serialize_audit,
)


@pytest.mark.parametrize(
    ("prior_a", "prior_b", "expected"),
    [
        (0, 0, "both_cold_start"),
        (0, 7, "exactly_one_cold_start"),
        (9, 0, "exactly_one_cold_start"),
        (1, 2, "neither_cold_start"),
    ],
)
def test_cold_start_classification_uses_frozen_prior_counts(prior_a, prior_b, expected):
    forecast = {
        "team_a_prior_eligible_matches": prior_a,
        "team_b_prior_eligible_matches": prior_b,
    }
    assert cold_start_classification(forecast) == expected


def test_identity_and_market_orientation_are_explicit():
    assert resolved_identity(128541, "474") == "474"
    assert resolved_identity(128541, None) == "ps:128541"
    assert fixture_orientation("Team Liquid", "FUT Esports", "Team Liquid", "FUT Esports") == "aligned"
    assert fixture_orientation("Team Liquid", "FUT Esports", "FUT Esports", "Team Liquid") == "reversed"
    assert fixture_orientation("Team Liquid", "FUT Esports", "Other", "Team Liquid") == "mismatch"


def test_loss_decomposition_is_additive_and_keeps_all_groups():
    rows = [
        {
            "group": "cold",
            "elo_probability_team_a": 0.5,
            "bet365_no_vig_probability_team_a": 0.75,
            "team_a_won": True,
        },
        {
            "group": "warm",
            "elo_probability_team_a": 0.8,
            "bet365_no_vig_probability_team_a": 0.6,
            "team_a_won": False,
        },
        {
            "group": "warm",
            "elo_probability_team_a": 0.4,
            "bet365_no_vig_probability_team_a": 0.4,
            "team_a_won": True,
        },
    ]
    result = loss_decomposition(rows, lambda row: row["group"], order=("cold", "warm"))
    assert [row["matches"] for row in result] == [1, 2]
    assert sum(row["matches"] for row in result) == len(rows)
    assert sum(row["log_loss_contribution_to_aggregate_mean"] for row in result) == pytest.approx(
        sum(row["elo_minus_market_log_loss_sum"] for row in result) / len(rows)
    )
    assert sum(row["brier_contribution_to_aggregate_mean"] for row in result) == pytest.approx(
        sum(row["elo_minus_market_brier_sum"] for row in result) / len(rows)
    )


def test_audit_json_serialization_is_deterministic():
    first = serialize_audit({"z": [3, 2, 1], "a": {"y": 2, "x": 1}})
    second = serialize_audit({"a": {"x": 1, "y": 2}, "z": [3, 2, 1]})
    assert first == second
    assert first.startswith('{\n  "a"') and first.endswith("\n")
