import pandas as pd
import pytest

from valorant_quant.corrected_elo import (
    CANONICAL_COLUMNS,
    MODEL_VERSION,
    CorrectedEloEngine,
    ExactIdentityResolver,
    Fixture,
    validate_v2_forecast_record,
)


def canonical(rows):
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


def match(match_id, match_date, team_a_id, team_b_id, team_a_won=True):
    return {
        "match_id": match_id,
        "match_date": match_date,
        "year": int(match_date[:4]),
        "team_a_id": team_a_id,
        "team_a_name": f"Name {team_a_id}",
        "team_b_id": team_b_id,
        "team_b_name": f"Name {team_b_id}",
        "team_a_won": team_a_won,
        "tournament_name": "Test",
        "source_snapshot_id": "base:v1",
    }


def mapping(rows):
    return pd.DataFrame([
        {
            "pandascore_team_id": provider,
            "historical_team_id": historical,
            "match_method": method,
            "ambiguity_flag": ambiguous,
        }
        for provider, historical, method, ambiguous in rows
    ])


def resolver():
    return ExactIdentityResolver.from_frame(
        mapping([
            ("101", "hist-a", "exact_normalized_name", False),
            ("102", "hist-b", "exact_normalized_name", False),
            ("103", "hist-c", "exact_normalized_name", False),
            ("201", "", "new_pandascore_team", False),
        ]),
        mapping_version="test-map-v1",
    )


def fixture(date="2026-01-05", *, a="101", b="102", name_a="A", name_b="B", fixture_id="f1"):
    return Fixture(
        fixture_id=fixture_id,
        scheduled_start_utc=f"{date}T12:00:00Z",
        team_a_provider_id=a,
        team_a_name=name_a,
        team_b_provider_id=b,
        team_b_name=name_b,
    )


def completed(
    match_id,
    match_date,
    available,
    *,
    a="101",
    b="103",
    a_won=False,
    eligible=True,
):
    return {
        "pandascore_match_id": match_id,
        "match_date": match_date,
        "team_a_provider_id": a,
        "team_a_name": f"Provider {a}",
        "team_b_provider_id": b,
        "team_b_name": f"Provider {b}",
        "team_a_won": a_won,
        "tournament_name": "Dynamic",
        "source_snapshot_id": f"result:{match_id}",
        "completion_status": "finished",
        "forfeit": False,
        "eligible": eligible,
        "completed_at_utc": f"{match_date}T08:00:00Z",
        "result_available_at_utc": available,
    }


def base():
    return canonical([
        match("base-1", "2026-01-01", "hist-a", "hist-b", True),
        match("base-2", "2026-01-02", "hist-a", "hist-c", True),
        match("base-3", "2026-01-02", "ps:201", "hist-b", False),
    ])


def test_historical_exact_identity_is_used_for_rating_and_count():
    record = CorrectedEloEngine(base(), resolver()).forecast(
        fixture(), generated_at_utc="2026-01-05T10:00:00Z"
    )
    assert record["team_a_identity"] == "hist-a"
    assert record["team_b_identity"] == "hist-b"
    assert record["team_a_prior_eligible_matches"] == 2
    assert record["team_b_prior_eligible_matches"] == 2
    assert record["elo_a"] > record["elo_b"]
    assert record["model_version"] == MODEL_VERSION


def test_provider_only_identity_continues_and_true_new_team_stays_cold():
    record = CorrectedEloEngine(base(), resolver()).forecast(
        fixture(a="201", b="999", name_a="Provider Only", name_b="Actually New"),
        generated_at_utc="2026-01-05T10:00:00Z",
    )
    assert record["team_a_identity"] == "ps:201"
    assert record["team_a_prior_eligible_matches"] == 1
    assert record["elo_a"] != 1500
    assert record["team_b_identity"] == "ps:999"
    assert record["team_b_prior_eligible_matches"] == 0
    assert record["elo_b"] == 1500


def test_non_exact_or_ambiguous_identity_mapping_is_rejected():
    with pytest.raises(ValueError, match="non-exact or ambiguous"):
        ExactIdentityResolver.from_frame(
            mapping([("101", "hist-a", "fuzzy_name", False)]), mapping_version="bad"
        )
    with pytest.raises(ValueError, match="non-exact or ambiguous"):
        ExactIdentityResolver.from_frame(
            mapping([("101", "hist-a", "exact_normalized_name", True)]), mapping_version="bad"
        )


