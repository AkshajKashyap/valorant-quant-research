"""Inactive, separately stored Milestone 10 v2 shadow collector.

The collector deliberately treats the v1 ledger as read-only fixture/market
provenance.  Every run polls the timestamped Milestone 9 result source before
constructing any corrected-Elo forecast.  Nothing in this module is scheduled
or invoked by the v1 runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError

from valorant_quant.completed_matches import (
    INCREMENTAL_BEGIN_AT_UTC,
    PandaScoreCompletedClient,
    PollBundle,
    ingest_bundle,
    load_pandascore_token,
    load_result_ledger,
    pandascore_http_transport,
    forecast_from_result_ledger,
)
from valorant_quant.corrected_elo import CorrectedEloEngine, Fixture, build_default_engine, parse_utc, utc_text
from valorant_quant.prospective import (
    MATERIAL_RESCHEDULE_MINUTES,
    PRIMARY_BOOKMAKER,
    lead_time_minutes,
    reconstruct_eligible_completed_matches,
)


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "v2_shadow_protocol_1"
ACTIVATION_TIMESTAMP_UTC = "2026-09-22T07:00:00Z"
CHECKPOINT_TARGET = 100

V1_LEDGER = ROOT / "data/processed/milestone_6/prospective_ledger.jsonl"
V2_LEDGER = ROOT / "data/processed/milestone_10/v2_shadow_ledger.jsonl"
RESULT_LEDGER = ROOT / "data/processed/milestone_9/completed_match_ledger.jsonl"
RAW_DIRECTORY = ROOT / "data/raw/pandascore_completed_v2/v1"
OPERATIONAL_LOG = ROOT / "data/processed/milestone_10/operational_log.jsonl"
LOCK_PATH = ROOT / "data/processed/milestone_10/v2_shadow.lock"
FROZEN_100 = ROOT / "artifacts/milestone_6_checkpoint_100_dataset.json"

V2_FORECAST = "v2_forecast_generated"
V2_SUPERSEDED = "v2_forecast_superseded"
V2_SCHEDULE = "v2_schedule_updated"
V2_TERMINAL = "v2_terminal_exclusion"


class ConcurrentRunError(RuntimeError):
    """Raised when another v2 one-shot process owns the isolated lock."""


class ActivationError(RuntimeError):
    """Raised when a persistent run is attempted before registration time."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    identifiers = [str(row.get("record_id")) for row in rows]
    if any(value == "None" for value in identifiers) or len(identifiers) != len(set(identifiers)):
        raise ValueError(f"invalid or duplicate record IDs in {path}")
    return rows


