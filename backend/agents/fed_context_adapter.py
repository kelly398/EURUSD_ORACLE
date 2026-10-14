
"""F4.5.3: Quarantined Fed input preflight for an operational context.

Accept already-acquired candidate snapshots, check the existing F1 and
AgentContext contracts, and emit a diagnostic-only manifest fingerprint.

Never return a Fed-populated context or admit data to the production Council.

No network, database, broker, or registry calls are made here.
A SHA-256 manifest fingerprint is NOT a historical source attestation.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from itertools import islice
import json
from typing import Iterable

from pydantic import ValidationError

from backend.agents.contracts import AgentContext
from backend.agents.fed_input_readiness import (
    FedInputNotReady,
    FedInputReadinessReport,
    FedRateInputStatus,
    inspect_fed_input_readiness,
)
from backend.macro.fed_replay_lock import (
    FedReplayLockError,
    lock_fed_data_as_of,
)
from backend.macro.macro_schemas import CentralBankRateSnapshot


MAX_FED_CANDIDATE_SNAPSHOTS = 256


class FedContextAdapterError(ValueError):
    """A candidate input window cannot be quarantined as canonical."""


@dataclass(frozen=True)
class FedContextQuarantineReceipt:
    as_of: datetime
    candidate_snapshot_count: int
    diagnostic_manifest_sha256: str
    readiness: FedInputReadinessReport

    @property
    def council_context_admission_authorized(self) -> bool:
        return False

    @property
    def council_registration_authorized(self) -> bool:
        return False

    @property
    def historical_replay_authorized(self) -> bool:
        return False

    @property
    def fed_persistence_authorized(self) -> bool:
        return False

    def require_council_context_delivery(self) -> None:
        raise FedInputNotReady(
            "Fed operational context delivery remains blocked: "
            + "; ".join(self.readiness.blockers)
        )


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )


def _manifest_digest(
    as_of: datetime,
    snapshots: tuple[CentralBankRateSnapshot, ...],
) -> str:
    """Fingerprint candidate values and observation timestamps.

    The digest supports repeatable diagnostics. It provides no
    independent assurance about source authenticity or publication.
    """
    payload = {
        "contract": "fed_candidate_quarantine_v1",
        "as_of_utc": _utc_timestamp(as_of),
        "snapshots": [
            {
                "central_bank": item.central_bank,
                "source": item.source,
                "rate_name": item.rate_name,
                "source_record_id": item.source_record_id,
                "reference_date": item.reference_date.isoformat(),
                "available_at_utc": _utc_timestamp(
                    item.available_at
                ),
                "ingested_at_utc": _utc_timestamp(
                    item.ingested_at
                ),
                "value_percent": repr(
                    float(item.value_percent)
                ),
                "economic_content_hash": item.content_hash,
            }
            for item in snapshots
        ],
    }

    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    return sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def quarantine_fed_context_candidates(
    base_context: AgentContext,
    candidate_snapshots: Iterable[CentralBankRateSnapshot],
) -> FedContextQuarantineReceipt:
    """Inspect Fed candidates without delivering a Fed context.

    The normal operational context must have empty fed_data.

    Only a complete, ordered, already as-of-eligible candidate
    window may receive a diagnostic receipt.

    No future observations, conflicting versions, duplicates,
    or revisions may be silently removed or normalized.
    """

    if not isinstance(base_context, AgentContext):
        raise TypeError(
            "Fed context quarantine requires AgentContext."
        )

    if (
        not isinstance(base_context.as_of, datetime)
        or base_context.as_of.tzinfo is None
        or base_context.as_of.utcoffset() is None
    ):
        raise FedContextAdapterError(
            "Fed context requires aware as_of."
        )

    # Pydantic model_copy(update=...) bypasses model validation.
    # Revalidate the entire original context before using it.
    try:
        validated_base = AgentContext.model_validate(
            base_context.model_dump()
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise FedContextAdapterError(
            "Fed base context failed validation."
        ) from exc

    if validated_base.fed_data:
        raise FedContextAdapterError(
            "Fed base context must be empty; "
            "candidate data is quarantined."
        )

    if candidate_snapshots is None:
        raise TypeError(
            "Fed candidate snapshots cannot be None."
        )

    try:
        supplied = tuple(
            islice(
                candidate_snapshots,
                MAX_FED_CANDIDATE_SNAPSHOTS + 1,
            )
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "Fed candidate snapshots must be iterable."
        ) from exc

    if len(supplied) > MAX_FED_CANDIDATE_SNAPSHOTS:
        raise FedContextAdapterError(
            "Fed candidate snapshot limit exceeded."
        )

    for item in supplied:
        if type(item) is not CentralBankRateSnapshot:
            raise FedContextAdapterError(
                "Fed candidate has invalid snapshot type."
            )

        # Reject unvalidated model_copy mutations before serialization.
        if (
            type(item.reference_date) is not date
            or type(item.available_at) is not datetime
            or type(item.ingested_at) is not datetime
            or type(item.value_percent) is not float
        ):
            raise FedContextAdapterError(
                "Fed candidate snapshot requires canonical types."
            )

        try:
            validated_item = CentralBankRateSnapshot.model_validate(
                item.model_dump()
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FedContextAdapterError(
                "Fed candidate snapshot validation failed."
            ) from exc

        if validated_item.model_dump() != item.model_dump():
            raise FedContextAdapterError(
                "Fed candidate snapshot requires coercion."
            )

    try:
        locked = lock_fed_data_as_of(
            supplied,
            validated_base.as_of,
        )
    except (
        FedReplayLockError,
        TypeError,
        ValueError,
    ) as exc:
        raise FedContextAdapterError(
            "Fed candidate replay-lock validation failed."
        ) from exc

    # Do not silently discard future data, select revisions,
    # remove duplicates, or reorder candidate observations.
    if locked != supplied:
        raise FedContextAdapterError(
            "Fed candidate window is not exactly canonical "
            "and as-of eligible."
        )

    # Construct an ephemeral, fully validated diagnostic context.
    # This object is never returned or dispatched to CouncilRunner.
    candidate_payload = validated_base.model_dump()
    candidate_payload["fed_data"] = supplied

    try:
        diagnostic_context = AgentContext.model_validate(
            candidate_payload
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise FedContextAdapterError(
            "Fed candidate context failed validation."
        ) from exc

    report = inspect_fed_input_readiness(
        diagnostic_context
    )

    expected_status = (
        FedRateInputStatus.INTERNALLY_CONSISTENT
        if supplied
        else FedRateInputStatus.ABSENT
    )

    if report.rate_input_status is not expected_status:
        raise FedContextAdapterError(
            "Fed candidate readiness is inconsistent."
        )

    return FedContextQuarantineReceipt(
        as_of=validated_base.as_of,
        candidate_snapshot_count=len(supplied),
        diagnostic_manifest_sha256=_manifest_digest(
            validated_base.as_of,
            supplied,
        ),
        readiness=report,
    )
