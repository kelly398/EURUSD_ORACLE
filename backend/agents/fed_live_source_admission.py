"""F5.2: current HTTPS Fed source observation into a one-use live preview.

The ONLY production acquisition path here constructs FedRateClient itself:
verified HTTPS to fixed official Fed URLs, no redirects, full original-source
bundle, and the F1 economic/temporal validators. The capture time, NOT an old
press-release timestamp, gates when new contexts may use the observation.

This is not historical version attestation. It provides no persistence,
replay, Council registration, or production factory build permission.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets

from pydantic import ValidationError

from backend.agents.contracts import AgentContext
from backend.agents.fed_input_readiness import (
    FedInputReadinessReport,
    FedRateInputStatus,
    inspect_fed_input_readiness,
)
from backend.macro.fed_rate_backfill import FedRateBackfillPlanner
from backend.macro.fed_rate_client import FedRateClient
from backend.macro.fed_replay_lock import FedReplayLockError, lock_fed_data_as_of


MAX_CAPTURE_SECONDS = 120
MAX_PREVIEW_AGE_SECONDS = 120
MAX_PENDING_CAPTURES = 8


class FedLiveSourceAdmissionError(ValueError):
    """A purported live Fed source capture cannot enter a preview context."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime, label: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FedLiveSourceAdmissionError(f"{label} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _snapshots_digest(items) -> str:
    canonical = [
        {
            "central_bank": item.central_bank,
            "source": item.source,
            "rate_name": item.rate_name,
            "source_record_id": item.source_record_id,
            "reference_date": item.reference_date.isoformat(),
            "available_at": item.available_at.astimezone(timezone.utc).isoformat(),
            "ingested_at": item.ingested_at.astimezone(timezone.utc).isoformat(),
            "value": repr(item.value_percent),
            "hash": item.content_hash,
        }
        for item in items
    ]
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FedLiveCaptureReceipt:
    capture_id: str
    statement_id: str
    observed_at: datetime
    document_sha256: tuple[str, str, str]
    snapshots_sha256: str

    @property
    def historical_replay_authorized(self) -> bool:
        return False

    @property
    def fed_persistence_authorized(self) -> bool:
        return False

    @property
    def council_registration_authorized(self) -> bool:
        return False


@dataclass(frozen=True)
class FedLivePreview:
    """One-time preview. It is NOT a Council delivery authorization."""

    context: AgentContext
    capture: FedLiveCaptureReceipt
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


