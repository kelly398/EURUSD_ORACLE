
"""F3.3.1: ALFRED date-granular policy-rate vintage corroboration.

Offline contract only. Does NOT fetch ALFRED responses, authenticate an
external archive, certify original Fed HTML, or authorize database writes.

FRED DFEDTARL/DFEDTARU report daily effective targets; ALFRED can expose
historical versions of those daily observations. A date-level vintage is
NOT proof that an FOMC statement's bytes existed at its intraday release.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import re

from backend.macro.fed_rate_backfill import FedRateBackfillPlan
from backend.macro.fed_rate_protocol import (
    FED_RATE_NAMES,
    FED_CENTRAL_BANK,
    FED_RATE_SOURCE,
    parse_fed_rate_percent,
    conservative_fed_date_only_available_at,
    build_fed_record_id,
    build_fed_content_hash,
)


ALFRED_RATE_SERIES = (
    "DFEDTARL",
    "DFEDTARU",
)

ALFRED_VINTAGE_STATUS = "CANDIDATE_VINTAGE_MATCH_UNAUTHENTICATED"

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class FedVintageProtocolError(ValueError):
    """A vintage observation is invalid or disagrees with Fed evidence."""


def _date(value, label: str) -> date:
    if type(value) is not date:
        raise FedVintageProtocolError(
            f"{label} must be date, not datetime/text."
        )
    return value


def _utc(value, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise FedVintageProtocolError(
            f"{label} must be timezone-aware."
        )

    try:
        if value.utcoffset() is None:
            raise ValueError("No UTC offset")

        return value.astimezone(timezone.utc)

    except (ValueError, OverflowError) as exc:
        raise FedVintageProtocolError(
            f"Invalid {label} timezone."
        ) from exc


@dataclass(frozen=True)
class FedAlfredVintageObservation:
    """An ALFRED observation awaiting source-response verification.

    vintage_date is the requested historical real-time date.
    realtime_start/end describe the returned observation's validity
    across vintage dates.

    The later acquisition client must verify these fields against
    actual official response bytes and exact request parameters.
    """

    series_id: str
    observation_date: date
    vintage_date: date
    realtime_start: date
    realtime_end: date
    value_percent: str
    response_sha256: str
    fetched_at: datetime


@dataclass(frozen=True)
class FedVintageCorroboration:
    """Value comparison only, not original-release certification."""

    statement_id: str
    effective_date: date
    vintage_date: date
    conservative_vintage_boundary: datetime
    evidence_observed_at: datetime
    observation_hashes: tuple[str, str]
    status: str = ALFRED_VINTAGE_STATUS

    @property
    def historical_html_certified(self) -> bool:
        return False

    @property
    def source_response_authenticated(self) -> bool:
        return False

    @property
    def persistence_permitted(self) -> bool:
        return False


def _check_observation(
    value: FedAlfredVintageObservation,
    series_id: str,
    expected_effective_date: date,
    announced_at: datetime,
) -> tuple[Decimal, datetime]:

    if not isinstance(value, FedAlfredVintageObservation):
        raise FedVintageProtocolError(
            "Expected an ALFRED vintage observation."
        )

    if value.series_id != series_id:
        raise FedVintageProtocolError(
            "ALFRED series identity mismatch."
        )

    obs = _date(value.observation_date, "observation_date")
    vintage = _date(value.vintage_date, "vintage_date")
    start = _date(value.realtime_start, "realtime_start")
    end = _date(value.realtime_end, "realtime_end")

    if obs != expected_effective_date:
        raise FedVintageProtocolError(
            "ALFRED observation date is not the effective date."
        )

    if not start <= vintage <= end:
        raise FedVintageProtocolError(
            "ALFRED value was not valid in requested vintage."
        )

    # V1 corroborates the daily EFFECTIVE rate state.
    # It does not infer future-effective values from announcement time.
    if vintage < obs or vintage < announced_at.date():
        raise FedVintageProtocolError(
            "Vintage predates the effective policy observation."
        )

    if (
        not isinstance(value.response_sha256, str)
        or not _SHA256.fullmatch(value.response_sha256)
    ):
        raise FedVintageProtocolError(
            "ALFRED response requires a SHA-256 fingerprint."
        )

    fetched = _utc(value.fetched_at, "fetched_at")

    if fetched.date() < vintage:
        raise FedVintageProtocolError(
            "Cannot retrieve a future vintage."
        )

    try:
        rate = parse_fed_rate_percent(value.value_percent)
    except (ValueError, TypeError) as exc:
        raise FedVintageProtocolError(
            "Invalid ALFRED numeric rate token."
        ) from exc

    return rate, fetched


def corroborate_fed_effective_rate_vintage(
    plan: FedRateBackfillPlan,
    lower: FedAlfredVintageObservation,
    upper: FedAlfredVintageObservation,
) -> FedVintageCorroboration:
    """Compare a historical daily rate pair with a candidate Fed decision.

    This function has no persistence authority.

    Its caller-provided ALFRED observations are NOT authenticated merely
    because they have the correct structure. Source acquisition and
    provenance attestation must be implemented separately.
    """

    if not isinstance(plan, FedRateBackfillPlan):
        raise FedVintageProtocolError(
            "Expected a FedRateBackfillPlan."
        )

    if plan.persistence_permitted:
        raise FedVintageProtocolError(
            "Unexpected mutable historical persistence state."
        )

    if len(plan.candidate_snapshots) != 2:
        raise FedVintageProtocolError(
            "Expected exactly two Fed decision bounds."
        )

    snapshots = plan.candidate_snapshots

    announced_at = _utc(plan.announced_at, "announced_at")
    ingested_at = _utc(plan.ingested_at, "ingested_at")

    if ingested_at < announced_at:
        raise FedVintageProtocolError(
            "Candidate ingestion predates the release."
        )

    if tuple(s.rate_name for s in snapshots) != FED_RATE_NAMES:
        raise FedVintageProtocolError(
            "Candidate target bound names mismatch."
        )

    if any(
        s.central_bank != FED_CENTRAL_BANK
        or s.source != FED_RATE_SOURCE
        for s in snapshots
    ):
        raise FedVintageProtocolError(
            "Candidate sources are not official Fed decisions."
        )

    if any(
        s.reference_date != plan.effective_date
        or _utc(s.available_at, "candidate.available_at") != announced_at
        or _utc(s.ingested_at, "candidate.ingested_at") != ingested_at
        for s in snapshots
    ):
        raise FedVintageProtocolError(
            "Candidate pair chronology is inconsistent."
        )

    candidate_low = parse_fed_rate_percent(
        str(snapshots[0].value_percent)
    )
    candidate_high = parse_fed_rate_percent(
        str(snapshots[1].value_percent)
    )

    if candidate_low > candidate_high:
        raise FedVintageProtocolError(
            "Candidate Fed target bounds are reversed."
        )

    for bound, snapshot in zip(
        ("lower", "upper"), snapshots, strict=True
    ):
        expected_id = build_fed_record_id(
            plan.statement_id, bound
        )

        expected_hash = build_fed_content_hash(
            plan.statement_id,
            snapshot.rate_name,
            plan.effective_date,
            str(candidate_low),
            str(candidate_high),
        )

        if (
            snapshot.source_record_id != expected_id
            or snapshot.content_hash != expected_hash
        ):
            raise FedVintageProtocolError(
                "Candidate Fed identity or economic hash mismatch."
            )
    if not isinstance(lower, FedAlfredVintageObservation) or not isinstance(
        upper, FedAlfredVintageObservation
    ):
        raise FedVintageProtocolError(
            "Expected two ALFRED vintage observations."
        )
    if lower.vintage_date != upper.vintage_date:
        raise FedVintageProtocolError(
            "The two ALFRED bounds need the same vintage date."
        )

    low, first_fetch = _check_observation(
        lower,
        ALFRED_RATE_SERIES[0],
        plan.effective_date,
        announced_at,
    )

    high, second_fetch = _check_observation(
        upper,
        ALFRED_RATE_SERIES[1],
        plan.effective_date,
        announced_at,
    )

    if low > high:
        raise FedVintageProtocolError(
            "ALFRED lower bound exceeds upper bound."
        )

    for observed, candidate_rate in zip(
        (low, high),
        (candidate_low, candidate_high),
        strict=True,
    ):
        if observed != candidate_rate:
            raise FedVintageProtocolError(
                "ALFRED historical value conflicts with Fed decision."
            )

    # A date-level source cannot prove a particular intraday timestamp.
    # The next New York local midnight is a conservative boundary,
    # not a statement-release certification.
    boundary = conservative_fed_date_only_available_at(
        lower.vintage_date
    )

    observed_at = max(
        first_fetch,
        second_fetch,
        ingested_at,
    )

    return FedVintageCorroboration(
        statement_id=plan.statement_id,
        effective_date=plan.effective_date,
        vintage_date=lower.vintage_date,
        conservative_vintage_boundary=boundary,
        evidence_observed_at=observed_at,
        observation_hashes=(
            lower.response_sha256,
            upper.response_sha256,
        ),
    )
