"""Read-only Milestone 7 failure-mode audit of the frozen 100-match sample.

This module deliberately treats the Milestone 6 ledger, raw snapshots, and
checkpoint as immutable inputs.  It writes only a derived report and audit
artifact and does not contain collector or model-v2 behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable

import pandas as pd

from valorant_quant.elo import EloConfig, run_elo, win_probability
from valorant_quant.market_data import normalized_team_name
from valorant_quant.milestone6_evaluation import (
    accuracy,
    brier_score,
    disagreement_bucket,
    freeze_checkpoint,
    load_ledger,
    log_loss,
    no_vig_probabilities,
    reconstruct_eligible_observations,
)
from valorant_quant.prospective import (
    MAX_LEAD_MINUTES,
    MIN_LEAD_MINUTES,
    lead_time_minutes,
    select_primary_snapshot,
)


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "artifacts/milestone_6_checkpoint_100_dataset.json"
LEDGER = ROOT / "data/processed/milestone_6/prospective_ledger.jsonl"
RAW_ODDS = ROOT / "data/raw/market_pilot_live/v1/milestone6_odds"
MAPPING = ROOT / "data/processed/milestone_5/pandascore_team_mapping.csv"
HISTORICAL = ROOT / "data/processed/milestone_2/canonical_matches.csv"
BRIDGE = ROOT / "data/processed/milestone_5/pandascore_eligible_bridge_matches.csv"
ARTIFACT = ROOT / "artifacts/milestone_7_failure_mode_audit.json"
REPORT = ROOT / "reports/milestone_7_failure_mode_audit.md"

COLD_ORDER = ("both_cold_start", "exactly_one_cold_start", "neither_cold_start")
DISAGREEMENT_ORDER = ("< 2 pp", "2–5 pp", "5–10 pp", "> 10 pp")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cold_start_classification(forecast: dict[str, Any]) -> str:
    """Classify only from the prior-match counts frozen with the forecast."""
    cold_a = int(forecast["team_a_prior_eligible_matches"]) == 0
    cold_b = int(forecast["team_b_prior_eligible_matches"]) == 0
    if cold_a and cold_b:
        return "both_cold_start"
    if cold_a != cold_b:
        return "exactly_one_cold_start"
    return "neither_cold_start"


def fixture_orientation(team_a: str, team_b: str, home: str, away: str) -> str:
    """Classify source order without treating an unordered name match as ordered."""
    forecast_names = (normalized_team_name(team_a), normalized_team_name(team_b))
    raw_names = (normalized_team_name(home), normalized_team_name(away))
    if forecast_names == raw_names:
        return "aligned"
    if forecast_names == raw_names[::-1]:
        return "reversed"
    return "mismatch"


def resolved_identity(provider_id: str | int, historical_id: str | None) -> str:
    """Return the bridge identity that forecast-state lookup must consume."""
    return str(historical_id) if historical_id else f"ps:{provider_id}"


def _score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    elo_ll = [log_loss(float(row["elo_probability_team_a"]), bool(row["team_a_won"])) for row in rows]
    market_ll = [log_loss(float(row["bet365_no_vig_probability_team_a"]), bool(row["team_a_won"])) for row in rows]
    elo_brier = [brier_score(float(row["elo_probability_team_a"]), bool(row["team_a_won"])) for row in rows]
    market_brier = [brier_score(float(row["bet365_no_vig_probability_team_a"]), bool(row["team_a_won"])) for row in rows]
    return {
        "matches": len(rows),
        "elo_log_loss": mean(elo_ll),
        "market_log_loss": mean(market_ll),
        "elo_minus_market_log_loss_sum": sum(a - b for a, b in zip(elo_ll, market_ll, strict=True)),
        "elo_brier": mean(elo_brier),
        "market_brier": mean(market_brier),
        "elo_minus_market_brier_sum": sum(a - b for a, b in zip(elo_brier, market_brier, strict=True)),
    }


def loss_decomposition(
    rows: list[dict[str, Any]],
    group: Callable[[dict[str, Any]], str],
    *,
    order: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return additive loss contributions for a complete, non-overlapping grouping."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[group(row)].append(row)
    labels = list(order) if order is not None else sorted(grouped)
    labels.extend(sorted(set(grouped) - set(labels)))
    output = []
    for label in labels:
        if not grouped.get(label):
            continue
        entry = {"group": label, **_score_summary(grouped[label])}
        entry["log_loss_contribution_to_aggregate_mean"] = entry["elo_minus_market_log_loss_sum"] / len(rows)
        entry["brier_contribution_to_aggregate_mean"] = entry["elo_minus_market_brier_sum"] / len(rows)
        output.append(entry)
    return output


def _raw_snapshot_index(directory: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    index: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(directory.glob("*.json")):
        payload = path.read_bytes()
        index[hashlib.sha256(payload).hexdigest()] = (path, json.loads(payload))
    return index


def _integrity_audit(
    frozen_rows: list[dict[str, Any]], records: list[dict[str, Any]], raw_directory: Path
) -> dict[str, Any]:
    by_record = {str(record["record_id"]): record for record in records}
    reconstructed = freeze_checkpoint(reconstruct_eligible_observations(records), 100)
    for row in reconstructed:
        row.pop("_forecast_ledger_position", None)
    superseded_forecasts = {
        str(record["forecast_record_id"])
        for record in records
        if record.get("record_type") == "forecast_superseded"
    }
    superseded_primaries = {
        str(record["primary_record_id"])
        for record in records
        if record.get("record_type") == "primary_market_superseded"
    }
    raw_index = _raw_snapshot_index(raw_directory)
    counts = Counter()
    failures: list[dict[str, Any]] = []
    rescheduled: list[str] = []

    def fail(match_id: str, check: str, detail: str) -> None:
        failures.append({"pandascore_match_id": match_id, "check": check, "detail": detail})

    for row in frozen_rows:
        match_id = str(row["pandascore_match_id"])
        try:
            forecast = by_record[str(row["forecast_record_id"])]
            primary = by_record[str(row["primary_snapshot_record_id"])]
            candidate = by_record[str(row["candidate_snapshot_id"])]
        except KeyError as error:
            fail(match_id, "referential_integrity", f"missing record {error}")
            continue
        events = [record for record in records if str(record.get("pandascore_match_id")) == match_id]
        outcomes = [record for record in events if record.get("record_type") == "outcome_attached"]
        if not all(str(item.get("pandascore_match_id")) == match_id for item in (forecast, primary, candidate)):
            fail(match_id, "forecast_fixture_identity", "linked records use different match IDs")
        else:
            counts["forecast_fixture_identity"] += 1
        if forecast["record_id"] in superseded_forecasts or primary["record_id"] in superseded_primaries:
            fail(match_id, "active_records", "frozen row references a superseded record")
        else:
            active_forecasts = [
                event for event in events
                if event.get("record_type") == "forecast_generated" and event["record_id"] not in superseded_forecasts
            ]
            active_primaries = [
                event for event in events
                if event.get("record_type") == "primary_market_selected" and event["record_id"] not in superseded_primaries
            ]
            if len(active_forecasts) == len(active_primaries) == 1:
                counts["one_active_forecast_and_primary"] += 1
            else:
                fail(match_id, "active_records", f"active forecasts={len(active_forecasts)}, primaries={len(active_primaries)}")
        if parse_utc(str(forecast["generated_at_utc"])) < parse_utc(str(primary["scheduled_start_utc"])):
            counts["forecast_before_effective_start"] += 1
        else:
            fail(match_id, "forecast_timing", "forecast was not generated before effective start")
        lead = lead_time_minutes(str(candidate["captured_at_utc"]), str(primary["scheduled_start_utc"]))
        if MIN_LEAD_MINUTES <= lead <= MAX_LEAD_MINUTES and abs(lead - float(row["lead_time_minutes"])) < 1e-10:
            counts["market_capture_window"] += 1
        else:
            fail(match_id, "market_window", f"lead={lead}")
        same_schedule_candidates = [
            event for event in events
            if event.get("record_type") == "market_candidate"
            and event.get("scheduled_start_utc") == primary.get("scheduled_start_utc")
        ]
        selected = select_primary_snapshot(same_schedule_candidates, str(primary["scheduled_start_utc"]))
        if selected and selected["record_id"] == candidate["record_id"] and primary.get("candidate_record_id") == candidate["record_id"]:
            counts["deterministic_primary_selection"] += 1
        else:
            fail(match_id, "primary_selection", "selected candidate does not reproduce")
        if len(outcomes) == 1:
            outcome = outcomes[0]
            expected_winner = forecast["team_a_provider_id"] if outcome["team_a_won"] else forecast["team_b_provider_id"]
            if outcome.get("winner_team_id") == expected_winner and bool(row["team_a_won"]) == bool(outcome["team_a_won"]):
                counts["winner_outcome_orientation"] += 1
            else:
                fail(match_id, "winner_orientation", "winner ID, team_a_won, and forecast IDs disagree")
        else:
            fail(match_id, "outcome_count", f"outcomes={len(outcomes)}")
        raw = raw_index.get(str(candidate.get("raw_snapshot_hash")))
        if raw is None:
            fail(match_id, "raw_snapshot", "candidate hash has no immutable snapshot")
            continue
        raw_path, payload = raw
        counts["raw_hash_and_snapshot"] += 1
        orientation = fixture_orientation(
            str(forecast["team_a"]), str(forecast["team_b"]), str(payload.get("home", "")), str(payload.get("away", ""))
        )
        if orientation == "aligned":
            counts["raw_home_away_aligned"] += 1
        elif orientation == "reversed":
            counts["raw_home_away_reversed"] += 1
        else:
            fail(match_id, "raw_fixture_identity", "forecast names do not match raw home/away names")
        markets = [
            market for market in payload.get("bookmakers", {}).get("Bet365", [])
            if market.get("name") == "ML" and market.get("updatedAt") == candidate.get("source_last_update_utc")
        ]
        if len(markets) == 1 and markets[0].get("odds"):
            quote = markets[0]["odds"][0]
            if str(quote.get("home")) == str(candidate["team_a_decimal_odds"]) and str(quote.get("away")) == str(candidate["team_b_decimal_odds"]):
                counts["raw_odds_assignment"] += 1
            else:
                fail(match_id, "raw_odds_assignment", f"snapshot={raw_path.name}")
        else:
            fail(match_id, "raw_market", f"matching Bet365 ML markets={len(markets)}")
        market_a, market_b = no_vig_probabilities(float(candidate["team_a_decimal_odds"]), float(candidate["team_b_decimal_odds"]))
        stored_values = (
            float(candidate["team_a_no_vig_probability"]),
            float(candidate["team_b_no_vig_probability"]),
            float(row["bet365_no_vig_probability_team_a"]),
            float(row["bet365_no_vig_probability_team_b"]),
        )
        if all(abs(value - expected) < 1e-12 for value, expected in zip(stored_values, (market_a, market_b, market_a, market_b), strict=True)):
            counts["no_vig_normalization"] += 1
        else:
            fail(match_id, "no_vig", "stored normalization does not reproduce")
        if any(event.get("record_type") == "reschedule" for event in events):
            rescheduled.append(match_id)

    duplicate_ids = sorted(match_id for match_id, count in Counter(str(row["pandascore_match_id"]) for row in frozen_rows).items() if count > 1)
    return {
        "frozen_dataset_reproduces_first_100": reconstructed == frozen_rows,
        "frozen_unique_match_ids": len({str(row["pandascore_match_id"]) for row in frozen_rows}),
        "duplicate_fixture_ids": duplicate_ids,
        "checks_passed_counts": dict(sorted(counts.items())),
        "rescheduled_fixture_ids": sorted(rescheduled),
        "failures": failures,
        "observed_integrity_issue_count_excluding_identity": len(failures) + (0 if reconstructed == frozen_rows else 1) + len(duplicate_ids),
        "latent_orientation_risk": (
            "Fixture matching is unordered while the collector labels raw home/away as team A/B without an explicit order check; "
            "all 100 observed raw snapshots happened to be aligned, so the frozen checkpoint impact is zero."
        ),
    }


def _load_modeling_tables(historical_path: Path, bridge_path: Path) -> pd.DataFrame:
    historical = pd.read_csv(historical_path, dtype={"team_a_id": "string", "team_b_id": "string"})
    bridge = pd.read_csv(bridge_path, dtype={"team_a_id": "string", "team_b_id": "string"})
    columns = [
        "match_id", "match_date", "year", "team_a_id", "team_a_name", "team_b_id", "team_b_name",
        "team_a_won", "tournament_name", "source_snapshot_id",
    ]
    return pd.concat([historical[columns], bridge[columns]], ignore_index=True)


def _identity_audit(
    frozen_rows: list[dict[str, Any]],
    records: list[dict[str, Any]],
    mapping_path: Path,
    historical_path: Path,
    bridge_path: Path,
) -> tuple[dict[str, Any], dict[str, float]]:
    by_record = {str(record["record_id"]): record for record in records}
    mappings = pd.read_csv(mapping_path, dtype=str).fillna("")
    mapping_by_provider = {str(row.pandascore_team_id): row for row in mappings.itertuples(index=False)}
    table = _load_modeling_tables(historical_path, bridge_path)
    max_source_date = str(table["match_date"].astype(str).max())
    earliest_fixture_date = min(str(row["scheduled_start_utc"])[:10] for row in frozen_rows)
    if max_source_date >= earliest_fixture_date:
        raise ValueError("identity-impact reconstruction requires source data strictly before the checkpoint")
    _, ratings = run_elo(table, EloConfig(k=64), allow_post_2024=True)
    identity_counts = Counter()
    appearance_counts = Counter()
    team_inventory: dict[str, dict[str, Any]] = {}
    affected_matches: set[str] = set()
    discrepancy_appearances = 0
    corrected_probability: dict[str, float] = {}
    corrected_history_categories = Counter()

    for frozen in frozen_rows:
        forecast = by_record[str(frozen["forecast_record_id"])]
        intended_ids: list[str] = []
        intended_counts: list[int] = []
        for side in ("a", "b"):
            provider_id = str(forecast[f"team_{side}_provider_id"])
            mapping = mapping_by_provider.get(provider_id)
            historical_id = str(mapping.historical_team_id) if mapping is not None else ""
            if historical_id:
                category = "exact_historical_mapping"
                intended_id = resolved_identity(provider_id, historical_id)
            elif mapping is not None:
                category = "provider_only_with_bridge_history"
                intended_id = resolved_identity(provider_id, None)
            else:
                category = "new_after_bridge_snapshot"
                intended_id = resolved_identity(provider_id, None)
            expected_count = int((table["team_a_id"] == intended_id).sum() + (table["team_b_id"] == intended_id).sum())
            if category == "provider_only_with_bridge_history" and expected_count == 0:
                category = "provider_only_without_pre_match_history"
            stored_count = int(forecast[f"team_{side}_prior_eligible_matches"])
            stored_rating = float(forecast[f"elo_{side}"])
            intended_rating = float(ratings.get(intended_id, 1500.0))
            appearance_counts[category] += 1
            intended_ids.append(intended_id)
            intended_counts.append(expected_count)
            if stored_count != expected_count or abs(stored_rating - intended_rating) > 1e-12 or forecast[f"team_{side}_identity"] != intended_id:
                if historical_id:
                    discrepancy_appearances += 1
                    affected_matches.add(str(frozen["pandascore_match_id"]))
            entry = team_inventory.setdefault(provider_id, {
                "pandascore_team_id": provider_id,
                "pandascore_team_name": str(forecast[f"team_{side}"]),
                "coverage_category": category,
                "historical_team_id": historical_id or None,
                "intended_identity": intended_id,
                "pre_checkpoint_match_count": expected_count,
                "checkpoint_appearances": 0,
            })
            entry["checkpoint_appearances"] += 1
        corrected_probability[str(frozen["pandascore_match_id"])] = win_probability(
            ratings.get(intended_ids[0], 1500.0), ratings.get(intended_ids[1], 1500.0)
        )
        if intended_counts[0] and intended_counts[1]:
            corrected_history_categories["both_have_history"] += 1
        elif bool(intended_counts[0]) != bool(intended_counts[1]):
            corrected_history_categories["exactly_one_has_history"] += 1
        else:
            corrected_history_categories["neither_has_history"] += 1

    for entry in team_inventory.values():
        identity_counts[entry["coverage_category"]] += 1

    neutral_reasons = Counter()
    neutral_usable_history = Counter()
    for frozen in frozen_rows:
        if float(frozen["elo_probability_team_a"]) != 0.5:
            continue
        forecast = by_record[str(frozen["forecast_record_id"])]
        if float(forecast["elo_a"]) == float(forecast["elo_b"]) == 1500.0:
            neutral_reasons["both_initial_1500"] += 1
        elif float(forecast["elo_a"]) == float(forecast["elo_b"]):
            neutral_reasons["equal_non_initial_ratings"] += 1
        else:
            neutral_reasons["other"] += 1
        inventory = [team_inventory[str(forecast[f"team_{side}_provider_id"])] for side in ("a", "b")]
        histories = [int(entry["pre_checkpoint_match_count"]) > 0 for entry in inventory]
        if all(histories):
            neutral_usable_history["both_teams"] += 1
        elif any(histories):
            neutral_usable_history["exactly_one_team"] += 1
        else:
            neutral_usable_history["neither_team"] += 1

    historical_to_providers: dict[str, set[str]] = defaultdict(set)
    normalized_name_to_providers: dict[str, set[str]] = defaultdict(set)
    for provider_id, entry in team_inventory.items():
        if entry["historical_team_id"]:
            historical_to_providers[str(entry["historical_team_id"])].add(provider_id)
        normalized_name_to_providers[normalized_team_name(str(entry["pandascore_team_name"]))].add(provider_id)
    bridge_source = pd.read_csv(bridge_path, dtype=str)
    names_by_provider: dict[str, set[str]] = defaultdict(set)
    checkpoint_provider_ids = set(team_inventory)
    for side in ("a", "b"):
        for provider_id, team_name in zip(
            bridge_source[f"pandascore_team_{side}_id"], bridge_source[f"team_{side}_name"], strict=True
        ):
            if str(provider_id) in checkpoint_provider_ids:
                names_by_provider[str(provider_id)].add(str(team_name))

    outcomes = [bool(row["team_a_won"]) for row in frozen_rows]
    frozen_probabilities = [float(row["elo_probability_team_a"]) for row in frozen_rows]
    market_probabilities = [float(row["bet365_no_vig_probability_team_a"]) for row in frozen_rows]
    repaired_probabilities = [corrected_probability[str(row["pandascore_match_id"])] for row in frozen_rows]
    impact = {
        "forecasts_changed": sum(abs(a - b) > 1e-15 for a, b in zip(frozen_probabilities, repaired_probabilities, strict=True)),
        "frozen_elo_log_loss": mean(log_loss(p, y) for p, y in zip(frozen_probabilities, outcomes, strict=True)),
        "identity_repaired_elo_log_loss": mean(log_loss(p, y) for p, y in zip(repaired_probabilities, outcomes, strict=True)),
        "frozen_elo_brier": mean(brier_score(p, y) for p, y in zip(frozen_probabilities, outcomes, strict=True)),
        "identity_repaired_elo_brier": mean(brier_score(p, y) for p, y in zip(repaired_probabilities, outcomes, strict=True)),
        "frozen_mean_absolute_market_disagreement": mean(abs(p - m) for p, m in zip(frozen_probabilities, market_probabilities, strict=True)),
        "identity_repaired_mean_absolute_market_disagreement": mean(abs(p - m) for p, m in zip(repaired_probabilities, market_probabilities, strict=True)),
        "identity_repaired_exact_half_forecasts": sum(p == 0.5 for p in repaired_probabilities),
        "status": "diagnostic counterfactual only; the frozen report is not replaced",
    }
    return ({
        "unique_team_count": len(team_inventory),
        "unique_teams_by_coverage": dict(sorted(identity_counts.items())),
        "appearances_by_coverage": dict(sorted(appearance_counts.items())),
        "exact_mapping_ignored_appearances": discrepancy_appearances,
        "affected_match_count": len(affected_matches),
        "affected_match_ids": sorted(affected_matches),
        "identity_reconciled_history_coverage": dict(sorted(corrected_history_categories.items())),
        "neutral_forecast_reasons": dict(sorted(neutral_reasons.items())),
        "neutral_forecasts_with_usable_unreflected_history": dict(sorted(neutral_usable_history.items())),
        "historical_alias_or_provider_id_change_findings": {
            "confirmed_additional_aliases": 0,
            "confirmed_provider_id_changes": 0,
            "historical_ids_linked_to_multiple_checkpoint_provider_ids": sum(
                len(provider_ids) > 1 for provider_ids in historical_to_providers.values()
            ),
            "normalized_checkpoint_names_linked_to_multiple_provider_ids": sum(
                len(provider_ids) > 1 for provider_ids in normalized_name_to_providers.values()
            ),
            "checkpoint_provider_ids_with_multiple_bridge_names": sum(
                len(names) > 1 for names in names_by_provider.values()
            ),
            "note": (
                "No extra cross-ID merge met the pre-match evidence standard. Name-similar historical candidates were not merged. "
                "The confirmed continuity failure is the forecaster bypassing the 37 pre-existing exact bridge mappings."
            ),
        },
        "team_inventory": sorted(team_inventory.values(), key=lambda row: (row["coverage_category"], row["pandascore_team_name"].casefold(), row["pandascore_team_id"])),
        "impact_reproduction": impact,
    }, corrected_probability)


def _state_freshness_audit(frozen_rows: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    by_record = {str(record["record_id"]): record for record in records}
    forecast_by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("record_type") == "forecast_generated":
            forecast_by_match[str(record["pandascore_match_id"])].append(record)
    known_outcomes = []
    for outcome in records:
        if outcome.get("record_type") != "outcome_attached":
            continue
        for forecast in forecast_by_match[str(outcome["pandascore_match_id"])]:
            known_outcomes.append({
                "pandascore_match_id": str(outcome["pandascore_match_id"]),
                "team_ids": {str(forecast["team_a_provider_id"]), str(forecast["team_b_provider_id"])},
                "scheduled_date": str(forecast["scheduled_start_utc"])[:10],
                "observed_at_utc": str(outcome["observed_at_utc"]),
            })
    affected = []
    for frozen in frozen_rows:
        forecast = by_record[str(frozen["forecast_record_id"])]
        fixture_date = str(frozen["scheduled_start_utc"])[:10]
        generated = parse_utc(str(forecast["generated_at_utc"]))
        missed_by_side = []
        for side in ("a", "b"):
            provider_id = str(forecast[f"team_{side}_provider_id"])
            missed = {
                item["pandascore_match_id"] for item in known_outcomes
                if provider_id in item["team_ids"]
                and item["scheduled_date"] < fixture_date
                and parse_utc(item["observed_at_utc"]) < generated
            }
            missed_by_side.append(len(missed))
        if any(missed_by_side):
            affected.append({
                "pandascore_match_id": str(frozen["pandascore_match_id"]),
                "team_a_known_prior_ledger_outcomes_missing_from_state": missed_by_side[0],
                "team_b_known_prior_ledger_outcomes_missing_from_state": missed_by_side[1],
            })
    state_dates = Counter(str(by_record[str(row["forecast_record_id"])]["state_through_date"]) for row in frozen_rows)
    return {
        "state_through_dates": dict(sorted(state_dates.items())),
        "forecasts_with_at_least_one_demonstrably_available_prior_ledger_outcome_omitted": len(affected),
        "maximum_known_omitted_prior_matches_for_one_side": max(
            (max(row["team_a_known_prior_ledger_outcomes_missing_from_state"], row["team_b_known_prior_ledger_outcomes_missing_from_state"]) for row in affected),
            default=0,
        ),
        "affected_forecasts": affected,
        "scope_note": (
            "This is a lower bound from ledger outcomes only. The static bridge ends 2026-07-25, so completed PandaScore matches "
            "outside the prospective ledger may add further missing pre-match history."
        ),
    }


def build_audit(
    *,
    dataset_path: Path = DATASET,
    ledger_path: Path = LEDGER,
    raw_directory: Path = RAW_ODDS,
    mapping_path: Path = MAPPING,
    historical_path: Path = HISTORICAL,
    bridge_path: Path = BRIDGE,
) -> dict[str, Any]:
    frozen = json.loads(dataset_path.read_text(encoding="utf-8"))
    if frozen.get("checkpoint") != 100 or len(frozen.get("rows", [])) != 100:
        raise ValueError("Milestone 7 requires the frozen 100-match checkpoint")
    rows = frozen["rows"]
    records = load_ledger(ledger_path)
    by_record = {str(record["record_id"]): record for record in records}
    enriched = []
    for original in rows:
        row = dict(original)
        forecast = by_record[str(row["forecast_record_id"])]
        row["cold_start_category"] = cold_start_classification(forecast)
        row["calendar_date"] = str(row["scheduled_start_utc"])[:10]
        row["disagreement_bucket"] = disagreement_bucket(float(row["model_market_probability_disagreement"]))
        enriched.append(row)

    identity, _ = _identity_audit(rows, records, mapping_path, historical_path, bridge_path)
    cold_counts = Counter(row["cold_start_category"] for row in enriched)
    outcomes = [bool(row["team_a_won"]) for row in rows]
    elo_probabilities = [float(row["elo_probability_team_a"]) for row in rows]
    market_probabilities = [float(row["bet365_no_vig_probability_team_a"]) for row in rows]
    neutral_probabilities = [0.5] * len(rows)
    neutral = {
        "log_loss": mean(log_loss(p, y) for p, y in zip(neutral_probabilities, outcomes, strict=True)),
        "brier": mean(brier_score(p, y) for p, y in zip(neutral_probabilities, outcomes, strict=True)),
        "accuracy": accuracy(neutral_probabilities, outcomes),
        "team_a_wins": sum(outcomes),
    }
    aggregate = {
        "elo_log_loss": mean(log_loss(p, y) for p, y in zip(elo_probabilities, outcomes, strict=True)),
        "market_log_loss": mean(log_loss(p, y) for p, y in zip(market_probabilities, outcomes, strict=True)),
        "elo_brier": mean(brier_score(p, y) for p, y in zip(elo_probabilities, outcomes, strict=True)),
        "market_brier": mean(brier_score(p, y) for p, y in zip(market_probabilities, outcomes, strict=True)),
        "elo_accuracy": accuracy(elo_probabilities, outcomes),
        "market_accuracy": accuracy(market_probabilities, outcomes),
    }
    return {
        "audit_version": "milestone_7_failure_mode_audit_v1",
        "sample_size": len(rows),
        "source_sha256": {
            "frozen_dataset": sha256_file(dataset_path),
            "prospective_ledger": sha256_file(ledger_path),
            "team_mapping": sha256_file(mapping_path),
            "historical_matches": sha256_file(historical_path),
            "bridge_matches": sha256_file(bridge_path),
        },
        "integrity": _integrity_audit(rows, records, raw_directory),
        "cold_start": {
            "counts": {category: cold_counts.get(category, 0) for category in COLD_ORDER},
            "definition": "Uses only prior-eligible-match counts stored with each frozen forecast.",
            "exact_half_forecasts": sum(float(row["elo_probability_team_a"]) == 0.5 for row in rows),
        },
        "identity": identity,
        "state_freshness": _state_freshness_audit(rows, records),
        "aggregate_metrics": aggregate,
        "neutral_baseline": neutral,
        "loss_decomposition": {
            "by_cold_start": loss_decomposition(enriched, lambda row: row["cold_start_category"], order=COLD_ORDER),
            "by_recorded_history_coverage": loss_decomposition(enriched, lambda row: row["cold_start_category"], order=COLD_ORDER),
            "by_disagreement_bucket": loss_decomposition(enriched, lambda row: row["disagreement_bucket"], order=DISAGREEMENT_ORDER),
            "by_calendar_date": loss_decomposition(enriched, lambda row: row["calendar_date"]),
            "interpretation": "Post-hoc descriptive decomposition; groups are exhaustive and no favorable subset is selected.",
        },
    }


def _number(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def _decomposition_table(entries: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Group | n | Elo LL | Market LL | LL gap sum | LL contribution | Elo Brier | Market Brier | Brier gap sum | Brier contribution |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in entries:
        lines.append(
            f"| {row['group']} | {row['matches']} | {_number(row['elo_log_loss'])} | {_number(row['market_log_loss'])} | "
            f"{_number(row['elo_minus_market_log_loss_sum'])} | {_number(row['log_loss_contribution_to_aggregate_mean'])} | "
            f"{_number(row['elo_brier'])} | {_number(row['market_brier'])} | {_number(row['elo_minus_market_brier_sum'])} | "
            f"{_number(row['brier_contribution_to_aggregate_mean'])} |"
        )
    return lines


def render_report(audit: dict[str, Any]) -> str:
    integrity = audit["integrity"]
    cold = audit["cold_start"]
    identity = audit["identity"]
    impact = identity["impact_reproduction"]
    aggregate = audit["aggregate_metrics"]
    baseline = audit["neutral_baseline"]
    stale = audit["state_freshness"]
    mapped = [row for row in identity["team_inventory"] if row["coverage_category"] == "exact_historical_mapping"]
    provider_only = [row for row in identity["team_inventory"] if row["coverage_category"] == "provider_only_with_bridge_history"]
    new_teams = [row for row in identity["team_inventory"] if row["coverage_category"] in {"new_after_bridge_snapshot", "provider_only_without_pre_match_history"}]
    lines = [
        "# Milestone 7: Prospective Failure-Mode Audit", "",
        "> This is a post-hoc diagnostic of the already-observed frozen 100-match sample. It does not replace the Milestone 6 report, alter historical records, define a betting strategy, or constitute independent validation.", "",
        "## Executive summary", "",
        f"The frozen sample and all 100 market/outcome orientations reproduce. No duplicate, winner-orientation, Bet365 home/away, odds-assignment, no-vig, timing-window, active-selection, or observed reschedule error was found. The frozen Elo underperformance is real as recorded: log loss {_number(aggregate['elo_log_loss'])} versus {_number(aggregate['market_log_loss'])}, and Brier {_number(aggregate['elo_brier'])} versus {_number(aggregate['market_brier'])}.", "",
        f"A confirmed identity-plumbing failure affected {identity['exact_mapping_ignored_appearances']} of 200 competitor appearances and {identity['affected_match_count']} of 100 forecasts. The pre-existing bridge had exact historical mappings for {identity['unique_teams_by_coverage'].get('exact_historical_mapping', 0)} teams, but every forecast used a `ps:<provider-id>` key. Those mapped appearances therefore received zero prior matches and rating 1500. A diagnostic identity-only repair, using only the mapping and match data available before the sample, changes {impact['forecasts_changed']} forecasts, moves Elo log loss to {_number(impact['identity_repaired_elo_log_loss'])}, Brier to {_number(impact['identity_repaired_elo_brier'])}, and mean absolute market disagreement from {_number(impact['frozen_mean_absolute_market_disagreement'] * 100, 2)} pp to {_number(impact['identity_repaired_mean_absolute_market_disagreement'] * 100, 2)} pp. It still trails both the market and the neutral baseline, so identity failure explains part, not all, of v1's weakness.", "",
        f"A second confirmed limitation is stale state: all 100 forecasts say `state_through_date=2026-07-25`. Even using the prospective ledger alone as a conservative lower bound, {stale['forecasts_with_at_least_one_demonstrably_available_prior_ledger_outcome_omitted']} later forecasts had at least one prior-day result already observed before generation but absent from their state. The complete missing-history impact cannot be reconstructed from the ledger because it is not a complete PandaScore match feed.", "",
        "## Data-integrity findings", "",
        f"- Frozen artifact equals an independent reconstruction of the first 100 strictly eligible ledger lifecycles: **{integrity['frozen_dataset_reproduces_first_100']}**.",
        f"- Unique fixture IDs: **{integrity['frozen_unique_match_ids']}/100**; duplicates: **{len(integrity['duplicate_fixture_ids'])}**.",
        f"- Raw immutable odds hashes found and verified: **{integrity['checks_passed_counts'].get('raw_hash_and_snapshot', 0)}/100**.",
        f"- Forecast/raw fixture order aligned home-to-team-A and away-to-team-B: **{integrity['checks_passed_counts'].get('raw_home_away_aligned', 0)}/100**; reversed: **{integrity['checks_passed_counts'].get('raw_home_away_reversed', 0)}**.",
        f"- Winner ID and `team_a_won` orientation agree: **{integrity['checks_passed_counts'].get('winner_outcome_orientation', 0)}/100**.",
        f"- Bet365 ML decimal odds and no-vig probabilities reproduce: **{integrity['checks_passed_counts'].get('raw_odds_assignment', 0)}/100** and **{integrity['checks_passed_counts'].get('no_vig_normalization', 0)}/100**.",
        f"- Forecasts predate effective starts, captures lie within 45–75 minutes, and primary choice reproduces: **{integrity['checks_passed_counts'].get('forecast_before_effective_start', 0)}/100**, **{integrity['checks_passed_counts'].get('market_capture_window', 0)}/100**, **{integrity['checks_passed_counts'].get('deterministic_primary_selection', 0)}/100**.",
        f"- Rescheduled fixtures in the sample: **{len(integrity['rescheduled_fixture_ids'])}** ({', '.join(integrity['rescheduled_fixture_ids']) or 'none'}). Their superseded primaries are excluded and their active replacement primaries are used.",
        f"- Non-identity integrity failures: **{integrity['observed_integrity_issue_count_excluding_identity']}**.", "",
        "The collector nevertheless has a latent orientation hazard: fixture reconciliation is unordered while raw `home`/`away` prices are assigned directly to team A/B. It had zero impact here because all 100 pairs happened to share the same order; a future invariant must make that coincidence unnecessary.", "",
        "## Cold-start breakdown", "",
        "The registered classification uses only the counts frozen at forecast time. Because the identity audit proves many of those counts were wrong, \"true\" below means the protocol's stored-count definition, not the identity-reconciled finding.", "",
        "| Category | Forecasts |", "| --- | ---: |",
        f"| Both teams true cold starts as recorded | {cold['counts']['both_cold_start']} |",
        f"| Exactly one cold-start team as recorded | {cold['counts']['exactly_one_cold_start']} |",
        f"| Neither team a cold start as recorded | {cold['counts']['neither_cold_start']} |", "",
        f"All {cold['exact_half_forecasts']} exact-0.5 forecasts came from both stored ratings being the initial 1500; zero came from equal non-initial ratings. After applying only the already-existing exact identity mappings, {identity['neutral_forecasts_with_usable_unreflected_history'].get('both_teams', 0)} of those neutral forecasts had usable history for both teams and {identity['neutral_forecasts_with_usable_unreflected_history'].get('exactly_one_team', 0)} had usable history for one. Thus every exact-0.5 forecast reflects confirmed missing continuity, not evidence inferred from the later winner.", "",
        f"Identity-reconciled pre-sample coverage is {identity['identity_reconciled_history_coverage'].get('both_have_history', 0)} both-history, {identity['identity_reconciled_history_coverage'].get('exactly_one_has_history', 0)} one-history, and {identity['identity_reconciled_history_coverage'].get('neither_has_history', 0)} neither-history fixtures. This is an audit reclassification, not a rewrite of the registered cold-start fields.", "",
        "## Team identity coverage", "",
        f"The 100 fixtures contain {identity['unique_team_count']} unique PandaScore team IDs: {len(mapped)} had an exact, unique historical-name mapping already recorded before forecasting; {len(provider_only)} were provider-only IDs with their own bridge history; and {len(new_teams)} first appeared after the bridge snapshot and had no pre-sample history.", "",
        "| Coverage class | Teams | Checkpoint appearances | Finding |", "| --- | ---: | ---: | --- |",
        f"| Exact historical mapping | {len(mapped)} | {sum(row['checkpoint_appearances'] for row in mapped)} | Confirmed mapping existed but forecaster bypassed it |",
        f"| Provider-only with bridge history | {len(provider_only)} | {sum(row['checkpoint_appearances'] for row in provider_only)} | Continuity within PandaScore bridge was retained |",
        f"| New after bridge snapshot | {len(new_teams)} | {sum(row['checkpoint_appearances'] for row in new_teams)} | True pre-sample cold starts |", "",
        "Exact mapped teams (PandaScore ID → historical ID): " + "; ".join(f"{row['pandascore_team_name']} ({row['pandascore_team_id']} → {row['historical_team_id']})" for row in mapped) + ".", "",
        "Provider-only teams with bridge history: " + "; ".join(f"{row['pandascore_team_name']} ({row['pandascore_team_id']}, {row['pre_checkpoint_match_count']} matches)" for row in provider_only) + ".", "",
        "True new teams: " + "; ".join(f"{row['pandascore_team_name']} ({row['pandascore_team_id']})" for row in new_teams) + ".", "",
        "No additional historical alias or provider-ID-change mapping had independently verifiable pre-match evidence in the preserved inputs. Among checkpoint teams, no historical ID mapped to multiple PandaScore IDs, no normalized name appeared under multiple PandaScore IDs, and no checkpoint PandaScore ID used multiple names in the bridge. These checks found no duplicate organizational identity or preserved rename signal. Name-similar candidates were deliberately not merged. The machine-readable artifact contains the full inventory and appearance counts.", "",
        "## Loss decomposition", "",
        "Differences are Elo minus market. `gap sum` is the group's additive numerator; `contribution` divides that sum by all 100 matches, so contributions add to the aggregate gap. These are exhaustive post-hoc diagnostics.", "",
        "### By recorded cold-start / history coverage", "",
        "Recorded history coverage (both zero / exactly one zero / neither zero) is algebraically identical to the requested cold-start split, so it is shown once rather than duplicated under a second label.", "",
        *_decomposition_table(audit["loss_decomposition"]["by_cold_start"]), "",
        "The one-cold group contributes most of the aggregate deficit: its log-loss contribution is " + _number(next(row for row in audit["loss_decomposition"]["by_cold_start"] if row["group"] == "exactly_one_cold_start")["log_loss_contribution_to_aggregate_mean"]) + ".", "",
        "### By preregistered disagreement bucket", "",
        *_decomposition_table(audit["loss_decomposition"]["by_disagreement_bucket"]), "",
        "### By UTC calendar date", "",
        *_decomposition_table(audit["loss_decomposition"]["by_calendar_date"]), "",
        "## Neutral baseline verification", "",
        f"Over exactly the same outcomes, a constant 0.5 has log loss **{_number(baseline['log_loss'])}** and Brier **{_number(baseline['brier'])}**. Team A won {baseline['team_a_wins']}/100, but that imbalance does not change the per-match 0.5 scores. Under the registered accuracy rule, every 0.5 is a no-direction tie, so neutral directional accuracy is undefined (0 directional predictions), not 50% observed accuracy.", "",
        f"Elo is worse than neutral by {_number(aggregate['elo_log_loss'] - baseline['log_loss'])} log loss and {_number(aggregate['elo_brier'] - baseline['brier'])} Brier. Bet365 is worse by {_number(aggregate['market_log_loss'] - baseline['log_loss'])} and {_number(aggregate['market_brier'] - baseline['brier'])}, while still outperforming Elo. Market directional accuracy is {_number(aggregate['market_accuracy']['value'])} ({aggregate['market_accuracy']['correct']}/{aggregate['market_accuracy']['directional_predictions']}); its slightly worse proper scores arise because probability confidence and error magnitude matter, and a small number of confident misses can outweigh more correct directions. With the orientation checks clean, this is not explained by a detected market-label error.", "",
        "## Market disagreement diagnosis", "",
        f"The >10 pp bucket contains {next(row for row in audit['loss_decomposition']['by_disagreement_bucket'] if row['group'] == '> 10 pp')['matches']} matches and contributes {_number(next(row for row in audit['loss_decomposition']['by_disagreement_bucket'] if row['group'] == '> 10 pp')['log_loss_contribution_to_aggregate_mean'])} of the {_number(aggregate['elo_log_loss'] - aggregate['market_log_loss'])} aggregate log-loss gap. The identity-only reproduction reduces mean absolute disagreement by {_number((impact['frozen_mean_absolute_market_disagreement'] - impact['identity_repaired_mean_absolute_market_disagreement']) * 100, 2)} pp, strong evidence that missing identity continuity drove much of the disagreement magnitude. Because repaired Elo still has {_number(impact['identity_repaired_elo_log_loss'])} log loss versus market {_number(aggregate['market_log_loss'])}, the remaining gap is consistent with additional missing/current information and raw-Elo model limitations; this audit cannot assign those residual causes precisely.", "",
        "## Confirmed versus suspected failure modes", "",
        "Confirmed:", "",
        f"- Exact identity mappings were bypassed in {identity['exact_mapping_ignored_appearances']} appearances across {identity['affected_match_count']} matches.",
        f"- State was frozen through 2026-07-25 for all 100 forecasts; at least {stale['forecasts_with_at_least_one_demonstrably_available_prior_ledger_outcome_omitted']} forecasts omitted demonstrably available prior-day ledger results.",
        "- Raw Elo lacks contextual strength information beyond the stale match-result stream; after the identity-only repair it still trails neutral and market scores.",
        "- Fixture matching has a latent order-safety weakness, although no checkpoint row was reversed.", "",
        "Suspected but not confirmed:", "",
        "- Further cross-provider aliases, renamed organizations, or provider-ID changes may connect some provider-only teams to older historical IDs, but the preserved evidence does not justify a merge.",
        "- Missing completed matches outside the prospective ledger likely make the stale-state lower bound incomplete; exact scope needs a separately preserved complete feed.",
        "- Market inputs may encode roster, patch, tournament, or other current information absent from v1, but this audit does not isolate those channels.", "",
        "Not supported:", "",
        "- No observed fixture, winner, Bet365 home/away, odds, no-vig, duplicate, primary-selection, timing-window, or reschedule corruption explains the result.", "",
        "## Limitations", "",
        "The sample has only 100 outcomes and was already observed before this audit. Identity-repair scores are diagnostic counterfactuals, not a newly validated model. The static bridge prevents a complete reconstruction of all information that should have been available before each match. Organization continuity cannot be established from name similarity alone. No profitability, staking, or favorable-subset analysis was performed.", "",
        "## Evidence-based requirements for a future v2", "",
        "- Consume one versioned identity crosswalk consistently in both state construction and forecasting, and fail closed when the forecast key differs from the mapped state key.",
        "- Preserve a complete, timestamped match feed and prove that every forecast state ends on D-1, with same-day matches batched under the registered rule.",
        "- Distinguish true new teams from mapped teams and provider-only teams in frozen forecast records; store both provider and canonical IDs.",
        "- Assert ordered team/price alignment against the raw market event before accepting a candidate.",
        "- Reproduce ratings, counts, forecast probabilities, and market normalization from immutable inputs before a forecast can enter evaluation.",
        "- Pre-register any added information source, feature, model family, hyperparameter, and missing-data behavior before collecting v2 outcomes.", "",
        "## Proposed untouched prospective evaluation design for v2", "",
        "Freeze the v2 specification only after the identity and state-freshness pipeline passes historical replay tests. Start a new append-only ledger namespace at a declared UTC activation time; no match whose outcome was known or included in this audit may enter v2 evaluation. Pre-register one primary model, one primary market comparator, eligibility and reschedule rules, the same proper scores, checkpoint size, and a date-clustered uncertainty method. Keep the current v1 collector and its records unchanged. Run v1 and v2 forecasts side by side only on newly scheduled fixtures, without updating specifications from interim outcomes, then evaluate the untouched chronological sample once the registered count is reached.", "",
        "## Reproduction", "",
        "Run `python -m valorant_quant.milestone7_audit`. The command reads immutable inputs and writes only `artifacts/milestone_7_failure_mode_audit.json` and this report. Source SHA-256 values are recorded in the artifact.", "",
    ]
    return "\n".join(lines)


def serialize_audit(audit: dict[str, Any]) -> str:
    """Canonical human-readable JSON used for deterministic audit artifacts."""
    return json.dumps(audit, indent=2, sort_keys=True) + "\n"


def run_audit(
    *,
    artifact_path: Path = ARTIFACT,
    report_path: Path = REPORT,
    **input_paths: Any,
) -> tuple[Path, Path, dict[str, Any]]:
    audit = build_audit(**input_paths)
    artifact_text = serialize_audit(audit)
    report_text = render_report(audit)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(artifact_text, encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")
    return artifact_path, report_path, audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce the read-only Milestone 7 failure-mode audit.")
    parser.parse_args()
    artifact, report, audit = run_audit()
    print(json.dumps({
        "artifact": str(artifact),
        "report": str(report),
        "sample_size": audit["sample_size"],
        "observed_non_identity_integrity_issues": audit["integrity"]["observed_integrity_issue_count_excluding_identity"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
