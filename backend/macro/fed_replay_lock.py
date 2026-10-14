"""F4.3.1: Pure, conservative Fed target-range replay selection.

This checks snapshot structure, economic hashes, and as-of chronology.
It does NOT authenticate source availability, certify historical Fed bytes,
open a database, or authorize persistence.
"""

from datetime import datetime
from decimal import Decimal
from math import isfinite

from backend.macro.fed_rate_protocol import (
    FED_CENTRAL_BANK,
    FED_RATE_SOURCE,
    FedRateProtocolError,
    build_fed_content_hash,
    build_fed_record_id,
)
from backend.macro.macro_schemas import CentralBankRateSnapshot


class FedReplayLockError(ValueError):
    """A Fed snapshot set cannot be safely represented in replay."""


def _identity(item: CentralBankRateSnapshot) -> tuple[str, str]:
    if item.central_bank != FED_CENTRAL_BANK or item.source != FED_RATE_SOURCE:
        raise FedReplayLockError(
            "Fed replay requires the official Fed source identity."
        )

    prefix = "FOMC:"

    if not item.source_record_id.startswith(prefix):
        raise FedReplayLockError(
            "Fed replay record identity is not canonical."
        )

    parts = item.source_record_id[len(prefix):].split(":")

    if len(parts) != 2 or parts[1] not in ("lower", "upper"):
        raise FedReplayLockError(
            "Fed replay record identity is not canonical."
        )

    statement_id, bound = parts

    try:
        expected = build_fed_record_id(statement_id, bound)
    except FedRateProtocolError as exc:
        raise FedReplayLockError(
            "Fed replay record identity is not canonical."
        ) from exc

    if (
        item.source_record_id != expected
        or item.rate_name != f"federal_funds_target_{bound}"
    ):
        raise FedReplayLockError(
            "Fed replay record identity disagrees with rate name."
        )

    if item.ingested_at < item.available_at:
        raise FedReplayLockError(
            "Fed replay ingestion predates claimed availability."
        )

    if not isfinite(item.value_percent):
        raise FedReplayLockError(
            "Fed replay rate must be finite."
        )

    return statement_id, bound


def lock_fed_data_as_of(
    fed_data: tuple[CentralBankRateSnapshot, ...],
    as_of: datetime,
) -> tuple[CentralBankRateSnapshot, ...]:
    """Lock complete paired Fed decisions to a replay information timestamp.

    A version is eligible only if BOTH its claimed public-availability and
    actual ingestion timestamps are at or before as_of.

    Eligible revisions are resolved by the newest available_at per
    source_record_id. Ambiguous versions and incomplete/mixed
    lower-upper pairs fail closed.

    The input is assumed to have passed upstream source certification.
    Chronology fields and reproducible F1 economic hashes are insufficient
    by themselves to prove historical source authenticity.
    """

    if (
        not isinstance(as_of, datetime)
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
    ):
        raise FedReplayLockError(
            "Fed replay as_of must be timezone-aware."
        )

    if fed_data is None:
        raise TypeError(
            "Fed replay input must be an iterable of snapshots."
        )

    versions_by_id: dict[
        str,
        dict[datetime, CentralBankRateSnapshot]
    ] = {}

    for item in tuple(fed_data):

        if not isinstance(item, CentralBankRateSnapshot):
            raise TypeError(
                "Fed replay requires CentralBankRateSnapshot items."
            )

        _identity(item)

        if item.available_at > as_of or item.ingested_at > as_of:
            continue

        by_timestamp = versions_by_id.setdefault(
            item.source_record_id, {}
        )

        existing = by_timestamp.get(item.available_at)

        if existing is not None:

            if (
                item.content_hash != existing.content_hash
                or item.reference_date != existing.reference_date
                or item.value_percent != existing.value_percent
            ):
                raise FedReplayLockError(
                    "Ambiguous Fed revisions share an availability timestamp."
                )

            if item.ingested_at >= existing.ingested_at:
                continue

        by_timestamp[item.available_at] = item

    # Resolve newest eligible version only AFTER detecting all revision
    # timestamp conflicts, including conflicts in superseded history.

    eligible_by_id = {
        record_id: max(
            by_timestamp.values(),
            key=lambda item: item.available_at,
        )
        for record_id, by_timestamp in versions_by_id.items()
    }

    decisions: dict[
        str,
        dict[str, CentralBankRateSnapshot]
    ] = {}

    for item in eligible_by_id.values():
        statement_id, bound = _identity(item)
        decisions.setdefault(statement_id, {})[bound] = item

    locked = []

    for statement_id, pair in decisions.items():

        if set(pair) != {"lower", "upper"}:
            raise FedReplayLockError(
                "Fed replay requires complete lower/upper pairs."
            )

        lower, upper = pair["lower"], pair["upper"]

        if (
            lower.reference_date != upper.reference_date
            or lower.available_at != upper.available_at
            or lower.ingested_at != upper.ingested_at
        ):
            raise FedReplayLockError(
                "Fed replay decision pair has inconsistent provenance."
            )

        if lower.value_percent > upper.value_percent:
            raise FedReplayLockError(
                "Fed replay lower target exceeds upper target."
            )

        # F1 hashes cover BOTH economic bounds, effective date and
        # statement identity. This proves internal consistency, not
        # authenticity of the historical source.

        lower_text = format(
            Decimal(str(lower.value_percent)), "f"
        )
        upper_text = format(
            Decimal(str(upper.value_percent)), "f"
        )

        try:
            expected_lower = build_fed_content_hash(
                statement_id,
                lower.rate_name,
                lower.reference_date,
                lower_text,
                upper_text,
            )

            expected_upper = build_fed_content_hash(
                statement_id,
                upper.rate_name,
                upper.reference_date,
                lower_text,
                upper_text,
            )

        except FedRateProtocolError as exc:
            raise FedReplayLockError(
                "Fed replay economic target range is invalid."
            ) from exc

        if (
            lower.content_hash != expected_lower
            or upper.content_hash != expected_upper
        ):
            raise FedReplayLockError(
                "Fed replay content hash does not match paired economics."
            )

        locked.extend((lower, upper))

    return tuple(
        sorted(
            locked,
            key=lambda item: (
                item.reference_date,
                item.rate_name,
                item.source_record_id,
            ),
        )
    )