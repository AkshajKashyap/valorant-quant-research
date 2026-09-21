"""Append-only, timestamped PandaScore completed-match ingestion for v2.

This module is separate from the running v1 collector.  Fetching must finish
and pass pagination validation before any local state is written.  Result
versions are append-only so provider corrections and their availability times
remain reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from valorant_quant.corrected_elo import CorrectedEloEngine, Fixture
from valorant_quant.market_data import normalized_team_name


ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://api.pandascore.co/valorant/matches"
PIPELINE_VERSION = "pandascore_completed_matches_v2_v1"
BRIDGE_STATE_THROUGH_DATE = "2026-07-25"
# One-day scheduled-time overlap is deliberate. Actual begin dates through
# 2026-07-25 remain excluded, while cancellations and schedule corrections at
# the boundary stay observable from the all-status endpoint.
INCREMENTAL_BEGIN_AT_UTC = "2026-07-25T00:00:00Z"
RAW_DIRECTORY = ROOT / "data/raw/pandascore_completed_v2/v1"
RESULT_LEDGER = ROOT / "data/processed/milestone_9/completed_match_ledger.jsonl"
V1_LEDGER = ROOT / "data/processed/milestone_6/prospective_ledger.jsonl"
PER_PAGE = 100

RESULT_EVENT_TYPES = {"v2_result_observed", "v2_result_corrected"}


class IncompletePaginationError(RuntimeError):
    """Raised when a response cannot prove all advertised pages were read."""


class SourceGapError(RuntimeError):
    """Raised when no completed poll covers a requested forecast boundary."""


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must contain a UTC offset")
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


@dataclass(frozen=True)
class HttpResponse:
    body: Any
    headers: dict[str, str]


@dataclass(frozen=True)
class FetchedPage:
    page: int
    received_at_utc: str
    response: list[dict[str, Any]]
    pagination_headers: dict[str, str]


@dataclass(frozen=True)
class PollBundle:
    endpoint: str
    query_begin_at_utc: str
    query_end_at_utc: str
    poll_started_at_utc: str
    poll_completed_at_utc: str
    total_matches_advertised: int
    total_pages_advertised: int
    per_page: int
    pages: tuple[FetchedPage, ...]

    def raw_payload(self) -> dict[str, Any]:
        return {
            "schema_version": PIPELINE_VERSION,
            "endpoint": self.endpoint,
            "query": {
                "range[scheduled_at]": f"{self.query_begin_at_utc},{self.query_end_at_utc}",
                "sort": "scheduled_at",
                "per_page": self.per_page,
            },
            "poll_started_at_utc": self.poll_started_at_utc,
            "poll_completed_at_utc": self.poll_completed_at_utc,
            "total_matches_advertised": self.total_matches_advertised,
            "total_pages_advertised": self.total_pages_advertised,
            "pages": [
                {
                    "page": page.page,
                    "received_at_utc": page.received_at_utc,
                    "pagination_headers": page.pagination_headers,
                    "response": page.response,
                }
                for page in self.pages
            ],
        }


Transport = Callable[[str, dict[str, str], dict[str, str]], HttpResponse]
Clock = Callable[[], datetime]


def _default_transport(url: str, headers: dict[str, str], query: dict[str, str]) -> HttpResponse:
    address = url + "?" + urlencode(query)
    with urlopen(Request(address, headers=headers), timeout=45) as response:
        return HttpResponse(json.loads(response.read()), dict(response.headers.items()))


def _pagination_headers(headers: dict[str, str]) -> dict[str, str]:
    lowered = {str(key).casefold(): str(value) for key, value in headers.items()}
    names = ("x-total", "x-total-pages", "x-page", "x-per-page")
    return {name: lowered[name] for name in names if name in lowered}


def _positive_int(headers: dict[str, str], name: str, *, allow_zero: bool = False) -> int:
    try:
        value = int(headers[name])
    except (KeyError, TypeError, ValueError) as error:
        raise IncompletePaginationError(f"missing or invalid {name} pagination header") from error
    if value < 0 or (value == 0 and not allow_zero):
        raise IncompletePaginationError(f"invalid {name} pagination value: {value}")
    return value


class PandaScoreCompletedClient:
    """Fetch a fixed-boundary, fully paginated snapshot without local writes."""

    def __init__(
        self,
        token: str,
        *,
        transport: Transport = _default_transport,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not token:
            raise ValueError("PandaScore token is required")
        self._token = token
        self._transport = transport
        self._clock = clock

    def fetch(self, *, begin_at_utc: str, end_at_utc: str) -> PollBundle:
        begin = parse_utc(begin_at_utc)
        end = parse_utc(end_at_utc)
        if begin >= end:
            raise ValueError("completed-match range must have begin < end")
        started = self._clock()
        base_query = {
            "range[scheduled_at]": f"{utc_text(begin)},{utc_text(end)}",
            "sort": "scheduled_at",
            "per_page": str(PER_PAGE),
        }
        authorization = {"Authorization": f"Bearer {self._token}"}
        first = self._transport(ENDPOINT, authorization, {**base_query, "page": "1"})
        if not isinstance(first.body, list):
            raise IncompletePaginationError("PandaScore page body must be a JSON list")
        first_headers = _pagination_headers(first.headers)
        total = _positive_int(first_headers, "x-total", allow_zero=True)
        calculated_pages = math.ceil(total / PER_PAGE) if total else 0
        header_pages = int(first_headers["x-total-pages"]) if "x-total-pages" in first_headers else calculated_pages
        if header_pages < 0 or header_pages != calculated_pages:
            raise IncompletePaginationError(
                f"advertised pages {header_pages} do not match total {total} at per_page {PER_PAGE}"
            )
        expected_pages = max(1, header_pages)
        pages = [FetchedPage(1, utc_text(self._clock()), first.body, first_headers)]
        for page_number in range(2, expected_pages + 1):
            response = self._transport(ENDPOINT, authorization, {**base_query, "page": str(page_number)})
            if not isinstance(response.body, list):
                raise IncompletePaginationError(f"PandaScore page {page_number} body must be a JSON list")
            headers = _pagination_headers(response.headers)
            if _positive_int(headers, "x-total", allow_zero=True) != total:
                raise IncompletePaginationError("x-total changed during pagination")
            if "x-total-pages" in headers and int(headers["x-total-pages"]) != header_pages:
                raise IncompletePaginationError("x-total-pages changed during pagination")
            if "x-page" in headers and int(headers["x-page"]) != page_number:
                raise IncompletePaginationError(f"requested page {page_number} but received page {headers['x-page']}")
            pages.append(FetchedPage(page_number, utc_text(self._clock()), response.body, headers))
        identifiers = [str(match.get("id")) for page in pages for match in page.response if match.get("id") is not None]
        returned = sum(len(page.response) for page in pages)
        if returned != total:
            raise IncompletePaginationError(f"advertised {total} matches but received {returned}")
        if len(identifiers) != returned or len(set(identifiers)) != returned:
            raise IncompletePaginationError("pages contain a missing or duplicate stable match ID")
        if len(pages) != expected_pages or [page.page for page in pages] != list(range(1, expected_pages + 1)):
            raise IncompletePaginationError("page sequence is incomplete")
        return PollBundle(
            endpoint=ENDPOINT,
            query_begin_at_utc=utc_text(begin),
            query_end_at_utc=utc_text(end),
            poll_started_at_utc=utc_text(started),
            poll_completed_at_utc=utc_text(self._clock()),
            total_matches_advertised=total,
            total_pages_advertised=header_pages,
            per_page=PER_PAGE,
            pages=tuple(pages),
        )


def validate_poll_bundle(bundle: PollBundle) -> None:
    """Revalidate completeness before a fetched bundle is allowed to persist."""
    if bundle.endpoint != ENDPOINT or bundle.query_begin_at_utc != INCREMENTAL_BEGIN_AT_UTC:
        raise SourceGapError("poll does not use the registered endpoint and fixed coverage boundary")
    expected_pages = max(1, bundle.total_pages_advertised)
    if len(bundle.pages) != expected_pages or [page.page for page in bundle.pages] != list(range(1, expected_pages + 1)):
        raise IncompletePaginationError("poll bundle has an incomplete page sequence")
    matches = [match for page in bundle.pages for match in page.response]
    identifiers = [str(match.get("id")) for match in matches if match.get("id") is not None]
    if len(matches) != bundle.total_matches_advertised:
        raise IncompletePaginationError("poll bundle count differs from advertised total")
    if len(identifiers) != len(matches) or len(set(identifiers)) != len(matches):
        raise IncompletePaginationError("poll bundle lacks unique stable match IDs")
    if bundle.total_pages_advertised != (math.ceil(len(matches) / bundle.per_page) if matches else 0):
        raise IncompletePaginationError("poll bundle page count is inconsistent with total/per_page")
    started, completed = parse_utc(bundle.poll_started_at_utc), parse_utc(bundle.poll_completed_at_utc)
    if started > completed or any(not started <= parse_utc(page.received_at_utc) <= completed for page in bundle.pages):
        raise ValueError("page receipt timestamps must lie inside the poll interval")


def _valid_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return utc_text(parse_utc(value))
    except ValueError:
        return None


def _score_by_team(match: dict[str, Any]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for item in match.get("results") or []:
        if item.get("team_id") is not None and isinstance(item.get("score"), int):
            scores[str(item["team_id"])] = int(item["score"])
    return scores


def normalize_completed_match(
    match: dict[str, Any],
    *,
    observed_at_utc: str,
    raw_snapshot_sha256: str,
    raw_page: int,
) -> dict[str, Any]:
    """Normalize one source match in stable team-ID order, retaining exclusions."""
    if match.get("id") is None:
        raise ValueError("source match has no stable PandaScore ID")
    match_id = str(match["id"])
    opponents = [item.get("opponent") or {} for item in match.get("opponents") or []]
    teams = [item for item in opponents if item.get("id") is not None]
    teams = sorted(teams, key=lambda team: str(team["id"]))
    status = str(match.get("status") or "")
    forfeit = bool(match.get("forfeit"))
    scheduled = _valid_timestamp(match.get("scheduled_at"))
    begin = _valid_timestamp(match.get("begin_at"))
    completed = _valid_timestamp(match.get("end_at"))
    winner_id = str(match["winner_id"]) if match.get("winner_id") is not None else None
    team_ids = [str(team["id"]) for team in teams]
    match_date = parse_utc(begin).date().isoformat() if begin else None
    reason = None
    if status != "finished":
        reason = "not_finished"
    elif forfeit:
        reason = "forfeit_excluded"
    elif len(teams) != 2 or len(set(team_ids)) != 2:
        reason = "invalid_opponents"
    elif any(normalized_team_name(str(team.get("name") or "")) in {"", "tbd"} for team in teams):
        reason = "placeholder_team"
    elif scheduled is None or begin is None or completed is None:
        reason = "missing_or_invalid_timestamp"
    elif match_date <= BRIDGE_STATE_THROUGH_DATE:
        reason = "pre_or_at_bridge_boundary"
    elif parse_utc(completed) > parse_utc(observed_at_utc):
        reason = "completion_after_observation"
    elif winner_id not in team_ids:
        reason = "invalid_winner"
    eligible = reason is None
    scores = _score_by_team(match)
    team_a = teams[0] if len(teams) >= 1 else {}
    team_b = teams[1] if len(teams) >= 2 else {}
    team_a_id = str(team_a["id"]) if team_a.get("id") is not None else None
    team_b_id = str(team_b["id"]) if team_b.get("id") is not None else None
    normalized = {
        "pipeline_version": PIPELINE_VERSION,
        "pandascore_match_id": match_id,
        "match_date": match_date,
        "scheduled_start_utc": scheduled,
        "actual_begin_at_utc": begin,
        "completed_at_utc": completed,
        "team_a_provider_id": team_a_id,
        "team_a_name": str(team_a.get("name") or ""),
        "team_b_provider_id": team_b_id,
        "team_b_name": str(team_b.get("name") or ""),
        "team_a_score": scores.get(team_a_id) if team_a_id else None,
        "team_b_score": scores.get(team_b_id) if team_b_id else None,
        "winner_team_id": winner_id,
        "team_a_won": winner_id == team_a_id if eligible else None,
        "completion_status": status,
        "forfeit": forfeit,
        "eligible": eligible,
        "eligibility_reason": reason,
        "tournament_name": str((match.get("tournament") or {}).get("name") or ""),
        "provider_modified_at_utc": _valid_timestamp(match.get("modified_at")),
        "result_version_observed_at_utc": utc_text(parse_utc(observed_at_utc)),
        "source_endpoint": ENDPOINT,
        "source_snapshot_id": f"pandascore_completed_v2:{raw_snapshot_sha256}",
        "raw_snapshot_sha256": raw_snapshot_sha256,
        "raw_page": raw_page,
    }
    signature_fields = {
        key: normalized[key]
        for key in (
            "match_date", "scheduled_start_utc", "actual_begin_at_utc", "completed_at_utc",
            "team_a_provider_id", "team_a_name", "team_b_provider_id", "team_b_name",
            "team_a_score", "team_b_score", "winner_team_id", "team_a_won", "completion_status",
            "forfeit", "eligible", "eligibility_reason", "tournament_name",
        )
    }
    normalized["result_signature_sha256"] = hashlib.sha256(canonical_json(signature_fields)).hexdigest()
    return normalized


def load_result_ledger(path: Path = RESULT_LEDGER) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    identifiers = [str(record.get("record_id")) for record in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("completed-match ledger contains duplicate record IDs")
    return records


def _append_batch(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    existing = {str(record["record_id"]) for record in load_result_ledger(path)}
    new_ids = [str(record["record_id"]) for record in records]
    if len(new_ids) != len(set(new_ids)) or existing.intersection(new_ids):
        raise ValueError("completed-match ledger append would duplicate a record ID")
    payload = b"".join(canonical_json(record) for record in records)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_raw_bundle(bundle: PollBundle, directory: Path) -> tuple[Path, str]:
    payload = canonical_json(bundle.raw_payload())
    digest = hashlib.sha256(payload).hexdigest()
    timestamp = parse_utc(bundle.poll_completed_at_utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{timestamp}_{digest[:12]}.json"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError(f"raw snapshot path collision: {path}")
    return path, digest


def _latest_results(records: Iterable[dict[str, Any]], *, as_of_utc: str | None = None) -> dict[str, dict[str, Any]]:
    cutoff = parse_utc(as_of_utc) if as_of_utc else None
    latest: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for position, record in enumerate(records):
        if record.get("record_type") not in RESULT_EVENT_TYPES:
            continue
        observed = parse_utc(str(record["result_version_observed_at_utc"]))
        if cutoff is not None and observed > cutoff:
            continue
        match_id = str(record["pandascore_match_id"])
        candidate = (observed, position, record)
        if match_id not in latest or candidate[:2] > latest[match_id][:2]:
            latest[match_id] = candidate
    return {match_id: value[2] for match_id, value in latest.items()}


def ingest_bundle(
    bundle: PollBundle,
    *,
    raw_directory: Path = RAW_DIRECTORY,
    ledger_path: Path = RESULT_LEDGER,
) -> dict[str, Any]:
    """Persist one already-validated poll; identical results append no versions."""
    validate_poll_bundle(bundle)
    raw_path, raw_digest = _write_raw_bundle(bundle, raw_directory)
    existing_records = load_result_ledger(ledger_path)
    latest = _latest_results(existing_records)
    events: list[dict[str, Any]] = []
    normalization_failures = []
    unchanged = 0
    for page in bundle.pages:
        for match in page.response:
            try:
                normalized = normalize_completed_match(
                    match,
                    observed_at_utc=page.received_at_utc,
                    raw_snapshot_sha256=raw_digest,
                    raw_page=page.page,
                )
            except (KeyError, TypeError, ValueError) as error:
                normalization_failures.append({"pandascore_match_id": match.get("id"), "error": str(error)})
                continue
            match_id = normalized["pandascore_match_id"]
            previous = latest.get(match_id)
            if previous and previous["result_signature_sha256"] == normalized["result_signature_sha256"]:
                unchanged += 1
                continue
            observed = normalized["result_version_observed_at_utc"]
            prior_first = previous.get("first_observed_result_at_utc") if previous else None
            first_result = prior_first or (observed if normalized["eligible"] else None)
            event_type = "v2_result_observed" if previous is None else "v2_result_corrected"
            event = {
                **normalized,
                "record_id": (
                    f"v2_result:{match_id}:{parse_utc(observed).strftime('%Y%m%dT%H%M%S%fZ')}:"
                    f"{normalized['result_signature_sha256'][:12]}"
                ),
                "record_type": event_type,
                "first_observed_result_at_utc": first_result,
                "supersedes_record_id": previous.get("record_id") if previous else None,
            }
            events.append(event)
            latest[match_id] = event
    poll_id_material = f"{bundle.poll_completed_at_utc}:{raw_digest}"
    poll_event = {
        "record_id": "v2_results_poll:" + hashlib.sha256(poll_id_material.encode()).hexdigest()[:24],
        "record_type": "v2_results_poll_completed",
        "pipeline_version": PIPELINE_VERSION,
        "poll_started_at_utc": bundle.poll_started_at_utc,
        "poll_completed_at_utc": bundle.poll_completed_at_utc,
        "query_begin_at_utc": bundle.query_begin_at_utc,
        "query_end_at_utc": bundle.query_end_at_utc,
        "total_matches_advertised": bundle.total_matches_advertised,
        "total_pages_advertised": bundle.total_pages_advertised,
        "raw_snapshot_sha256": raw_digest,
        "raw_snapshot_path": str(raw_path),
        "result_versions_appended": len(events),
        "normalization_failures": normalization_failures,
    }
    existing_ids = {str(record["record_id"]) for record in existing_records}
    to_append = events + ([] if poll_event["record_id"] in existing_ids else [poll_event])
    _append_batch(ledger_path, to_append)
    return {
        "raw_snapshot_path": str(raw_path),
        "raw_snapshot_sha256": raw_digest,
        "source_matches": bundle.total_matches_advertised,
        "result_versions_appended": len(events),
        "unchanged_results": unchanged,
        "normalization_failures": normalization_failures,
        "poll_event_appended": poll_event["record_id"] not in existing_ids,
    }


def poll_once(
    client: PandaScoreCompletedClient,
    *,
    end_at_utc: str,
    raw_directory: Path = RAW_DIRECTORY,
    ledger_path: Path = RESULT_LEDGER,
) -> dict[str, Any]:
    """Fetch completely before persistence so HTTP/page failures leave no local poll state."""
    bundle = client.fetch(begin_at_utc=INCREMENTAL_BEGIN_AT_UTC, end_at_utc=end_at_utc)
    return ingest_bundle(bundle, raw_directory=raw_directory, ledger_path=ledger_path)


def materialize_results_as_of(
    records: list[dict[str, Any]],
    *,
    as_of_utc: str,
) -> pd.DataFrame:
    """Materialize latest known version per match without changing first observation."""
    latest = _latest_results(records, as_of_utc=as_of_utc)
    rows = []
    for match_id, record in sorted(latest.items()):
        match_date = record.get("match_date")
        if record.get("eligible") and match_date and match_date <= BRIDGE_STATE_THROUGH_DATE:
            raise SourceGapError(f"incremental result {match_id} overlaps frozen bridge boundary")
        rows.append({
            "pandascore_match_id": match_id,
            "match_date": match_date,
            "team_a_provider_id": record.get("team_a_provider_id"),
            "team_a_name": record.get("team_a_name"),
            "team_b_provider_id": record.get("team_b_provider_id"),
            "team_b_name": record.get("team_b_name"),
            "team_a_won": record.get("team_a_won"),
            "tournament_name": record.get("tournament_name"),
            "source_snapshot_id": record.get("source_snapshot_id"),
            "completion_status": record.get("completion_status"),
            "forfeit": record.get("forfeit"),
            "eligible": bool(record.get("eligible")),
            "completed_at_utc": record.get("completed_at_utc"),
            "result_available_at_utc": record.get("result_version_observed_at_utc"),
            "first_observed_result_at_utc": record.get("first_observed_result_at_utc"),
            "result_record_id": record.get("record_id"),
        })
    columns = [
        "pandascore_match_id", "match_date", "team_a_provider_id", "team_a_name",
        "team_b_provider_id", "team_b_name", "team_a_won", "tournament_name",
        "source_snapshot_id", "completion_status", "forfeit", "eligible", "completed_at_utc",
        "result_available_at_utc", "first_observed_result_at_utc", "result_record_id",
    ]
    return pd.DataFrame(rows, columns=columns)


def require_coverage_for_forecast(
    records: list[dict[str, Any]],
    *,
    fixture: Fixture,
    generated_at_utc: str,
) -> dict[str, Any]:
    generated = parse_utc(generated_at_utc)
    required_end = datetime.combine(parse_utc(fixture.scheduled_start_utc).date(), datetime.min.time(), tzinfo=timezone.utc)
    polls = [
        record for record in records
        if record.get("record_type") == "v2_results_poll_completed"
        and record.get("query_begin_at_utc") == INCREMENTAL_BEGIN_AT_UTC
        and parse_utc(str(record["poll_completed_at_utc"])) <= generated
        and parse_utc(str(record["query_end_at_utc"])) >= required_end
        and not record.get("normalization_failures")
    ]
    if not polls:
        raise SourceGapError(
            f"no complete fixed-boundary result poll covers all dates before fixture {fixture.fixture_id}"
        )
    return max(polls, key=lambda record: parse_utc(str(record["poll_completed_at_utc"])))


def forecast_from_result_ledger(
    engine: CorrectedEloEngine,
    fixture: Fixture,
    *,
    generated_at_utc: str,
    ledger_path: Path = RESULT_LEDGER,
) -> dict[str, Any]:
    """Feed an as-of result view to v2 only after coverage is proven."""
    records = load_result_ledger(ledger_path)
    coverage = require_coverage_for_forecast(records, fixture=fixture, generated_at_utc=generated_at_utc)
    frame = materialize_results_as_of(records, as_of_utc=generated_at_utc)
    forecast = engine.forecast(fixture, generated_at_utc=generated_at_utc, completed_matches=frame)
    forecast["completed_results_poll_record_id"] = coverage["record_id"]
    forecast["completed_results_raw_snapshot_sha256"] = coverage["raw_snapshot_sha256"]
    return forecast


def inspect_source_feasibility(
    bundle: PollBundle,
    *,
    known_reference_match_ids: set[str] | None = None,
) -> dict[str, Any]:
    validate_poll_bundle(bundle)
    matches = [match for page in bundle.pages for match in page.response]
    finished = [match for match in matches if match.get("status") == "finished"]
    stable_ids = [match.get("id") for match in matches]
    stable_id_text = {str(identifier) for identifier in stable_ids}
    known_ids = known_reference_match_ids or set()
    missing_known_ids = sorted(known_ids - stable_id_text)
    scheduled = sum(_valid_timestamp(match.get("scheduled_at")) is not None for match in matches)
    completion = sum(_valid_timestamp(match.get("end_at")) is not None for match in matches)
    two_teams = 0
    valid_winners = 0
    eligibility = Counter()
    page_lookup = {
        str(match["id"]): page.received_at_utc
        for page in bundle.pages for match in page.response if match.get("id") is not None
    }
    for match in matches:
        teams = [item.get("opponent") or {} for item in match.get("opponents") or []]
        ids = {team.get("id") for team in teams if team.get("id") is not None}
        if len(teams) == 2 and len(ids) == 2:
            two_teams += 1
        if match.get("winner_id") in ids:
            valid_winners += 1
        normalized = normalize_completed_match(
            match,
            observed_at_utc=page_lookup[str(match["id"])],
            raw_snapshot_sha256="feasibility-only",
            raw_page=0,
        )
        eligibility["eligible" if normalized["eligible"] else str(normalized["eligibility_reason"])] += 1
    begin_values = sorted(value for match in matches if (value := _valid_timestamp(match.get("begin_at"))))
    end_values = sorted(value for match in matches if (value := _valid_timestamp(match.get("end_at"))))
    sufficient = bool(matches) and all((
        len(set(stable_ids)) == len(matches),
        bundle.query_begin_at_utc == INCREMENTAL_BEGIN_AT_UTC,
        not missing_known_ids,
    ))
    return {
        "checked_at_utc": bundle.poll_completed_at_utc,
        "endpoint": bundle.endpoint,
        "query_begin_at_utc": bundle.query_begin_at_utc,
        "query_end_at_utc": bundle.query_end_at_utc,
        "http_success_is_not_the_completeness_criterion": True,
        "pagination_validated": True,
        "advertised_total": bundle.total_matches_advertised,
        "pages_read": len(bundle.pages),
        "stable_unique_ids": len(set(stable_ids)),
        "known_v1_outcome_ids_checked": len(known_ids),
        "known_v1_outcome_ids_missing": missing_known_ids,
        "finished_status": len(finished),
        "status_counts": dict(sorted(Counter(str(match.get("status")) for match in matches).items())),
        "scheduled_timestamp_present": scheduled,
        "completion_timestamp_present": completion,
        "two_distinct_teams": two_teams,
        "winner_among_teams": valid_winners,
        "eligible_for_elo": eligibility.get("eligible", 0),
        "mechanical_exclusions": {
            reason: count for reason, count in sorted(eligibility.items()) if reason != "eligible"
        },
        "earliest_begin_at_utc": begin_values[0] if begin_values else None,
        "latest_begin_at_utc": begin_values[-1] if begin_values else None,
        "latest_completion_at_utc": end_values[-1] if end_values else None,
        "first_observed_result_timestamp_source": "local authenticated page receipt time; never PandaScore end_at",
        "sufficient_for_v2_results_pipeline": sufficient,
        "limitations": [
            "PandaScore does not supply the local first-observed timestamp; the pipeline must capture it.",
            "A successful poll proves complete pagination for the requested endpoint/range, not matches absent from PandaScore itself.",
            "Provider corrections require repeated fixed-boundary polling and append-only version records.",
        ],
    }


def load_pandascore_token() -> str:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    token = os.getenv("PANDASCORE_TOKEN") or values.get("PANDASCORE_TOKEN")
    if not token:
        raise RuntimeError("PANDASCORE_TOKEN is required")
    return token


def live_feasibility_check(*, now: datetime | None = None) -> dict[str, Any]:
    """Read the live source into memory only; do not persist or activate ingestion."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    token = load_pandascore_token()
    bundle = PandaScoreCompletedClient(token).fetch(
        begin_at_utc=INCREMENTAL_BEGIN_AT_UTC,
        end_at_utc=utc_text(current),
    )
    known_ids: set[str] = set()
    if V1_LEDGER.exists():
        known_ids = {
            str(record["pandascore_match_id"])
            for record in (
                json.loads(line) for line in V1_LEDGER.read_text(encoding="utf-8").splitlines() if line
            )
            if record.get("record_type") == "outcome_attached" and record.get("pandascore_match_id") is not None
        }
    result = inspect_source_feasibility(bundle, known_reference_match_ids=known_ids)
    missing_details = []
    for match_id in result["known_v1_outcome_ids_missing"]:
        response = _default_transport(
            f"https://api.pandascore.co/matches/{match_id}",
            {"Authorization": f"Bearer {token}"},
            {},
        )
        match = response.body if isinstance(response.body, dict) else {}
        missing_details.append({
            "pandascore_match_id": match_id,
            "returned_by_direct_id_lookup": bool(match),
            "status": match.get("status"),
            "scheduled_at": match.get("scheduled_at"),
            "begin_at": match.get("begin_at"),
            "end_at": match.get("end_at"),
            "winner_id": match.get("winner_id"),
            "videogame": (match.get("videogame") or {}).get("name"),
        })
    result["missing_known_id_direct_lookup"] = missing_details
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Milestone 9 completed-match source utilities")
    parser.add_argument("--live-feasibility", action="store_true")
    args = parser.parse_args()
    if not args.live_feasibility:
        parser.error("only the read-only --live-feasibility action is available; ingestion is not activated")
    print(json.dumps(live_feasibility_check(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
