from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

import valorant_quant.milestone10_shadow as shadow
from valorant_quant.completed_matches import ENDPOINT, INCREMENTAL_BEGIN_AT_UTC, FetchedPage, PollBundle
from valorant_quant.corrected_elo import CANONICAL_COLUMNS, CorrectedEloEngine, ExactIdentityResolver


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _v1_fixture(
    match_id: str = "900",
    *,
    start: str = "2026-09-23T09:00:00Z",
    generated: str = "2026-09-23T07:00:00Z",
    primary: bool = True,
    outcome: bool = False,
) -> list[dict]:
    candidate_id = f"candidate:{match_id}"
    rows = [{
        "record_id": f"forecast:{match_id}:{start}",
        "record_type": "forecast_generated",
        "generated_at_utc": generated,
        "pandascore_match_id": match_id,
        "scheduled_start_utc": start,
        "team_a": "Mapped A",
        "team_b": "Provider B",
        "team_a_provider_id": "1",
        "team_b_provider_id": "2",
        "p_team_a_wins": 0.5,
    }]
    if primary:
        captured = shadow.utc_text(shadow.parse_utc(start) - timedelta(hours=1))
        rows.extend([{
            "record_id": candidate_id,
            "record_type": "market_candidate",
            "pandascore_match_id": match_id,
            "scheduled_start_utc": start,
            "captured_at_utc": captured,
            "bookmaker": "Bet365",
            "market_type": "ML",
            "team_a_decimal_odds": "1.80",
            "team_b_decimal_odds": "2.10",
            "team_a_no_vig_probability": 0.5384615385,
            "team_b_no_vig_probability": 0.4615384615,
        }, {
            "record_id": f"primary:{match_id}",
            "record_type": "primary_market_selected",
            "pandascore_match_id": match_id,
            "candidate_record_id": candidate_id,
            "scheduled_start_utc": start,
        }])
    if outcome:
        rows.append({
            "record_id": f"outcome:{match_id}",
            "record_type": "outcome_attached",
            "pandascore_match_id": match_id,
            "completion_status": "finished",
            "forfeit": False,
            "team_a_won": True,
        })
    return rows


def _bundle(*, completed: str = "2026-09-23T08:01:00Z", query_end: str = "2026-09-23T08:00:00Z") -> PollBundle:
    return PollBundle(
        endpoint=ENDPOINT,
        query_begin_at_utc=INCREMENTAL_BEGIN_AT_UTC,
        query_end_at_utc=query_end,
        poll_started_at_utc="2026-09-23T08:00:00Z",
        poll_completed_at_utc=completed,
        total_matches_advertised=0,
        total_pages_advertised=0,
        per_page=100,
        pages=(FetchedPage(1, completed, [], {"x-total": "0"}),),
    )


