from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import valorant_quant.completed_matches as results
from valorant_quant.completed_matches import (
    ENDPOINT,
    INCREMENTAL_BEGIN_AT_UTC,
    FetchedPage,
    HttpResponse,
    IncompletePaginationError,
    PandaScoreCompletedClient,
    PollBundle,
    SourceGapError,
    forecast_from_result_ledger,
    ingest_bundle,
    load_result_ledger,
    materialize_results_as_of,
    poll_once,
    require_coverage_for_forecast,
)
from valorant_quant.corrected_elo import CANONICAL_COLUMNS, CorrectedEloEngine, ExactIdentityResolver, Fixture


def source_match(
    match_id,
    *,
    match_date="2026-08-01",
    winner=1,
    team_a=1,
    team_b=2,
    status="finished",
    end_hour=8,
):
    return {
        "id": match_id,
        "scheduled_at": f"{match_date}T06:00:00Z",
        "begin_at": f"{match_date}T06:05:00Z",
        "end_at": f"{match_date}T{end_hour:02}:00:00Z",
        "modified_at": f"{match_date}T{end_hour + 1:02}:00:00Z",
        "status": status,
        "forfeit": False,
        "winner_id": winner,
        "opponents": [
            {"opponent": {"id": team_b, "name": f"Team {team_b}"}},
            {"opponent": {"id": team_a, "name": f"Team {team_a}"}},
        ],
        "results": [
            {"team_id": team_a, "score": 2 if winner == team_a else 0},
            {"team_id": team_b, "score": 2 if winner == team_b else 0},
        ],
        "tournament": {"name": "Test tournament"},
    }


def bundle(
    matches,
    *,
    received="2026-08-02T09:01:00Z",
    started="2026-08-02T09:00:00Z",
    completed="2026-08-02T09:02:00Z",
    query_end="2026-08-02T08:59:00Z",
):
    return PollBundle(
        endpoint=ENDPOINT,
        query_begin_at_utc=INCREMENTAL_BEGIN_AT_UTC,
        query_end_at_utc=query_end,
        poll_started_at_utc=started,
        poll_completed_at_utc=completed,
        total_matches_advertised=len(matches),
        total_pages_advertised=1 if matches else 0,
        per_page=100,
        pages=(FetchedPage(1, received, list(matches), {
            "x-total": str(len(matches)),
            "x-total-pages": "1" if matches else "0",
            "x-page": "1",
            "x-per-page": "100",
        }),),
    )


def test_client_reads_every_advertised_page_and_preserves_receipt_times(monkeypatch):
    monkeypatch.setattr(results, "PER_PAGE", 2)
    matches = [source_match(1), source_match(2), source_match(3)]
    calls = []

    def transport(url, headers, query):
        calls.append((url, "Authorization" in headers, dict(query)))
        page = int(query["page"])
        body = matches[:2] if page == 1 else matches[2:]
        return HttpResponse(body, {"X-Total": "3", "X-Total-Pages": "2", "X-Page": str(page), "X-Per-Page": "2"})

    times = iter([
        datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 9, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 9, 3, tzinfo=timezone.utc),
    ])
    fetched = PandaScoreCompletedClient("secret", transport=transport, clock=lambda: next(times)).fetch(
        begin_at_utc=INCREMENTAL_BEGIN_AT_UTC, end_at_utc="2026-08-02T08:59:00Z"
    )
    assert [page.page for page in fetched.pages] == [1, 2]
    assert [page.received_at_utc for page in fetched.pages] == ["2026-08-02T09:01:00Z", "2026-08-02T09:02:00Z"]
    assert [call[2]["page"] for call in calls] == ["1", "2"]
    assert all(call[0] == ENDPOINT and call[1] for call in calls)


def test_pagination_change_or_incomplete_response_fails_closed(monkeypatch):
    monkeypatch.setattr(results, "PER_PAGE", 2)

    def transport(url, headers, query):
        if query["page"] == "1":
            return HttpResponse([source_match(1), source_match(2)], {"X-Total": "3", "X-Total-Pages": "2"})
        return HttpResponse([source_match(3)], {"X-Total": "4", "X-Total-Pages": "2"})

    with pytest.raises(IncompletePaginationError, match="x-total changed"):
        PandaScoreCompletedClient("secret", transport=transport).fetch(
            begin_at_utc=INCREMENTAL_BEGIN_AT_UTC, end_at_utc="2026-08-02T08:59:00Z"
        )


