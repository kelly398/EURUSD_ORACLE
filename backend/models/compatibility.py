from dataclasses import dataclass

from backend.models.artifact_contracts import (
    ModelArtifactMetadata
)

from backend.models.feature_contracts import (
    ModelFeatureVector
)


@dataclass(frozen=True)
class CompatibilityResult:

    compatible: bool

    reasons: tuple[
        str,
        ...
    ]


class ModelCompatibilityChecker:

    def check(
        self,
        metadata:
            ModelArtifactMetadata,
        features:
            ModelFeatureVector
    ) -> CompatibilityResult:

        reasons = []


        if (
            metadata.symbol
            != features.symbol
        ):

            reasons.append(
                "symbol mismatch"
            )


        if (
            metadata.timeframe
            != features.timeframe
        ):

            reasons.append(
                "timeframe mismatch"
            )


        if (
            metadata.feature_version
            != features.feature_version
        ):

            reasons.append(
                "feature version mismatch"
            )


        if (
            metadata.feature_names
            != features.feature_names
        ):

            reasons.append(
                "ordered feature schema mismatch"
            )


        return CompatibilityResult(

            compatible=(
                len(reasons)
                == 0
            ),

            reasons=tuple(
                reasons
            )
        )


    def require_compatible(
        self,
        metadata,
        features
    ):

        result = (
            self.check(
                metadata,
                features
            )
        )


        if not result.compatible:

            raise RuntimeError(
                "Model artifact is "
                "incompatible with live "
                "ORACLE features: "
                + "; ".join(
                    result.reasons
                )
            )


        return result
        