class _Client:
    def __init__(self, response: PollBundle):
        self.response = response
        self.calls = []

    def fetch(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _engine() -> CorrectedEloEngine:
    base = pd.DataFrame([{
        "match_id": "base", "match_date": "2026-07-25", "year": 2026,
        "team_a_id": "hist-a", "team_a_name": "Mapped A",
        "team_b_id": "hist-x", "team_b_name": "X", "team_a_won": True,
        "tournament_name": "Base", "source_snapshot_id": "base:v1",
    }], columns=CANONICAL_COLUMNS)
    mapping = pd.DataFrame([{
        "pandascore_team_id": "1", "historical_team_id": "hist-a",
        "match_method": "exact_normalized_name", "ambiguity_flag": False,
    }])
    return CorrectedEloEngine(base, ExactIdentityResolver.from_frame(mapping, mapping_version="test-map"))


def _paths(tmp_path: Path) -> dict:
    return {
        "v1_ledger": tmp_path / "v1.jsonl",
        "v2_ledger": tmp_path / "v2.jsonl",
        "result_ledger": tmp_path / "results.jsonl",
        "raw_directory": tmp_path / "raw",
        "operational_log": tmp_path / "operations.jsonl",
        "lock_path": tmp_path / "shadow.lock",
    }


def test_normal_lifecycle_polls_results_then_freezes_oriented_v2_forecast(tmp_path):
    paths = _paths(tmp_path)
    _write_jsonl(paths["v1_ledger"], _v1_fixture())
    before = paths["v1_ledger"].read_bytes()
    result = shadow.run_once(
        dry_run=False,
        now=datetime(2026, 9, 23, 8, tzinfo=timezone.utc),
        client=_Client(_bundle()),
        engine=_engine(),
        **paths,
    )
    v2 = shadow._read_jsonl(paths["v2_ledger"])
    forecast = next(row for row in v2 if row["record_type"] == shadow.V2_FORECAST)
    assert result["action_types"] == {"v2_forecast_generated": 1, "v2_protocol_registered": 1}
    assert forecast["team_a_provider_id"] == "1" and forecast["team_b_provider_id"] == "2"
    assert forecast["team_a_identity"] == "hist-a" and forecast["team_b_identity"] == "ps:2"
    assert forecast["completed_results_poll_record_id"].startswith("v2_results_poll:")
    assert forecast["generated_at_utc"] == "2026-09-23T08:01:00Z"
    assert paths["v1_ledger"].read_bytes() == before
    assert not paths["lock_path"].exists()


def test_restart_and_duplicate_prevention_are_idempotent(tmp_path):
    paths = _paths(tmp_path)
    _write_jsonl(paths["v1_ledger"], _v1_fixture())
    kwargs = dict(
        dry_run=False, now=datetime(2026, 9, 23, 8, tzinfo=timezone.utc),
        client=_Client(_bundle()), engine=_engine(), **paths,
    )
    shadow.run_once(**kwargs)
    v2_before, results_before = paths["v2_ledger"].read_bytes(), paths["result_ledger"].read_bytes()
    second = shadow.run_once(**kwargs)
    assert paths["v2_ledger"].read_bytes() == v2_before
    assert paths["result_ledger"].read_bytes() == results_before
    assert second["v2_records_appended"] == 0


def test_dry_run_has_zero_persistent_writes(tmp_path):
    paths = _paths(tmp_path)
    _write_jsonl(paths["v1_ledger"], _v1_fixture())
    before = paths["v1_ledger"].read_bytes()
    result = shadow.run_once(
        dry_run=True,
        now=datetime(2026, 9, 23, 8, tzinfo=timezone.utc),
        client=_Client(_bundle()), engine=_engine(), **paths,
    )
    assert result["persistent_writes"] is False
    assert result["v2_records_would_append"] == 2
    assert paths["v1_ledger"].read_bytes() == before
    assert not paths["v2_ledger"].exists()
    assert not paths["result_ledger"].exists()
    assert not paths["raw_directory"].exists()
    assert not paths["operational_log"].exists()
    assert not paths["lock_path"].exists()


def test_activation_boundary_forbids_persistent_or_retrospective_forecasts(tmp_path):
    paths = _paths(tmp_path)
    old = _v1_fixture(generated="2026-09-20T07:00:00Z")
    _write_jsonl(paths["v1_ledger"], old)
    with pytest.raises(shadow.ActivationError):
        shadow.run_once(
            dry_run=False, now=datetime(2026, 9, 21, tzinfo=timezone.utc),
            client=_Client(_bundle()), engine=_engine(), **paths,
        )
    components, skips = shadow._effective_v1_components(old)
    assert components == [] and skips["pre_activation_v1_forecast"] == 1
    assert not paths["v2_ledger"].exists() and not paths["result_ledger"].exists()


def test_missing_primary_missing_v1_and_coverage_gap_fail_closed(tmp_path):
    no_market = _v1_fixture(primary=False)
    components, skips = shadow._effective_v1_components(no_market)
    assert not components and skips["absent_or_ambiguous_primary"] == 1
    assert shadow._effective_v1_components([])[0] == []
    invalidated = _v1_fixture() + [{
        "record_id": "reschedule:900:early", "record_type": "reschedule", "pandascore_match_id": "900",
        "scheduled_start_utc": "2026-09-23T06:00:00Z",
    }]
    components, skips = shadow._effective_v1_components(invalidated)
    assert not components and skips["v1_forecast_not_prestart_for_effective_schedule"] == 1

    paths = _paths(tmp_path)
    _write_jsonl(paths["v1_ledger"], _v1_fixture())
    gap = _bundle(query_end="2026-09-22T23:00:00Z")
    output = shadow.run_once(
        dry_run=False, now=datetime(2026, 9, 23, 8, tzinfo=timezone.utc),
        client=_Client(gap), engine=_engine(), **paths,
    )
    assert output["forecast_failures"][0]["error_type"] == "SourceGapError"
    assert not any(row["record_type"] == shadow.V2_FORECAST for row in shadow._read_jsonl(paths["v2_ledger"]))


def test_one_forecast_failure_isolated_from_another_fixture(tmp_path):
    paths = _paths(tmp_path)
    rows = _v1_fixture("bad", start="2026-09-23T09:00:00Z") + _v1_fixture(
        "good", start="2026-09-23T09:30:00Z"
    )
    _write_jsonl(paths["v1_ledger"], rows)

    class SelectiveEngine:
        def __init__(self):
            self.real = _engine()

        def forecast(self, fixture, **kwargs):
            if fixture.fixture_id == "bad":
                raise ValueError("isolated bad fixture")
            return self.real.forecast(fixture, **kwargs)

    output = shadow.run_once(
        dry_run=False, now=datetime(2026, 9, 23, 8, tzinfo=timezone.utc),
        client=_Client(_bundle()), engine=SelectiveEngine(), **paths,
    )
    forecasts = [row for row in shadow._read_jsonl(paths["v2_ledger"]) if row["record_type"] == shadow.V2_FORECAST]
    assert [row["pandascore_match_id"] for row in forecasts] == ["good"]
    assert output["forecast_failures"][0]["pandascore_match_id"] == "bad"


def test_material_reschedule_and_terminal_lifecycle_are_versioned():
    original = {
        "record_id": "v2_forecast:900:2026-09-23T09:00:00Z",
        "record_type": shadow.V2_FORECAST,
        "pandascore_match_id": "900",
        "generated_at_utc": "2026-09-23T08:00:00Z",
        "scheduled_start_utc": "2026-09-23T09:00:00Z",
    }
    same_day = _v1_fixture() + [{
        "record_id": "reschedule:900:same", "record_type": "reschedule", "pandascore_match_id": "900",
        "scheduled_start_utc": "2026-09-23T11:00:00Z",
    }, {
        "record_id": "primary_superseded:primary:900", "record_type": "primary_market_superseded",
        "pandascore_match_id": "900", "primary_record_id": "primary:900",
    }, {
        "record_id": "candidate:900:new", "record_type": "market_candidate", "pandascore_match_id": "900",
        "scheduled_start_utc": "2026-09-23T11:00:00Z", "captured_at_utc": "2026-09-23T10:00:00Z",
        "bookmaker": "Bet365", "market_type": "ML", "team_a_decimal_odds": "1.80", "team_b_decimal_odds": "2.10",
    }, {
        "record_id": "primary:900:new", "record_type": "primary_market_selected", "pandascore_match_id": "900",
        "candidate_record_id": "candidate:900:new", "scheduled_start_utc": "2026-09-23T11:00:00Z",
    }]
    actions = shadow._lifecycle_actions(same_day, [original], observed_at_utc="2026-09-23T08:00:00Z")
    assert actions[0]["record_type"] == shadow.V2_SCHEDULE
    assert actions[0]["v1_primary_record_id"] == "primary:900:new"

    cross_date = _v1_fixture() + [{
        "record_id": "reschedule:900:cross", "record_type": "reschedule", "pandascore_match_id": "900",
        "scheduled_start_utc": "2026-09-24T11:00:00Z",
    }]
    actions = shadow._lifecycle_actions(cross_date, [original], observed_at_utc="2026-09-23T08:00:00Z")
    assert actions[0]["record_type"] == shadow.V2_SUPERSEDED

    cancelled = _v1_fixture() + [{
        "record_id": "terminal:900:cancelled", "record_type": "terminal_exclusion",
        "pandascore_match_id": "900", "reason": "cancelled",
    }]
    actions = shadow._lifecycle_actions(cancelled, [original], observed_at_utc="2026-09-23T08:00:00Z")
    assert actions[0]["record_type"] == shadow.V2_TERMINAL and actions[0]["reason"] == "cancelled"


def test_three_way_eligibility_requires_exact_links_and_excludes_inspected_ids():
    v1 = _v1_fixture(outcome=True)
    v2 = [{
        "record_id": "v2_forecast:900:2026-09-23T09:00:00Z",
        "record_type": shadow.V2_FORECAST,
        "pandascore_match_id": "900",
        "generated_at_utc": "2026-09-23T08:01:00Z",
        "scheduled_start_utc": "2026-09-23T09:00:00Z",
        "v1_forecast_record_id": v1[0]["record_id"],
        "v1_market_candidate_record_id": "candidate:900",
        "v1_primary_record_id": "primary:900",
    }]
    units = shadow.reconstruct_three_way_eligible(v1, v2, frozen_checkpoint_ids=set())
    assert len(units) == 1
    assert units[0]["v1_forecast"]["record_id"] == v1[0]["record_id"]
    assert units[0]["v2_forecast"]["record_id"] == v2[0]["record_id"]
    assert units[0]["bet365_primary"]["bookmaker"] == "Bet365"
    assert units[0]["outcome"]["record_id"] == "outcome:900"
    assert shadow.reconstruct_three_way_eligible(v1, v2, frozen_checkpoint_ids={"900"}) == []
    v2[0]["v1_primary_record_id"] = "wrong"
    assert shadow.reconstruct_three_way_eligible(v1, v2, frozen_checkpoint_ids=set()) == []


def test_retry_wrapper_retries_only_retryable_errors():
    calls = []

    def transient(url, headers, query):
        calls.append(query)
        if len(calls) < 3:
            raise URLError("temporary")
        return "ok"

    delays = []
    wrapped = shadow.retrying_transport(transient, attempts=3, sleeper=delays.append)
    assert wrapped("u", {}, {"page": "1"}) == "ok"
    assert delays == [1.0, 2.0]

    def unauthorized(url, headers, query):
        raise HTTPError("u", 401, "denied", {}, None)

    with pytest.raises(HTTPError):
        shadow.retrying_transport(unauthorized, sleeper=lambda _: None)("u", {}, {})


def test_result_fetch_failure_is_logged_without_touching_forecast_or_result_ledgers(tmp_path):
    paths = _paths(tmp_path)
    _write_jsonl(paths["v1_ledger"], _v1_fixture())

    class BrokenClient:
        def fetch(self, **kwargs):
            raise URLError("source unavailable")

    with pytest.raises(URLError):
        shadow.run_once(
            dry_run=False, now=datetime(2026, 9, 23, 8, tzinfo=timezone.utc),
            client=BrokenClient(), engine=_engine(), **paths,
        )
    assert not paths["v2_ledger"].exists() and not paths["result_ledger"].exists()
    failure = shadow._read_jsonl(paths["operational_log"])
    assert failure[0]["record_type"] == "v2_operational_failure"
    assert failure[0]["stage"] == "result_ingestion"
    assert not paths["lock_path"].exists()


def test_exclusive_lock_rejects_overlapping_cycle(tmp_path):
    lock = tmp_path / "collector.lock"
    with shadow._collector_lock(lock, acquired_at_utc="2026-09-23T08:00:00Z"):
        with pytest.raises(shadow.ConcurrentRunError):
            with shadow._collector_lock(lock, acquired_at_utc="2026-09-23T08:00:01Z"):
                pass
    assert not lock.exists()


def test_operational_status_contains_no_performance_metrics(tmp_path):
    paths = _paths(tmp_path)
    _write_jsonl(paths["v1_ledger"], _v1_fixture(outcome=True))
    status = shadow.operational_status(
        v1_ledger=paths["v1_ledger"], v2_ledger=paths["v2_ledger"],
        result_ledger=paths["result_ledger"], operational_log=paths["operational_log"],
    )
    assert status["performance_metrics_computed"] is False
    banned = {"accuracy", "brier", "log_loss", "profit", "roi"}
    assert not banned.intersection(status)