def test_fixture_orientation_is_preserved_and_reversal_is_complementary():
    engine = CorrectedEloEngine(base(), resolver())
    forward = engine.forecast(fixture(a="101", b="102"), generated_at_utc="2026-01-05T10:00:00Z")
    reverse = engine.forecast(
        fixture(a="102", b="101", fixture_id="f2"), generated_at_utc="2026-01-05T10:00:00Z"
    )
    assert forward["elo_a"] == reverse["elo_b"]
    assert forward["elo_b"] == reverse["elo_a"]
    assert forward["p_team_a_wins"] + reverse["p_team_a_wins"] == pytest.approx(1.0)


def test_d2_uses_new_state_when_eligible_d1_result_is_available():
    engine = CorrectedEloEngine(base(), resolver())
    feed = pd.DataFrame([
        completed("dynamic-1", "2026-01-03", "2026-01-03T15:00:00Z", a_won=False)
    ])
    d1 = engine.forecast(
        fixture("2026-01-03", fixture_id="d1"),
        generated_at_utc="2026-01-03T10:00:00Z",
        completed_matches=feed,
    )
    d2 = engine.forecast(
        fixture("2026-01-04", fixture_id="d2"),
        generated_at_utc="2026-01-03T18:00:00Z",
        completed_matches=feed,
    )
    assert d1["state_through_date"] == "2026-01-02"
    assert d2["state_through_date"] == "2026-01-03"
    assert d2["team_a_prior_eligible_matches"] == d1["team_a_prior_eligible_matches"] + 1
    assert d2["elo_a"] != d1["elo_a"]
    assert d2["p_team_a_wins"] != d1["p_team_a_wins"]


def test_same_date_result_cannot_affect_same_day_forecast_even_if_already_observed():
    engine = CorrectedEloEngine(base(), resolver())
    same_day = pd.DataFrame([
        completed("same-day", "2026-01-03", "2026-01-03T09:00:00Z")
    ])
    with_feed = engine.forecast(
        fixture("2026-01-03"), generated_at_utc="2026-01-03T10:00:00Z", completed_matches=same_day
    )
    without_feed = engine.forecast(fixture("2026-01-03"), generated_at_utc="2026-01-03T10:00:00Z")
    assert with_feed == without_feed


def test_result_unavailable_at_generation_time_cannot_leak():
    engine = CorrectedEloEngine(base(), resolver())
    late = pd.DataFrame([
        completed("late", "2026-01-02", "2026-01-03T11:00:00Z")
    ])
    with_late = engine.forecast(
        fixture("2026-01-03"), generated_at_utc="2026-01-03T10:00:00Z", completed_matches=late
    )
    without_late = engine.forecast(fixture("2026-01-03"), generated_at_utc="2026-01-03T10:00:00Z")
    assert with_late == without_late


def test_same_date_batching_is_order_invariant_and_reconstruction_is_deterministic():
    first = completed("dynamic-a", "2026-01-03", "2026-01-03T15:00:00Z", b="103", a_won=True)
    second = completed("dynamic-b", "2026-01-03", "2026-01-03T16:00:00Z", b="102", a_won=False)
    fixture_d2 = fixture("2026-01-04")
    engine = CorrectedEloEngine(base(), resolver())
    one = engine.forecast(
        fixture_d2,
        generated_at_utc="2026-01-03T18:00:00Z",
        completed_matches=pd.DataFrame([first, second]),
    )
    two = engine.forecast(
        fixture_d2,
        generated_at_utc="2026-01-03T18:00:00Z",
        completed_matches=pd.DataFrame([second, first]),
    )
    assert one == two


def test_reproducibility_after_process_restart_equivalent_engine_rebuild():
    feed = pd.DataFrame([
        completed("dynamic-1", "2026-01-03", "2026-01-03T15:00:00Z")
    ])
    first_engine = CorrectedEloEngine(base(), resolver())
    second_engine = CorrectedEloEngine(base(), resolver())
    first = first_engine.forecast(
        fixture("2026-01-04"), generated_at_utc="2026-01-03T18:00:00Z", completed_matches=feed
    )
    second = second_engine.forecast(
        fixture("2026-01-04"), generated_at_utc="2026-01-03T18:00:00Z", completed_matches=feed
    )
    assert first == second
    assert len(first["state_input_sha256"]) == 64


def test_v2_schema_is_complete_and_rejects_parameter_or_timing_drift():
    record = CorrectedEloEngine(base(), resolver()).forecast(
        fixture(), generated_at_utc="2026-01-05T10:00:00Z"
    )
    validate_v2_forecast_record(record)
    invalid = dict(record, k=32)
    with pytest.raises(ValueError, match="parameters"):
        validate_v2_forecast_record(invalid)
    with pytest.raises(ValueError, match="before scheduled"):
        CorrectedEloEngine(base(), resolver()).forecast(
            fixture(), generated_at_utc="2026-01-05T12:00:00Z"
        )