def test_incremental_poll_and_repeated_identical_results_are_deduplicated(tmp_path):
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    first = ingest_bundle(bundle([source_match(1), source_match(2)]), raw_directory=raw, ledger_path=ledger)
    second_bundle = bundle(
        [source_match(1), source_match(2)],
        received="2026-08-03T09:01:00Z",
        started="2026-08-03T09:00:00Z",
        completed="2026-08-03T09:02:00Z",
        query_end="2026-08-03T08:59:00Z",
    )
    second = ingest_bundle(second_bundle, raw_directory=raw, ledger_path=ledger)
    records = load_result_ledger(ledger)
    assert first["result_versions_appended"] == 2
    assert second["result_versions_appended"] == 0
    assert second["unchanged_results"] == 2
    assert sum(record["record_type"] in results.RESULT_EVENT_TYPES for record in records) == 2
    assert sum(record["record_type"] == "v2_results_poll_completed" for record in records) == 2
    assert len(list(raw.glob("*.json"))) == 2


def test_delayed_result_uses_first_observation_not_completion_time(tmp_path):
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    delayed = bundle(
        [source_match(7)],
        received="2026-08-03T09:01:00Z",
        started="2026-08-03T09:00:00Z",
        completed="2026-08-03T09:02:00Z",
        query_end="2026-08-03T08:59:00Z",
    )
    ingest_bundle(delayed, raw_directory=raw, ledger_path=ledger)
    records = load_result_ledger(ledger)
    before = materialize_results_as_of(records, as_of_utc="2026-08-03T09:00:59Z")
    after = materialize_results_as_of(records, as_of_utc="2026-08-03T09:01:00Z")
    assert before.empty
    assert after.iloc[0].completed_at_utc == "2026-08-01T08:00:00Z"
    assert after.iloc[0].first_observed_result_at_utc == "2026-08-03T09:01:00Z"
    assert after.iloc[0].result_available_at_utc == "2026-08-03T09:01:00Z"


def test_provider_correction_is_versioned_and_does_not_rewrite_first_observation(tmp_path):
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    ingest_bundle(bundle([source_match(8, winner=1)]), raw_directory=raw, ledger_path=ledger)
    corrected = bundle(
        [source_match(8, winner=2)],
        received="2026-08-03T09:01:00Z",
        started="2026-08-03T09:00:00Z",
        completed="2026-08-03T09:02:00Z",
        query_end="2026-08-03T08:59:00Z",
    )
    ingest_bundle(corrected, raw_directory=raw, ledger_path=ledger)
    records = load_result_ledger(ledger)
    versions = [record for record in records if record.get("pandascore_match_id") == "8"]
    assert [record["record_type"] for record in versions] == ["v2_result_observed", "v2_result_corrected"]
    assert versions[1]["supersedes_record_id"] == versions[0]["record_id"]
    assert versions[1]["first_observed_result_at_utc"] == versions[0]["first_observed_result_at_utc"]
    old = materialize_results_as_of(records, as_of_utc="2026-08-02T10:00:00Z").iloc[0]
    new = materialize_results_as_of(records, as_of_utc="2026-08-03T10:00:00Z").iloc[0]
    assert bool(old.team_a_won) is True
    assert bool(new.team_a_won) is False
    assert new.result_available_at_utc == "2026-08-03T09:01:00Z"


def test_source_gap_blocks_forecast_when_fixed_boundary_poll_is_missing():
    target = Fixture("future", "2026-08-03T12:00:00Z", "1", "A", "2", "B")
    with pytest.raises(SourceGapError, match="no complete"):
        require_coverage_for_forecast([], fixture=target, generated_at_utc="2026-08-03T10:00:00Z")


def test_poll_through_generation_is_sufficient_before_fixture_date_midnight():
    target = Fixture("tomorrow", "2026-08-04T00:30:00Z", "1", "A", "2", "B")
    poll = {
        "record_id": "v2_results_poll:test",
        "record_type": "v2_results_poll_completed",
        "query_begin_at_utc": INCREMENTAL_BEGIN_AT_UTC,
        "query_end_at_utc": "2026-08-03T23:00:01Z",
        "poll_completed_at_utc": "2026-08-03T23:00:01Z",
        "normalization_failures": [],
    }
    assert require_coverage_for_forecast(
        [poll], fixture=target, generated_at_utc="2026-08-03T23:00:01Z"
    )["record_id"] == "v2_results_poll:test"


def test_boundary_overlap_is_audited_but_cannot_duplicate_base_elo_update(tmp_path):
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    overlap = source_match(19, match_date="2026-07-25")
    ingest_bundle(bundle([overlap]), raw_directory=raw, ledger_path=ledger)
    frame = materialize_results_as_of(load_result_ledger(ledger), as_of_utc="2026-08-03T10:00:00Z")
    assert len(frame) == 1
    assert not bool(frame.iloc[0].eligible)
    event = next(record for record in load_result_ledger(ledger) if record.get("pandascore_match_id") == "19")
    assert event["eligibility_reason"] == "pre_or_at_bridge_boundary"


