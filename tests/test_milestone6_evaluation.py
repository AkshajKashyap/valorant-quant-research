import json
from pathlib import Path

import pytest

from valorant_quant import milestone6_evaluation as evaluation
from valorant_quant import milestone6_runner as runner
from valorant_quant.prospective import eligible_completed_count


def match_records(match_id: str, start: str, *, forecast_position: int = 0, outcome: bool = True, odds=(2.0, 2.0), elo=.6):
    del forecast_position
    candidate_id = f"candidate:{match_id}"
    forecast = {
        "record_id": f"forecast:{match_id}", "record_type": "forecast_generated", "pandascore_match_id": match_id,
        "scheduled_start_utc": start, "team_a": "A", "team_b": "B", "p_team_a_wins": elo,
    }
    candidate = {
        "record_id": candidate_id, "record_type": "market_candidate", "pandascore_match_id": match_id,
        "scheduled_start_utc": start, "captured_at_utc": start.replace("12:00:00", "11:00:00"),
        "bookmaker": "Bet365", "market_type": "ML", "team_a_decimal_odds": odds[0], "team_b_decimal_odds": odds[1],
    }
    primary = {
        "record_id": f"primary:{match_id}", "record_type": "primary_market_selected", "pandascore_match_id": match_id,
        "candidate_record_id": candidate_id, "scheduled_start_utc": start,
    }
    attached = {
        "record_id": f"outcome:{match_id}", "record_type": "outcome_attached", "pandascore_match_id": match_id,
        "completion_status": "finished", "forfeit": False, "team_a_won": outcome,
    }
    return [forecast, candidate, primary, attached]


def test_reconstruction_is_exact_active_and_deterministic():
    first = match_records("2", "2026-08-02T12:00:00Z")
    second = match_records("1", "2026-08-01T12:00:00Z")
    stale = match_records("stale", "2026-08-03T12:00:00Z")
    stale.append({"record_id": "primary-superseded:stale", "record_type": "primary_market_superseded", "pandascore_match_id": "stale", "primary_record_id": "primary:stale"})
    terminal = match_records("terminal", "2026-08-04T12:00:00Z")
    terminal.append({"record_id": "terminal:terminal", "record_type": "terminal_exclusion", "pandascore_match_id": "terminal"})
    rows = evaluation.reconstruct_eligible_observations(first + second + stale + terminal)
    assert [row["pandascore_match_id"] for row in rows] == ["1", "2"]
    assert len({row["pandascore_match_id"] for row in rows}) == 2
    assert rows[0]["primary_snapshot_record_id"] == "primary:1"


def test_reconstruction_requires_valid_linked_bet365_prices_and_binary_outcome():
    invalid_odds = match_records("odds", "2026-08-01T12:00:00Z", odds=(1.0, 2.0))
    invalid_outcome = match_records("outcome", "2026-08-02T12:00:00Z")
    invalid_outcome[-1]["team_a_won"] = None
    assert evaluation.reconstruct_eligible_observations(invalid_odds + invalid_outcome) == []


def test_status_and_evaluator_share_strict_active_primary_eligibility(tmp_path, monkeypatch):
    active = match_records("active", "2026-08-01T12:00:00Z")
    stale = match_records("stale", "2026-08-02T12:00:00Z")
    stale.append({
        "record_id": "primary-superseded:stale", "record_type": "primary_market_superseded",
        "pandascore_match_id": "stale", "primary_record_id": "primary:stale",
    })
    records = active + stale
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    monkeypatch.setattr(runner, "LEDGER", ledger)

    assert eligible_completed_count(records) == 1
    assert [row["pandascore_match_id"] for row in evaluation.reconstruct_eligible_observations(records)] == ["active"]
    assert runner.status()["eligible_completed"] == 1


def test_freeze_uses_first_chronological_ledger_order_without_subsetting():
    rows = evaluation.reconstruct_eligible_observations(
        match_records("late", "2026-08-02T12:00:00Z") + match_records("first", "2026-08-01T12:00:00Z") + match_records("second", "2026-08-01T12:00:00Z")
    )
    assert [row["pandascore_match_id"] for row in evaluation.freeze_checkpoint(rows, 2)] == ["first", "second"]
    with pytest.raises(evaluation.InsufficientEligibleMatchesError):
        evaluation.freeze_checkpoint(rows, 4)


def test_no_vig_and_core_scores_are_registered_definitions():
    assert evaluation.no_vig_probabilities(2.0, 2.0) == (.5, .5)
    with pytest.raises(ValueError):
        evaluation.no_vig_probabilities(1.0, 2.0)
    assert evaluation.log_loss(.8, True) == pytest.approx(-__import__("math").log(.8))
    assert evaluation.brier_score(.8, True) == pytest.approx(.04)
    score = evaluation.accuracy([.7, .3, .5], [True, True, False])
    assert score == {"value": .5, "correct": 1, "directional_predictions": 2, "no_directional_ties": 1, "tie_rule": evaluation.ACCURACY_TIE_RULE}


def test_paired_deltas_and_date_cluster_bootstrap_are_reproducible():
    rows = evaluation.reconstruct_eligible_observations(
        match_records("1", "2026-08-01T12:00:00Z", outcome=True, odds=(2, 2), elo=.8)
        + match_records("2", "2026-08-01T13:00:00Z", outcome=False, odds=(2, 2), elo=.8)
        + match_records("3", "2026-08-02T12:00:00Z", outcome=True, odds=(2, 2), elo=.6)
    )
    paired = evaluation.paired_deltas(rows)
    assert paired[0]["log_loss_difference"] < 0
    first = evaluation.bootstrap_mean_date_clustered(paired, "log_loss_difference", seed=9, replicates=200)
    second = evaluation.bootstrap_mean_date_clustered(paired, "log_loss_difference", seed=9, replicates=200)
    assert first == second


@pytest.mark.parametrize(("value", "expected"), [
    (.019999, "< 2 pp"), (.02, "2–5 pp"), (.049999, "2–5 pp"),
    (.05, "5–10 pp"), (.1, "5–10 pp"), (.100001, "> 10 pp"),
])
def test_preregistered_disagreement_bucket_boundaries(value, expected):
    assert evaluation.disagreement_bucket(value) == expected
    assert evaluation.disagreement_bucket(-value) == expected


def test_evaluation_is_read_only_for_ledger_and_report_stays_exploratory(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    records = match_records("1", "2026-08-01T12:00:00Z") + match_records("2", "2026-08-02T12:00:00Z")
    ledger.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")
    monkeypatch.setattr(evaluation, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(evaluation, "REPORTS", tmp_path / "reports")
    dataset, report, _ = evaluation.run_evaluation(checkpoint=2, exploratory=True, ledger_path=ledger)
    assert ledger.read_text(encoding="utf-8") == before
    assert dataset.exists() and report.exists()
    assert "EXPLORATORY EARLY LOOK" in report.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="below 30"):
        evaluation.run_evaluation(checkpoint=2, exploratory=False, ledger_path=ledger)