class FedLiveSourceAdmission:
    """Own actual source acquisition, one-time tickets, and preview binding.

    No caller-supplied Fed certification, parser, source client, HTTP opener,
    source timestamps, or rate snapshots are accepted as authority. A monkey-
    patched Python process and test doubles are outside this trust boundary.
    """

    def __init__(self):
        self._pending = {}

    def capture(self, statement_url: str) -> FedLiveCaptureReceipt:
        """Fetch/parse the official release NOW, not an archived source claim."""
        if len(self._pending) >= MAX_PENDING_CAPTURES:
            raise FedLiveSourceAdmissionError("Too many pending Fed captures.")

        started = _utc(_now(), "capture start")
        client = FedRateClient()
        # Detect accidental custom HTTP opener/method injection in the client.
        if (
            type(client) is not FedRateClient
            or client.opener != client._verified_open
        ):
            raise FedLiveSourceAdmissionError(
                "Official Fed acquisition requires the built-in verified transport."
            )

        plan = FedRateBackfillPlanner(client=client).prepare(statement_url)
        finished = _utc(_now(), "capture finish")
        if finished < started or finished - started > timedelta(seconds=MAX_CAPTURE_SECONDS):
            raise FedLiveSourceAdmissionError("Fed acquisition clock/window is invalid.")

        expected = FedRateClient.build_urls(statement_url)
        docs = plan.source_fingerprints
        if len(docs) != 3:
            raise FedLiveSourceAdmissionError("Fed source capture must contain three documents.")
        for fp, role, url in zip(
            docs, ("statement", "implementation", "meeting"), expected, strict=True
        ):
            if fp.role != role or fp.url != url:
                raise FedLiveSourceAdmissionError("Fed source document identity changed.")
            fetched = _utc(fp.fetched_at, "document fetch time")
            if fetched < started or fetched > finished:
                raise FedLiveSourceAdmissionError(
                    "Fed source was not observed during this capture."
                )

        if _utc(plan.ingested_at, "Fed ingestion") != _utc(docs[-1].fetched_at, "last fetch"):
            raise FedLiveSourceAdmissionError("Fed live ingestion must equal last fetch.")

        pair = tuple(plan.candidate_snapshots)
        try:
            locked = lock_fed_data_as_of(pair, finished)
        except (FedReplayLockError, TypeError, ValueError) as exc:
            raise FedLiveSourceAdmissionError("Fed source produced an invalid rate pair.") from exc
        if len(pair) != 2 or locked != pair:
            raise FedLiveSourceAdmissionError("Fed live rate pair is not canonical.")

        receipt = FedLiveCaptureReceipt(
            capture_id=secrets.token_hex(24),
            statement_id=plan.statement_id,
            observed_at=finished,
            document_sha256=tuple(fp.sha256 for fp in docs),
            snapshots_sha256=_snapshots_digest(pair),
        )
        # Keep an independent copy of the receipt fields so mutation of a
        # frozen dataclass using object.__setattr__ does not bypass the gate.
        self._pending[receipt.capture_id] = (
            (
                receipt.capture_id,
                receipt.statement_id,
                receipt.observed_at,
                receipt.document_sha256,
                receipt.snapshots_sha256,
            ),
            pair,
        )
        return receipt

    def materialize_preview(
        self, base_context: AgentContext, receipt: FedLiveCaptureReceipt
    ) -> FedLivePreview:
        """Consume one ticket and produce a fully validated post-capture preview.

        The caller must build a NEW market-state snapshot AFTER acquisition.
        Neither older/historical contexts nor future-dated contexts are valid.
        """
        if type(receipt) is not FedLiveCaptureReceipt:
            raise FedLiveSourceAdmissionError("A genuine Fed capture receipt is required.")
        if type(receipt.capture_id) is not str:
            raise FedLiveSourceAdmissionError("Fed capture ticket is invalid.")
        record = self._pending.pop(receipt.capture_id, None)
        if record is None:
            raise FedLiveSourceAdmissionError("Unknown or already consumed Fed capture.")
        bound_fields, pair = record
        supplied = (
            receipt.capture_id,
            receipt.statement_id,
            receipt.observed_at,
            receipt.document_sha256,
            receipt.snapshots_sha256,
        )
        if supplied != bound_fields or _snapshots_digest(pair) != receipt.snapshots_sha256:
            raise FedLiveSourceAdmissionError("Fed capture receipt was altered.")
        if not isinstance(base_context, AgentContext):
            raise FedLiveSourceAdmissionError("Fed preview requires AgentContext.")
        try:
            base = AgentContext.model_validate(base_context.model_dump())
        except (ValidationError, ValueError, TypeError) as exc:
            raise FedLiveSourceAdmissionError("Fed base context is not valid.") from exc
        if base.fed_data:
            raise FedLiveSourceAdmissionError("Fed base context must be empty.")

        now = _utc(_now(), "preview clock")
        as_of = _utc(base.as_of, "context as_of")
        observed_at = _utc(receipt.observed_at, "capture observation")
        if (
            as_of < observed_at
            or as_of > now
            or now < observed_at
            or now - observed_at > timedelta(seconds=MAX_PREVIEW_AGE_SECONDS)
        ):
            raise FedLiveSourceAdmissionError(
                "Fed preview needs a current, post-capture market snapshot."
            )

        try:
            if lock_fed_data_as_of(pair, as_of) != pair:
                raise FedLiveSourceAdmissionError("Fed live pair changed or is ineligible.")
            payload = base.model_dump()
            payload["fed_data"] = pair
            preview_context = AgentContext.model_validate(payload)
        except (ValidationError, FedReplayLockError, TypeError, ValueError) as exc:
            raise FedLiveSourceAdmissionError("Fed preview context validation failed.") from exc

        readiness = inspect_fed_input_readiness(preview_context)
        if (
            readiness.rate_input_status is not FedRateInputStatus.INTERNALLY_CONSISTENT
            or readiness.council_registration_authorized
        ):
            raise FedLiveSourceAdmissionError("Fed readiness boundary is inconsistent.")
        return FedLivePreview(context=preview_context, capture=receipt, readiness=readiness)