def _engine():
    base = pd.DataFrame([{
        "match_id": "base", "match_date": "2026-07-25", "year": 2026,
        "team_a_id": "hist-a", "team_a_name": "A", "team_b_id": "hist-b", "team_b_name": "B",
        "team_a_won": True, "tournament_name": "Base", "source_snapshot_id": "base:v1",
    }], columns=CANONICAL_COLUMNS)
    identity = pd.DataFrame([
        {"pandascore_team_id": "1", "historical_team_id": "hist-a", "match_method": "exact_normalized_name", "ambiguity_flag": False},
        {"pandascore_team_id": "2", "historical_team_id": "hist-b", "match_method": "exact_normalized_name", "ambiguity_flag": False},
        {"pandascore_team_id": "3", "historical_team_id": "hist-c", "match_method": "exact_normalized_name", "ambiguity_flag": False},
    ])
    return CorrectedEloEngine(base, ExactIdentityResolver.from_frame(identity, mapping_version="test"))


def test_integrated_reconstruction_advances_across_dates_but_not_within_date(tmp_path):
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    match_d1 = source_match(20, match_date="2026-08-01", winner=2, team_a=1, team_b=2)
    match_d2 = source_match(21, match_date="2026-08-02", winner=3, team_a=1, team_b=3)
    poll_d2 = bundle([match_d1, match_d2])
    ingest_bundle(poll_d2, raw_directory=raw, ledger_path=ledger)
    fixture_d2 = Fixture("d2", "2026-08-02T12:00:00Z", "1", "A", "2", "B")
    forecast_d2 = forecast_from_result_ledger(
        _engine(), fixture_d2, generated_at_utc="2026-08-02T10:00:00Z", ledger_path=ledger
    )
    poll_d3 = bundle(
        [match_d1, match_d2],
        received="2026-08-03T09:01:00Z",
        started="2026-08-03T09:00:00Z",
        completed="2026-08-03T09:02:00Z",
        query_end="2026-08-03T08:59:00Z",
    )
    ingest_bundle(poll_d3, raw_directory=raw, ledger_path=ledger)
    fixture_d3 = Fixture("d3", "2026-08-03T12:00:00Z", "1", "A", "2", "B")
    forecast_d3 = forecast_from_result_ledger(
        _engine(), fixture_d3, generated_at_utc="2026-08-03T10:00:00Z", ledger_path=ledger
    )
    assert forecast_d2["state_through_date"] == "2026-08-01"
    assert forecast_d3["state_through_date"] == "2026-08-02"
    assert forecast_d3["team_a_prior_eligible_matches"] == forecast_d2["team_a_prior_eligible_matches"] + 1
    assert forecast_d3["elo_a"] != forecast_d2["elo_a"]


def test_process_restart_reingestion_is_reproducible(tmp_path):
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    fixed = bundle([source_match(30)])
    first = ingest_bundle(fixed, raw_directory=raw, ledger_path=ledger)
    bytes_after_first = ledger.read_bytes()
    second = ingest_bundle(fixed, raw_directory=raw, ledger_path=ledger)
    assert first["result_versions_appended"] == 1
    assert second["result_versions_appended"] == 0
    assert ledger.read_bytes() == bytes_after_first


def test_fetch_failure_is_isolated_before_any_local_write(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "PER_PAGE", 2)

    def transport(url, headers, query):
        if query["page"] == "1":
            return HttpResponse([source_match(1), source_match(2)], {"X-Total": "3", "X-Total-Pages": "2"})
        raise OSError("temporary page failure")

    client = PandaScoreCompletedClient("secret", transport=transport)
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    with pytest.raises(OSError, match="temporary"):
        poll_once(client, end_at_utc="2026-08-02T08:59:00Z", raw_directory=raw, ledger_path=ledger)
    assert not ledger.exists()
    assert not raw.exists()


def test_one_invalid_match_is_audited_without_blocking_other_results(tmp_path):
    ledger, raw = tmp_path / "results.jsonl", tmp_path / "raw"
    invalid = source_match(41)
    invalid["opponents"] = [{"opponent": {"id": 1, "name": "Only One"}}]
    output = ingest_bundle(bundle([source_match(40), invalid]), raw_directory=raw, ledger_path=ledger)
    versions = [record for record in load_result_ledger(ledger) if record.get("record_type") in results.RESULT_EVENT_TYPES]
    assert output["result_versions_appended"] == 2
    assert len(versions) == 2
    assert sum(record["eligible"] for record in versions) == 1
    assert next(record for record in versions if not record["eligible"])["eligibility_reason"] == "invalid_opponents"
