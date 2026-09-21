"""Separately versioned, inactive v2 Elo forecasting engine.

The engine fixes identity lookup and temporal-state construction while keeping
the v1 Elo mathematics unchanged.  It has no network, scheduler, ledger-write,
or live-collection behavior.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from valorant_quant.elo import DEFAULT_SCALE, INITIAL_RATING, EloConfig, run_elo, validate_canonical_matches, win_probability


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_MATCHES = ROOT / "data/processed/milestone_2/canonical_matches.csv"
BRIDGE_MATCHES = ROOT / "data/processed/milestone_5/pandascore_eligible_bridge_matches.csv"
IDENTITY_MAPPING = ROOT / "data/processed/milestone_5/pandascore_team_mapping.csv"

MODEL_VERSION = "raw_elo_daily_batched_k64_identity_temporal_v2"
RECORD_TYPE = "v2_forecast_generated"
K_FACTOR = 64.0

CANONICAL_COLUMNS = [
    "match_id", "match_date", "year", "team_a_id", "team_a_name", "team_b_id", "team_b_name",
    "team_a_won", "tournament_name", "source_snapshot_id",
]
DYNAMIC_REQUIRED_COLUMNS = {
    "pandascore_match_id", "match_date", "team_a_provider_id", "team_a_name",
    "team_b_provider_id", "team_b_name", "team_a_won", "tournament_name",
    "source_snapshot_id", "completion_status", "forfeit", "eligible", "completed_at_utc",
    "result_available_at_utc",
}
V2_FORECAST_REQUIRED_FIELDS = {
    "record_id", "record_type", "model_version", "generated_at_utc", "state_through_date",
    "state_cutoff_date", "state_as_of_utc", "state_input_sha256", "identity_mapping_version",
    "fixture_id", "scheduled_start_utc", "team_a_provider_id", "team_a_name", "team_a_identity",
    "team_b_provider_id", "team_b_name", "team_b_identity", "elo_a", "elo_b",
    "team_a_prior_eligible_matches", "team_b_prior_eligible_matches", "p_team_a_wins",
    "p_team_b_wins", "k", "scale", "initial_rating", "state_eligible_match_count",
}


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _false_like(value: Any) -> bool:
    return str(value).strip().casefold() in {"false", "0", "no"}


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    scheduled_start_utc: str
    team_a_provider_id: str
    team_a_name: str
    team_b_provider_id: str
    team_b_name: str

    def validate(self, generated_at_utc: str) -> None:
        if not self.fixture_id or not self.team_a_name.strip() or not self.team_b_name.strip():
            raise ValueError("fixture ID and both team names are required")
        if str(self.team_a_provider_id) == str(self.team_b_provider_id):
            raise ValueError("fixture teams must have distinct provider IDs")
        if parse_utc(generated_at_utc) >= parse_utc(self.scheduled_start_utc):
            raise ValueError("forecast must be generated before scheduled start")


@dataclass(frozen=True)
class EloState:
    ratings: dict[str, float]
    prior_eligible_matches: dict[str, int]
    state_through_date: str
    state_cutoff_date: str
    state_as_of_utc: str
    state_input_sha256: str
    eligible_match_count: int


class ExactIdentityResolver:
    """Resolve only preserved, unambiguous exact mappings; never fuzzy-match."""

    def __init__(self, mappings: dict[str, str], *, mapping_version: str) -> None:
        self._mappings = dict(sorted((str(key), str(value)) for key, value in mappings.items()))
        self.mapping_version = mapping_version

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, *, mapping_version: str | None = None) -> "ExactIdentityResolver":
        required = {"pandascore_team_id", "historical_team_id", "match_method", "ambiguity_flag"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"identity mapping missing columns: {sorted(missing)}")
        mappings: dict[str, str] = {}
        canonical_rows: list[dict[str, str]] = []
        for row in frame.fillna("").itertuples(index=False):
            provider_id = str(row.pandascore_team_id)
            historical_id = str(row.historical_team_id).strip()
            method = str(row.match_method)
            ambiguous = not _false_like(row.ambiguity_flag)
            if historical_id:
                if method != "exact_normalized_name" or ambiguous:
                    raise ValueError(f"non-exact or ambiguous mapping is forbidden for provider team {provider_id}")
                previous = mappings.get(provider_id)
                if previous is not None and previous != historical_id:
                    raise ValueError(f"conflicting historical identities for provider team {provider_id}")
                mappings[provider_id] = historical_id
            canonical_rows.append({
                "pandascore_team_id": provider_id,
                "historical_team_id": historical_id,
                "match_method": method,
                "ambiguity_flag": str(ambiguous).lower(),
            })
        if mapping_version is None:
            payload = json.dumps(sorted(canonical_rows, key=lambda item: tuple(item.values())), sort_keys=True, separators=(",", ":"))
            mapping_version = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        return cls(mappings, mapping_version=mapping_version)

    @classmethod
    def from_csv(cls, path: Path = IDENTITY_MAPPING) -> "ExactIdentityResolver":
        return cls.from_frame(pd.read_csv(path, dtype=str), mapping_version="sha256:" + _sha256_file(path))

    def resolve(self, provider_id: str | int) -> str:
        key = str(provider_id)
        return self._mappings.get(key, f"ps:{key}")

    def is_historically_mapped(self, provider_id: str | int) -> bool:
        return str(provider_id) in self._mappings


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_frozen_base_matches(
    historical_path: Path = HISTORICAL_MATCHES,
    bridge_path: Path = BRIDGE_MATCHES,
) -> pd.DataFrame:
    """Load the immutable pre-v2 base whose bridge identities are already resolved."""
    historical = pd.read_csv(historical_path, dtype={"team_a_id": "string", "team_b_id": "string"})
    bridge = pd.read_csv(bridge_path, dtype={"team_a_id": "string", "team_b_id": "string"})
    combined = pd.concat([historical[CANONICAL_COLUMNS], bridge[CANONICAL_COLUMNS]], ignore_index=True)
    validate_canonical_matches(combined, allow_post_2024=True)
    return combined


def _canonical_dynamic_matches(
    completed_matches: pd.DataFrame | None,
    *,
    fixture_date: date,
    generated_at: datetime,
    resolver: ExactIdentityResolver,
) -> pd.DataFrame:
    if completed_matches is None or completed_matches.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    missing = DYNAMIC_REQUIRED_COLUMNS - set(completed_matches.columns)
    if missing:
        raise ValueError(f"completed-match feed missing columns: {sorted(missing)}")
    frame = completed_matches.copy()
    if frame["pandascore_match_id"].isna().any() or frame["pandascore_match_id"].astype(str).duplicated().any():
        raise ValueError("completed-match provider IDs must be present and unique")
    if not frame["eligible"].map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("eligible must be boolean")
    selected = frame.loc[frame["eligible"]].copy()
    if selected.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    if not selected["team_a_won"].map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("team_a_won must be boolean for eligible results")
    if not selected["completion_status"].eq("finished").all() or not selected["forfeit"].eq(False).all():
        raise ValueError("eligible results must be finished and non-forfeit")
    match_dates = pd.to_datetime(selected["match_date"], errors="coerce")
    completed = pd.to_datetime(selected["completed_at_utc"], utc=True, errors="coerce")
    available = pd.to_datetime(selected["result_available_at_utc"], utc=True, errors="coerce")
    if match_dates.isna().any() or completed.isna().any() or available.isna().any():
        raise ValueError("eligible results require valid match, completion, and result-availability timestamps")
    if available.lt(completed).any():
        raise ValueError("a result cannot be available before match completion")
    selected = selected.loc[
        match_dates.dt.date.lt(fixture_date)
        & completed.le(pd.Timestamp(generated_at))
        & available.le(pd.Timestamp(generated_at))
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    if selected[["team_a_provider_id", "team_b_provider_id", "team_a_name", "team_b_name"]].isna().any().any():
        raise ValueError("eligible results require both teams")
    if selected["team_a_provider_id"].astype(str).eq(selected["team_b_provider_id"].astype(str)).any():
        raise ValueError("eligible result teams must be distinct")
    output = pd.DataFrame({
        "match_id": "pandascore:" + selected["pandascore_match_id"].astype(str),
        "match_date": selected["match_date"].astype(str),
        "year": pd.to_datetime(selected["match_date"]).dt.year.astype(int),
        "team_a_id": selected["team_a_provider_id"].map(resolver.resolve),
        "team_a_name": selected["team_a_name"].astype(str),
        "team_b_id": selected["team_b_provider_id"].map(resolver.resolve),
        "team_b_name": selected["team_b_name"].astype(str),
        "team_a_won": selected["team_a_won"].astype(bool),
        "tournament_name": selected["tournament_name"].astype(str),
        "source_snapshot_id": selected["source_snapshot_id"].astype(str),
    })
    validate_canonical_matches(output, allow_post_2024=True)
    return output


def _state_digest(matches: pd.DataFrame) -> str:
    serializable = []
    ordered = matches.sort_values(["match_date", "match_id"], kind="stable")
    for row in ordered[CANONICAL_COLUMNS].itertuples(index=False):
        serializable.append({
            "match_id": str(row.match_id),
            "match_date": str(row.match_date),
            "year": int(row.year),
            "team_a_id": str(row.team_a_id),
            "team_a_name": str(row.team_a_name),
            "team_b_id": str(row.team_b_id),
            "team_b_name": str(row.team_b_name),
            "team_a_won": bool(row.team_a_won),
            "tournament_name": str(row.tournament_name),
            "source_snapshot_id": str(row.source_snapshot_id),
        })
    payload = json.dumps(serializable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class CorrectedEloEngine:
    """Reconstruct a leakage-safe D-1 state and emit a v2 forecast record."""

    def __init__(self, base_matches: pd.DataFrame, resolver: ExactIdentityResolver) -> None:
        self._base_matches = base_matches[CANONICAL_COLUMNS].copy(deep=True)
        validate_canonical_matches(self._base_matches, allow_post_2024=True)
        self._resolver = resolver
        self._config = EloConfig(k=K_FACTOR, scale=DEFAULT_SCALE, initial_rating=INITIAL_RATING)

    def reconstruct_state(
        self,
        fixture: Fixture,
        *,
        generated_at_utc: str,
        completed_matches: pd.DataFrame | None = None,
    ) -> EloState:
        fixture.validate(generated_at_utc)
        generated_at = parse_utc(generated_at_utc)
        fixture_date = parse_utc(fixture.scheduled_start_utc).date()
        cutoff = fixture_date - timedelta(days=1)
        base_dates = pd.to_datetime(self._base_matches["match_date"], errors="coerce")
        if base_dates.isna().any():
            raise ValueError("base state contains invalid match dates")
        base = self._base_matches.loc[base_dates.dt.date.le(cutoff)].copy()
        dynamic = _canonical_dynamic_matches(
            completed_matches, fixture_date=fixture_date, generated_at=generated_at, resolver=self._resolver
        )
        combined = base.reset_index(drop=True) if dynamic.empty else pd.concat([base, dynamic], ignore_index=True)
        if combined["match_id"].astype(str).duplicated().any():
            duplicates = sorted(combined.loc[combined["match_id"].astype(str).duplicated(False), "match_id"].astype(str).unique())
            raise ValueError(f"state contains duplicate match IDs: {duplicates}")
        if combined.empty:
            ratings: dict[str, float] = {}
            counts: dict[str, int] = {}
            state_through = "none"
        else:
            validate_canonical_matches(combined, allow_post_2024=True)
            _, ratings = run_elo(combined, self._config, allow_post_2024=True)
            counts = pd.concat([combined["team_a_id"].astype(str), combined["team_b_id"].astype(str)]).value_counts().astype(int).to_dict()
            state_through = str(combined["match_date"].astype(str).max())
        return EloState(
            ratings=ratings,
            prior_eligible_matches=dict(sorted(counts.items())),
            state_through_date=state_through,
            state_cutoff_date=cutoff.isoformat(),
            state_as_of_utc=utc_text(generated_at),
            state_input_sha256=_state_digest(combined),
            eligible_match_count=len(combined),
        )

    def forecast(
        self,
        fixture: Fixture,
        *,
        generated_at_utc: str,
        completed_matches: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        state = self.reconstruct_state(
            fixture, generated_at_utc=generated_at_utc, completed_matches=completed_matches
        )
        identity_a = self._resolver.resolve(fixture.team_a_provider_id)
        identity_b = self._resolver.resolve(fixture.team_b_provider_id)
        if identity_a == identity_b:
            raise ValueError("fixture teams resolve to the same canonical identity")
        rating_a = float(state.ratings.get(identity_a, INITIAL_RATING))
        rating_b = float(state.ratings.get(identity_b, INITIAL_RATING))
        probability_a = win_probability(rating_a, rating_b, DEFAULT_SCALE)
        record = {
            "record_id": f"v2_forecast:{fixture.fixture_id}:{fixture.scheduled_start_utc}",
            "record_type": RECORD_TYPE,
            "model_version": MODEL_VERSION,
            "generated_at_utc": utc_text(parse_utc(generated_at_utc)),
            "state_through_date": state.state_through_date,
            "state_cutoff_date": state.state_cutoff_date,
            "state_as_of_utc": state.state_as_of_utc,
            "state_input_sha256": state.state_input_sha256,
            "identity_mapping_version": self._resolver.mapping_version,
            "fixture_id": str(fixture.fixture_id),
            "scheduled_start_utc": fixture.scheduled_start_utc,
            "team_a_provider_id": str(fixture.team_a_provider_id),
            "team_a_name": fixture.team_a_name,
            "team_a_identity": identity_a,
            "team_b_provider_id": str(fixture.team_b_provider_id),
            "team_b_name": fixture.team_b_name,
            "team_b_identity": identity_b,
            "elo_a": rating_a,
            "elo_b": rating_b,
            "team_a_prior_eligible_matches": int(state.prior_eligible_matches.get(identity_a, 0)),
            "team_b_prior_eligible_matches": int(state.prior_eligible_matches.get(identity_b, 0)),
            "p_team_a_wins": probability_a,
            "p_team_b_wins": 1.0 - probability_a,
            "k": K_FACTOR,
            "scale": DEFAULT_SCALE,
            "initial_rating": INITIAL_RATING,
            "state_eligible_match_count": state.eligible_match_count,
        }
        validate_v2_forecast_record(record)
        return record


def validate_v2_forecast_record(record: dict[str, Any]) -> None:
    missing = V2_FORECAST_REQUIRED_FIELDS - set(record)
    if missing:
        raise ValueError(f"v2 forecast missing fields: {sorted(missing)}")
    if record["record_type"] != RECORD_TYPE or record["model_version"] != MODEL_VERSION:
        raise ValueError("incorrect v2 record or model version")
    if parse_utc(str(record["generated_at_utc"])) >= parse_utc(str(record["scheduled_start_utc"])):
        raise ValueError("v2 forecast was not generated before start")
    cutoff = date.fromisoformat(str(record["state_cutoff_date"]))
    fixture_date = parse_utc(str(record["scheduled_start_utc"])).date()
    if cutoff != fixture_date - timedelta(days=1):
        raise ValueError("state cutoff must be D-1")
    if record["state_through_date"] != "none" and date.fromisoformat(str(record["state_through_date"])) > cutoff:
        raise ValueError("state contains a result after D-1")
    if not str(record["team_a_identity"]) or not str(record["team_b_identity"]):
        raise ValueError("both canonical team identities are required")
    if str(record["team_a_identity"]) == str(record["team_b_identity"]):
        raise ValueError("forecast identities must be distinct")
    if int(record["team_a_prior_eligible_matches"]) < 0 or int(record["team_b_prior_eligible_matches"]) < 0:
        raise ValueError("prior eligible match counts cannot be negative")
    probability_a, probability_b = float(record["p_team_a_wins"]), float(record["p_team_b_wins"])
    if not 0 <= probability_a <= 1 or not 0 <= probability_b <= 1 or abs(probability_a + probability_b - 1) > 1e-12:
        raise ValueError("v2 probabilities must be complementary values in [0, 1]")
    if float(record["k"]) != K_FACTOR or float(record["scale"]) != DEFAULT_SCALE or float(record["initial_rating"]) != INITIAL_RATING:
        raise ValueError("v2 Elo parameters are not the frozen K=64 baseline")


def build_default_engine(
    *,
    historical_path: Path = HISTORICAL_MATCHES,
    bridge_path: Path = BRIDGE_MATCHES,
    mapping_path: Path = IDENTITY_MAPPING,
) -> CorrectedEloEngine:
    """Build, but never activate, the v2 engine from preserved local inputs."""
    resolver = ExactIdentityResolver.from_csv(mapping_path)
    base = load_frozen_base_matches(historical_path, bridge_path)
    return CorrectedEloEngine(base, resolver)
