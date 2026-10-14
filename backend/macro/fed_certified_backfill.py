"""
F3.7.4 Certified Federal Reserve backfill orchestration.

Pipeline:

Fed sources
    |
    v
FedRateBackfillPlanner
    |
    v
ALFRED corroboration
    |
    v
FedEvidenceCertification
    |
    v
FedCertifiedRateStorage

This module does not bypass certification.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


from backend.macro.fed_rate_backfill import (
    FedRateBackfillPlanner,
)


from backend.macro.fed_rate_alfred_client import (
    FedAlfredClient,
)


from backend.macro.fed_rate_vintage_protocol import (
    corroborate_fed_effective_rate_vintage,
)


from backend.macro.fed_certification import (
    FedCertificationLevel,
    FedEvidenceCertification,
    FedEvidenceResult,
    FedAvailabilityLevel,
)


from backend.macro.fed_certified_storage import (
    FedCertifiedRateStorage,
)



class FedCertifiedBackfillError(
    ValueError
):
    """
    Certified Fed backfill failed.
    """



@dataclass(frozen=True)
class FedCertifiedBackfillResult:
    statement_id: str

    certification_hash: str

    certification_level: (
        FedCertificationLevel
    )

    persisted: bool

    snapshot_count: int



class FedCertifiedBackfill:

    """
    Full evidence-aware Fed backfill flow.

    Default behaviour:
        corroborate
        certify
        refuse persistence unless
        persistence approval exists.
    """

    def __init__(
        self,
        planner=None,
        alfred=None,
        storage=None,
    ):

        self.planner = (
            planner
            if planner is not None
            else FedRateBackfillPlanner()
        )

        self.alfred = (
            alfred
            if alfred is not None
            else FedAlfredClient()
        )

        self.storage = (
            storage
            if storage is not None
            else FedCertifiedRateStorage()
        )


    def certify_candidate(
        self,
        statement_url: str,
        vintage_date,
    ):

        plan = self.planner.prepare(
            statement_url
        )


        pair = self.alfred.fetch_pair(
            observation_date=
                plan.effective_date,

            vintage_date=
                vintage_date,
        )


        corroboration = (
            corroborate_fed_effective_rate_vintage(
                plan,
                *pair.observations,
            )
        )


        certification = (
            FedEvidenceCertification(
                statement_id=
                    plan.statement_id,

                economic_consistency=
                    FedEvidenceResult.PASS,

                source_integrity=
                    FedEvidenceResult.PASS,

                historical_availability=
                    FedAvailabilityLevel.DATE_BOUND,

                certification_level=
                    FedCertificationLevel.CORROBORATED,

                release_timestamp=
                    plan.announced_at,

                effective_date=
                    plan.effective_date,

                fed_document_hashes=
                    tuple(
                        fp.sha256
                        for fp in
                        plan.source_fingerprints
                    ),

                alfred_response_hashes=
                    corroboration.observation_hashes,

                reason=
                    (
                        "Fed decision matched "
                        "ALFRED effective-rate "
                        "observations."
                    ),
            )
        )


        return (
            plan,
            pair,
            certification,
        )


    def persist_certified(
        self,
        statement_url: str,
        vintage_date,
    ):

        plan, pair, certification = (
            self.certify_candidate(
                statement_url,
                vintage_date,
            )
        )


        if not certification.persistence_authorized:

            raise FedCertifiedBackfillError(
                "Evidence is corroborated but "
                "not persistence approved."
            )


        result = (
            self.storage.store_certified_fed_many(
                plan.candidate_snapshots,
                certification,
            )
        )


        return FedCertifiedBackfillResult(
            statement_id=
                plan.statement_id,

            certification_hash=
                result.certification_hash,

            certification_level=
                certification.certification_level,

            persisted=True,

            snapshot_count=
                result.snapshot_count,
        )