def _canonical_line(record: dict[str, Any]) -> bytes:
    forbidden = ("token", "api_key", "authorization", "password", "secret")
    if not record.get("record_id") or not record.get("record_type"):
        raise ValueError("v2 ledger events require record_id and record_type")
    if any(word in str(key).casefold() for key in record for word in forbidden):
        raise ValueError("secrets are forbidden in v2 records")
    return (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _append_batch(path: Path, records: list[dict[str, Any]]) -> int:
    """Validate an entire batch before one append; duplicate IDs are no-ops."""
    if not records:
        return 0
    existing = {str(row["record_id"]) for row in _read_jsonl(path)}
    unseen = [row for row in records if str(row["record_id"]) not in existing]
    new_ids = [str(row["record_id"]) for row in unseen]
    if len(new_ids) != len(set(new_ids)):
        raise ValueError("v2 batch contains duplicate record IDs")
    payload = b"".join(_canonical_line(row) for row in unseen)
    if not payload:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return len(unseen)


@contextmanager
def _collector_lock(path: Path, *, acquired_at_utc: str) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ConcurrentRunError(f"v2 collector lock already exists: {path}") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "acquired_at_utc": acquired_at_utc}, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def retrying_transport(
    transport: Callable = pandascore_http_transport,
    *,
    attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> Callable:
    """Return a bounded retry wrapper for 429/5xx and transient I/O errors."""
    if attempts < 1:
        raise ValueError("attempts must be positive")

    def request(url: str, headers: dict[str, str], query: dict[str, str]):
        for attempt in range(1, attempts + 1):
            try:
                return transport(url, headers, query)
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt == attempts:
                    raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == attempts:
                    raise
            sleeper(float(2 ** (attempt - 1)))
        raise AssertionError("unreachable")

    return request


def _effective_v1_components(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter]:
    """Find active future v1+Bet365 units without calculating any performance."""
    activation = parse_utc(ACTIVATION_TIMESTAMP_UTC)
    superseded_forecasts = {
        str(row.get("forecast_record_id")) for row in records if row.get("record_type") == "forecast_superseded"
    }
    superseded_primaries = {
        str(row.get("primary_record_id")) for row in records if row.get("record_type") == "primary_market_superseded"
    }
    by_match: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for position, row in enumerate(records):
        if row.get("pandascore_match_id") is not None:
            by_match.setdefault(str(row["pandascore_match_id"]), []).append((position, row))
    counters: Counter = Counter()
    active: list[dict[str, Any]] = []
    for match_id, indexed in by_match.items():
        rows = [row for _, row in indexed]
        forecasts = [
            (position, row) for position, row in indexed
            if row.get("record_type") == "forecast_generated" and str(row.get("record_id")) not in superseded_forecasts
        ]
        if len(forecasts) != 1:
            counters["absent_or_ambiguous_v1_forecast"] += 1
            continue
        forecast_position, forecast = forecasts[0]
        try:
            if parse_utc(str(forecast["generated_at_utc"])) < activation:
                counters["pre_activation_v1_forecast"] += 1
                continue
        except (KeyError, TypeError, ValueError):
            counters["invalid_v1_forecast"] += 1
            continue
        if any(row.get("record_type") == "terminal_exclusion" for row in rows):
            counters["v1_terminal"] += 1
            continue
        if any(row.get("record_type") == "outcome_attached" for row in rows):
            counters["outcome_already_observed"] += 1
            continue
        schedules = [row for _, row in indexed if row.get("record_type") == "reschedule"]
        effective_start = str(schedules[-1]["scheduled_start_utc"] if schedules else forecast["scheduled_start_utc"])
        if parse_utc(str(forecast["generated_at_utc"])) >= parse_utc(effective_start):
            counters["v1_forecast_not_prestart_for_effective_schedule"] += 1
            continue
        primaries = [
            row for _, row in indexed
            if row.get("record_type") == "primary_market_selected"
            and str(row.get("record_id")) not in superseded_primaries
            and row.get("scheduled_start_utc") == effective_start
        ]
        if len(primaries) != 1:
            counters["absent_or_ambiguous_primary"] += 1
            continue
        primary = primaries[0]
        candidates = [row for _, row in indexed if row.get("record_id") == primary.get("candidate_record_id")]
        if len(candidates) != 1:
            counters["missing_primary_candidate"] += 1
            continue
        candidate = candidates[0]
        try:
            valid_market = (
                candidate.get("bookmaker") == PRIMARY_BOOKMAKER
                and candidate.get("market_type") == "ML"
                and float(candidate["team_a_decimal_odds"]) > 1
                and float(candidate["team_b_decimal_odds"]) > 1
                and candidate.get("scheduled_start_utc") == effective_start
                and 45 <= lead_time_minutes(str(candidate["captured_at_utc"]), effective_start) <= 75
            )
        except (KeyError, TypeError, ValueError):
            valid_market = False
        if not valid_market:
            counters["invalid_primary_candidate"] += 1
            continue
        active.append({
            "pandascore_match_id": match_id,
            "forecast": forecast,
            "forecast_ledger_position": forecast_position,
            "primary": primary,
            "candidate": candidate,
            "effective_start_utc": effective_start,
        })
    return sorted(
        active,
        key=lambda row: (row["effective_start_utc"], row["forecast_ledger_position"], row["pandascore_match_id"]),
    ), counters


def _protocol_record() -> dict[str, Any]:
    return {
        "record_id": f"v2_protocol_registered:{PROTOCOL_VERSION}",
        "record_type": "v2_protocol_registered",
        "protocol_version": PROTOCOL_VERSION,
        "activation_timestamp_utc": ACTIVATION_TIMESTAMP_UTC,
        "checkpoint_target_eligible_matches": CHECKPOINT_TARGET,
        "comparison_arms": ["frozen_v1", "corrected_v2", "bet365_no_vig"],
        "ordering": ["scheduled_start_utc", "v2_forecast_ledger_position", "pandascore_match_id"],
        "interim_performance_reporting": False,
        "excludes_inspected_v1_checkpoint_100": True,
    }


def _v2_active_forecasts(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    superseded = {
        str(row.get("forecast_record_id")) for row in records if row.get("record_type") == V2_SUPERSEDED
    }
    terminal = {
        str(row.get("pandascore_match_id")) for row in records if row.get("record_type") == V2_TERMINAL
    }
    output: dict[str, dict[str, Any]] = {}
    for row in records:
        match_id = str(row.get("pandascore_match_id"))
        if row.get("record_type") == V2_FORECAST and str(row.get("record_id")) not in superseded and match_id not in terminal:
            if match_id in output:
                raise ValueError(f"multiple active v2 forecasts for {match_id}")
            output[match_id] = row
    return output


def _lifecycle_actions(v1: list[dict[str, Any]], v2: list[dict[str, Any]], *, observed_at_utc: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    existing = {str(row["record_id"]) for row in v2}
    active = _v2_active_forecasts(v2)
    v1_by_match: dict[str, list[dict[str, Any]]] = {}
    for row in v1:
        if row.get("pandascore_match_id") is not None:
            v1_by_match.setdefault(str(row["pandascore_match_id"]), []).append(row)
    for match_id, forecast in active.items():
        rows = v1_by_match.get(match_id, [])
        terminals = [row for row in rows if row.get("record_type") == "terminal_exclusion"]
        if terminals:
            source = terminals[-1]
            record = {
                "record_id": f"v2_terminal:{match_id}:{source['record_id']}",
                "record_type": V2_TERMINAL,
                "pandascore_match_id": match_id,
                "forecast_record_id": forecast["record_id"],
                "source_v1_terminal_record_id": source["record_id"],
                "reason": source.get("reason"),
                "observed_at_utc": observed_at_utc,
            }
            if record["record_id"] not in existing:
                actions.append(record)
            continue
        reschedules = [row for row in rows if row.get("record_type") == "reschedule"]
        if not reschedules:
            continue
        new_start = str(reschedules[-1]["scheduled_start_utc"])
        old_start = str(forecast["scheduled_start_utc"])
        if new_start == old_start:
            continue
        difference = abs((parse_utc(new_start) - parse_utc(old_start)).total_seconds()) / 60
        if difference <= MATERIAL_RESCHEDULE_MINUTES:
            continue
        if parse_utc(new_start).date() == parse_utc(old_start).date():
            if parse_utc(str(forecast["generated_at_utc"])) >= parse_utc(new_start):
                record = {
                    "record_id": f"v2_terminal:{match_id}:reschedule_invalidated_prestart:{new_start}",
                    "record_type": V2_TERMINAL,
                    "pandascore_match_id": match_id,
                    "forecast_record_id": forecast["record_id"],
                    "reason": "reschedule_invalidated_prestart",
                    "observed_at_utc": observed_at_utc,
                }
                if record["record_id"] not in existing:
                    actions.append(record)
                continue
            superseded_primaries = {
                str(row.get("primary_record_id"))
                for row in rows if row.get("record_type") == "primary_market_superseded"
            }
            primaries = [
                row for row in rows
                if row.get("record_type") == "primary_market_selected"
                and str(row.get("record_id")) not in superseded_primaries
                and row.get("scheduled_start_utc") == new_start
            ]
            if len(primaries) != 1:
                continue
            candidates = [row for row in rows if row.get("record_id") == primaries[0].get("candidate_record_id")]
            if len(candidates) != 1:
                continue
            record = {
                "record_id": f"v2_schedule:{forecast['record_id']}:{new_start}",
                "record_type": V2_SCHEDULE,
                "pandascore_match_id": match_id,
                "forecast_record_id": forecast["record_id"],
                "old_scheduled_start_utc": old_start,
                "scheduled_start_utc": new_start,
                "observed_at_utc": observed_at_utc,
                "v1_primary_record_id": primaries[0]["record_id"],
                "v1_market_candidate_record_id": candidates[0]["record_id"],
            }
        else:
            record = {
                "record_id": f"v2_superseded:{forecast['record_id']}:{new_start}",
                "record_type": V2_SUPERSEDED,
                "pandascore_match_id": match_id,
                "forecast_record_id": forecast["record_id"],
                "scheduled_start_utc": new_start,
                "reason": "cross_date_material_reschedule",
                "observed_at_utc": observed_at_utc,
            }
        if record["record_id"] not in existing:
            actions.append(record)
    return actions


def _fixture_from_component(component: dict[str, Any]) -> Fixture:
    forecast = component["forecast"]
    return Fixture(
        str(component["pandascore_match_id"]),
        str(component["effective_start_utc"]),
        str(forecast["team_a_provider_id"]),
        str(forecast.get("team_a") or forecast.get("team_a_name") or ""),
        str(forecast["team_b_provider_id"]),
        str(forecast.get("team_b") or forecast.get("team_b_name") or ""),
    )


def _frozen_checkpoint_ids(path: Path = FROZEN_100) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["pandascore_match_id"]) for row in payload.get("rows", [])}


def reconstruct_three_way_eligible(
    v1_records: list[dict[str, Any]],
    v2_records: list[dict[str, Any]],
    *,
    frozen_checkpoint_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return exact future comparison units; deliberately calculate no metrics."""
    frozen = _frozen_checkpoint_ids() if frozen_checkpoint_ids is None else {str(value) for value in frozen_checkpoint_ids}
    active_v2 = _v2_active_forecasts(v2_records)
    v2_positions = {str(row["record_id"]): position for position, row in enumerate(v2_records)}
    units = []
    for v1_unit in reconstruct_eligible_completed_matches(v1_records):
        match_id = str(v1_unit["pandascore_match_id"])
        v2 = active_v2.get(match_id)
        if v2 is None or match_id in frozen:
            continue
        schedule_updates = [
            row for row in v2_records
            if row.get("record_type") == V2_SCHEDULE
            and row.get("forecast_record_id") == v2.get("record_id")
        ]
        effective_v2 = {**v2, **(schedule_updates[-1] if schedule_updates else {})}
        try:
            valid = (
                parse_utc(str(v2["generated_at_utc"])) >= parse_utc(ACTIVATION_TIMESTAMP_UTC)
                and parse_utc(str(v2["generated_at_utc"])) < parse_utc(str(v1_unit["primary"]["scheduled_start_utc"]))
                and parse_utc(str(v1_unit["forecast"]["generated_at_utc"])) >= parse_utc(ACTIVATION_TIMESTAMP_UTC)
                and parse_utc(str(v1_unit["forecast"]["generated_at_utc"])) < parse_utc(str(v1_unit["primary"]["scheduled_start_utc"]))
                and effective_v2.get("v1_forecast_record_id") == v1_unit["forecast"].get("record_id")
                and effective_v2.get("v1_primary_record_id") == v1_unit["primary"].get("record_id")
                and effective_v2.get("v1_market_candidate_record_id") == v1_unit["candidate"].get("record_id")
                and str(v2.get("pandascore_match_id")) == match_id
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            continue
        units.append({
            "pandascore_match_id": match_id,
            "scheduled_start_utc": v1_unit["primary"]["scheduled_start_utc"],
            "v1_forecast": v1_unit["forecast"],
            "v2_forecast": v2,
            "bet365_primary": v1_unit["candidate"],
            "outcome": v1_unit["outcome"],
            "v2_forecast_ledger_position": v2_positions[str(v2["record_id"])],
        })
    return sorted(
        units,
        key=lambda row: (row["scheduled_start_utc"], row["v2_forecast_ledger_position"], row["pandascore_match_id"]),
    )


def operational_status(
    *,
    v1_ledger: Path = V1_LEDGER,
    v2_ledger: Path = V2_LEDGER,
    result_ledger: Path = RESULT_LEDGER,
    operational_log: Path = OPERATIONAL_LOG,
) -> dict[str, Any]:
    v1, v2, results = _read_jsonl(v1_ledger), _read_jsonl(v2_ledger), load_result_ledger(result_ledger)
    runs = _read_jsonl(operational_log)
    polls = [row for row in results if row.get("record_type") == "v2_results_poll_completed"]
    active = _v2_active_forecasts(v2)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "activation_timestamp_utc": ACTIVATION_TIMESTAMP_UTC,
        "checkpoint_target": CHECKPOINT_TARGET,
        "protocol_registered": any(row.get("record_type") == "v2_protocol_registered" for row in v2),
        "result_polls_completed": len(polls),
        "latest_result_poll_completed_at_utc": polls[-1]["poll_completed_at_utc"] if polls else None,
        "v2_forecasts_total": sum(row.get("record_type") == V2_FORECAST for row in v2),
        "v2_forecasts_active": len(active),
        "v2_terminal_exclusions": sum(row.get("record_type") == V2_TERMINAL for row in v2),
        "v2_forecasts_superseded": sum(row.get("record_type") == V2_SUPERSEDED for row in v2),
        "three_way_eligible_completed": len(reconstruct_three_way_eligible(v1, v2)),
        "collector_runs_succeeded": sum(row.get("record_type") == "v2_operational_run" for row in runs),
        "collector_runs_failed": sum(row.get("record_type") == "v2_operational_failure" for row in runs),
        "fixture_coverage_failures": sum(
            failure.get("error_type") in {"SourceGapError", "SourceNormalizationError"}
            for row in runs if row.get("record_type") == "v2_operational_run"
            for failure in row.get("forecast_failures", [])
        ),
        "performance_metrics_computed": False,
    }


def run_once(
    *,
    dry_run: bool,
    now: datetime | None = None,
    client: PandaScoreCompletedClient | None = None,
    engine: CorrectedEloEngine | None = None,
    v1_ledger: Path = V1_LEDGER,
    v2_ledger: Path = V2_LEDGER,
    result_ledger: Path = RESULT_LEDGER,
    raw_directory: Path = RAW_DIRECTORY,
    operational_log: Path = OPERATIONAL_LOG,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    """Run one isolated results-first collection cycle; dry-run persists nothing."""
    started = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started_text = utc_text(started)
    if not dry_run and started < parse_utc(ACTIVATION_TIMESTAMP_UTC):
        raise ActivationError(f"persistent v2 runs are disabled until {ACTIVATION_TIMESTAMP_UTC}")
    if client is None:
        client = PandaScoreCompletedClient(load_pandascore_token(), transport=retrying_transport())
    engine = engine or build_default_engine()
    v1_records = _read_jsonl(v1_ledger)
    protected_v1_digest = hashlib.sha256(v1_ledger.read_bytes()).hexdigest() if v1_ledger.exists() else None
    effective_lock = Path(tempfile.gettempdir()) / "valorant-quant-v2-shadow-dry-run.lock" if dry_run else lock_path

    with _collector_lock(effective_lock, acquired_at_utc=started_text):
        with tempfile.TemporaryDirectory(prefix="valorant-v2-shadow-") if dry_run else _null_tempdir() as temp:
            active_result_ledger = result_ledger
            active_raw_directory = raw_directory
            if dry_run:
                temporary = Path(temp)
                active_result_ledger = temporary / "completed_match_ledger.jsonl"
                active_raw_directory = temporary / "raw"
                if result_ledger.exists():
                    shutil.copyfile(result_ledger, active_result_ledger)
            try:
                bundle: PollBundle = client.fetch(
                    begin_at_utc=INCREMENTAL_BEGIN_AT_UTC,
                    # A bounded future schedule horizon lets a poll completed
                    # just after UTC midnight prove coverage through its actual
                    # generation time. Future unfinished rows cannot update Elo.
                    end_at_utc=utc_text(started + timedelta(minutes=15)),
                )
                ingestion = ingest_bundle(
                    bundle, raw_directory=active_raw_directory, ledger_path=active_result_ledger
                )
            except Exception as error:
                if not dry_run:
                    failure = {
                        "record_id": "v2_run_failure:" + hashlib.sha256(
                            f"{started_text}:{type(error).__name__}:result_ingestion".encode()
                        ).hexdigest()[:24],
                        "record_type": "v2_operational_failure",
                        "run_started_at_utc": started_text,
                        "stage": "result_ingestion",
                        "error_type": type(error).__name__,
                        "http_status": error.code if isinstance(error, HTTPError) else None,
                    }
                    _append_batch(operational_log, [failure])
                raise
            generated_at = bundle.poll_completed_at_utc
            v2_records = _read_jsonl(v2_ledger)
            actions = [_protocol_record()] if not any(
                row.get("record_type") == "v2_protocol_registered" for row in v2_records
            ) else []
            actions.extend(_lifecycle_actions(v1_records, v2_records, observed_at_utc=generated_at))
            projected = v2_records + actions
            components, skips = _effective_v1_components(v1_records)
            forecast_failures = []
            for component in components:
                match_id = component["pandascore_match_id"]
                if match_id in _v2_active_forecasts(projected):
                    skips["already_has_active_v2"] += 1
                    continue
                fixture = _fixture_from_component(component)
                if parse_utc(generated_at) >= parse_utc(fixture.scheduled_start_utc):
                    skips["not_prestart_at_generation"] += 1
                    continue
                if ingestion["normalization_failures"]:
                    forecast_failures.append({
                        "pandascore_match_id": match_id,
                        "error_type": "SourceNormalizationError",
                        "error": "current complete poll contains normalization failures",
                    })
                    continue
                try:
                    forecast = forecast_from_result_ledger(
                        engine,
                        fixture,
                        generated_at_utc=generated_at,
                        ledger_path=active_result_ledger,
                    )
                except Exception as error:  # isolate one fixture, while recording exact class/message
                    forecast_failures.append({
                        "pandascore_match_id": match_id,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    })
                    continue
                forecast.update({
                    "record_type": V2_FORECAST,
                    "pandascore_match_id": match_id,
                    "protocol_version": PROTOCOL_VERSION,
                    "activation_timestamp_utc": ACTIVATION_TIMESTAMP_UTC,
                    "v1_forecast_record_id": component["forecast"]["record_id"],
                    "v1_primary_record_id": component["primary"]["record_id"],
                    "v1_market_candidate_record_id": component["candidate"]["record_id"],
                    "fixture_schedule_source": "active_v1_lifecycle",
                })
                actions.append(forecast)
                projected.append(forecast)

            if protected_v1_digest != (hashlib.sha256(v1_ledger.read_bytes()).hexdigest() if v1_ledger.exists() else None):
                raise RuntimeError("v1 ledger changed during v2 run")
            appended = 0 if dry_run else _append_batch(v2_ledger, actions)
            summary = {
                "run_started_at_utc": started_text,
                "run_completed_at_utc": generated_at,
                "dry_run": dry_run,
                "protocol_version": PROTOCOL_VERSION,
                "activation_timestamp_utc": ACTIVATION_TIMESTAMP_UTC,
                "source_matches": ingestion["source_matches"],
                "result_versions_appended": 0 if dry_run else ingestion["result_versions_appended"],
                "result_versions_would_append": ingestion["result_versions_appended"] if dry_run else 0,
                "v2_records_appended": appended,
                "v2_records_would_append": len([row for row in actions if row["record_id"] not in {x["record_id"] for x in v2_records}]) if dry_run else 0,
                "action_types": dict(sorted(Counter(row["record_type"] for row in actions).items())),
                "candidate_fixtures": len(components),
                "forecast_failures": forecast_failures,
                "skip_counts": dict(sorted(skips.items())),
                "pagination_pages": len(bundle.pages),
                "pagination_advertised_total": bundle.total_matches_advertised,
                "v1_ledger_sha256": protected_v1_digest,
                "persistent_writes": not dry_run,
            }
            if not dry_run:
                log_record = {
                    **summary,
                    "record_id": "v2_run:" + hashlib.sha256(
                        f"{started_text}:{generated_at}".encode()
                    ).hexdigest()[:24],
                    "record_type": "v2_operational_run",
                }
                _append_batch(operational_log, [log_record])
            return summary


@contextmanager
def _null_tempdir() -> Iterator[None]:
    yield None


def main() -> None:
    parser = argparse.ArgumentParser(description="Milestone 10 inactive v2 shadow collector")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="exercise live inputs with zero persistent writes")
    mode.add_argument("--run", action="store_true", help="perform one persistent cycle after the activation timestamp")
    mode.add_argument("--status", action="store_true", help="show non-performance operational counts")
    args = parser.parse_args()
    output = operational_status() if args.status else run_once(dry_run=args.dry_run)
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
