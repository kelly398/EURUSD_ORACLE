"""F4.5.2a: Read-only Fed Council input readiness diagnostics.

This report is NOT an evidence certificate or authority to register an agent.
Exact historical source-version attestation and an independently validated
FOMC calendar acquisition path have not been integrated. Therefore approval
is intentionally impossible in this version, regardless of candidate data.

No HTTP, database access, writes, broker execution, or registry mutation.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from backend.agents.contracts import AgentContext
from backend.macro.fed_replay_lock import (
    FedReplayLockError,
    lock_fed_data_as_of,
)
from backend.macro.macro_schemas import EconomicCalendarEventSnapshot


class FedRateInputStatus(str, Enum):
    ABSENT = "absent"
    INVALID = "invalid"
    INTERNALLY_CONSISTENT = "internally_consistent"


class FedInputNotReady(RuntimeError):
    """Fed operational admission is disabled; catalogue registration is separate."""


@dataclass(frozen=True)
class FedInputReadinessReport:
    as_of: datetime
    rate_input_status: FedRateInputStatus
    rate_snapshot_count: int
    validated_decision_count: int
    usd_calendar_candidate_count: int
    blockers: tuple[str, ...]

    @property
    def council_registration_authorized(self) -> bool:
        # This report MUST NOT be used as a self-issued approval token.
        return False

    @property
    def historical_replay_authorized(self) -> bool:
        return False

    @property
    def fed_persistence_authorized(self) -> bool:
        return False

    def require_council_registration(self) -> None:
        """Legacy operational-admission gate; not needed to catalogue a class."""
        raise FedInputNotReady(
            "Fed Seat 25 registration blocked: " + "; ".join(self.blockers)
        )


def inspect_fed_input_readiness(context: AgentContext) -> FedInputReadinessReport:
    """Inspect candidate rate and USD calendar data without admitting either.

    USD calendar entries are diagnostic candidates only. Source fields and
    event names are not independent attestations of an official FOMC calendar.
    Economic F1 hashes prove consistency of paired values, not source history.
    """

    if not isinstance(context, AgentContext):
        raise TypeError("Fed readiness requires AgentContext.")

    as_of = context.as_of

    if (
        not isinstance(as_of, datetime)
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
    ):
        raise ValueError("Fed readiness requires timezone-aware as_of.")

    if context.fed_data is None or context.economic_calendar is None:
        raise TypeError("Fed readiness requires tuple-like context inputs.")

    supplied = tuple(context.fed_data)
    locked = ()
    status = FedRateInputStatus.ABSENT

    if supplied:
        try:
            locked = lock_fed_data_as_of(supplied, as_of)

            if locked != supplied:
                raise FedReplayLockError(
                    "Fed input was omitted, reordered or revised by the replay lock."
                )

        except (FedReplayLockError, TypeError, ValueError):
            status = FedRateInputStatus.INVALID
        else:
            status = FedRateInputStatus.INTERNALLY_CONSISTENT

    usd_candidates = sum(
        1
        for event in context.economic_calendar
        if isinstance(event, EconomicCalendarEventSnapshot)
        and event.currency == "USD"
        and event.available_at <= as_of
        and event.ingested_at <= as_of
        and event.ingested_at >= event.available_at
    )

    blockers = []

    if status is FedRateInputStatus.ABSENT:
        blockers.append("FED_RATE_INPUT_ABSENT")

    elif status is FedRateInputStatus.INVALID:
        blockers.append("FED_RATE_INPUT_INVALID")

    # Independent historical provenance, an authenticated FOMC calendar,
    # and an audited operational Fed context adapter are not available.
    # None of these gates may be overridden by caller-provided flags.
    blockers.extend((
        "FED_SOURCE_VERSION_ATTESTATION_MISSING",
        "FED_CALENDAR_SOURCE_NOT_VERIFIED",
        "FED_CONTEXT_ACQUISITION_NOT_ACTIVATED",
    ))

    return FedInputReadinessReport(
        as_of=as_of,
        rate_input_status=status,
        rate_snapshot_count=len(supplied),
        validated_decision_count=(
            len(locked) // 2
            if status is FedRateInputStatus.INTERNALLY_CONSISTENT
            else 0
        ),
        usd_calendar_candidate_count=usd_candidates,
        blockers=tuple(blockers),
    )
