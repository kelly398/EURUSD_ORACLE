import math

from datetime import timedelta

import numpy as np

from backend.agents.base_agent import (
    BaseAgent,
)

from backend.agents.contracts import (
    AgentCategory,
    AgentSignalType,
)

from backend.models.hmm_core import (
    causal_forward_filter,
)

from backend.models.hmm_model_registry import (
    HMMModelRegistry,
)

from backend.models.hmm_observation_builder import (
    HMMObservationBuilder,
)


class HiddenMarkovModelAgent(
    BaseAgent
):

    agent_id = (
        "hidden_markov_model"
    )

    agent_name = (
        "Hidden Markov Model"
    )

    category = (
        AgentCategory.STATISTICAL
    )

    signal_type = (
        AgentSignalType.CONTEXTUAL
    )

    version = (
        "1.0.0"
    )

    TIMEFRAME_MINUTES = (
        5
    )

    MINIMUM_CURRENT_CANDLES = (
        HMMObservationBuilder
        .MINIMUM_CONTIGUOUS_CANDLES
    )


    def __init__(
        self,
        registry=None,
    ):
        super().__init__()

        self.registry = (
            registry

            if registry is not None

            else HMMModelRegistry()
        )

        self.observation_builder = (
            HMMObservationBuilder(
                symbol="EURUSD",
                timeframe="M5",
                timeframe_minutes=
                    self.TIMEFRAME_MINUTES,
            )
        )


    @staticmethod
    def _latest_contiguous_count(
        candles,
    ):
        candles = tuple(
            candles
        )

        if not candles:
            return 0

        expected_delta = (
            timedelta(
                minutes=5
            )
        )

        count = (
            1
        )

        for index in range(
            len(candles) - 1,
            0,
            -1,
        ):
            current = (
                candles[
                    index
                ]
            )

            previous = (
                candles[
                    index - 1
                ]
            )

            if (
                current.timestamp
                - previous.timestamp
                != expected_delta
            ):
                break

            count += (
                1
            )

        return count


    @staticmethod
    def _context_confidence(
        posterior,
    ):
        posterior = np.asarray(
            posterior,
            dtype=np.float64,
        )

        state_count = (
            posterior.shape[
                0
            ]
        )

        if (
            state_count
            <= 1
        ):
            return 1.0

        safe_probability = np.clip(
            posterior,
            1e-15,
            1.0,
        )

        entropy = float(
            -np.sum(
                safe_probability
                * np.log(
                    safe_probability
                )
            )
        )

        normalized_entropy = (
            entropy
            / math.log(
                state_count
            )
        )

        confidence = (
            1.0
            - normalized_entropy
        )

        return float(
            np.clip(
                confidence,
                0.0,
                1.0,
            )
        )


    def _unavailable_opinion(
        self,
        context,
        latest_contiguous_count,
    ):
        return self.build_opinion(
            context=
                context,

            score=
                0.0,

            confidence=
                0.0,

            rationale=(
                "Current latent regime is "
                "unavailable because the latest "
                "continuous EUR/USD M5 sequence "
                "has not yet completed the "
                "13-candle HMM warm-up."
            ),

            evidence={
                "regime_available":
                    False,

                "latest_contiguous_candles":
                    int(
                        latest_contiguous_count
                    ),

                "required_contiguous_candles":
                    int(
                        self
                        .MINIMUM_CURRENT_CANDLES
                    ),

                "warmup_reason":
                    "insufficient_post_gap_history",
            },
        )


    def analyze(
        self,
        context,
    ):
        artifact = (
            self.registry.load()
        )

        metadata = (
            artifact[
                "metadata"
            ]
        )

        if (
            context.symbol
            != metadata[
                "symbol"
            ]
        ):
            raise RuntimeError(
                "HMM model symbol does not "
                "match AgentContext."
            )

        if (
            context.timeframe
            != metadata[
                "timeframe"
            ]
        ):
            raise RuntimeError(
                "HMM model timeframe does not "
                "match AgentContext."
            )

        latest_contiguous_count = (
            self._latest_contiguous_count(
                context.candles
            )
        )

        # ==================================
        # POST-GAP WARM-UP SAFETY
        # ==================================
        #
        # Never emit the final observation
        # from an earlier sequence as though
        # it represented the current market.
        # ==================================

        if (
            latest_contiguous_count
            < self.MINIMUM_CURRENT_CANDLES
        ):
            return (
                self._unavailable_opinion(
                    context=
                        context,

                    latest_contiguous_count=
                        latest_contiguous_count,
                )
            )

        sequence = (
            self.observation_builder
            .build(
                context.candles
            )
        )

        if (
            sequence.feature_version
            != metadata[
                "hmm_feature_version"
            ]
        ):
            raise RuntimeError(
                "Live HMM observation version "
                "does not match production "
                "artifact."
            )

        if (
            tuple(
                sequence.feature_names
            )
            != tuple(
                metadata[
                    "feature_names"
                ]
            )
        ):
            raise RuntimeError(
                "Live HMM observation feature "
                "order does not match the "
                "production artifact."
            )

        # The newest observation must
        # represent this exact AgentContext.
        # Otherwise the model would be using
        # stale pre-gap information.
        if (
            sequence.latest_as_of
            != context.as_of
        ):
            raise RuntimeError(
                "Latest HMM observation does "
                "not match AgentContext as_of. "
                "Refusing stale regime output."
            )

        standardizer = (
            artifact[
                "standardizer"
            ]
        )

        parameters = (
            artifact[
                "parameters"
            ]
        )

        state_outcome_probability = (
            artifact[
                "state_outcome_probability"
            ]
        )

        state_names = (
            artifact[
                "state_names"
            ]
        )

        standardized = (
            standardizer.transform(
                values=
                    sequence.values,

                feature_names=
                    sequence.feature_names,
            )
        )

        filtered = (
            causal_forward_filter(
                values=
                    standardized,

                parameters=
                    parameters,

                segment_lengths=
                    sequence.segment_lengths,
            )
        )

        posterior = np.asarray(
            filtered
            .latest_state_probability,
            dtype=np.float64,
        )

        if (
            posterior.shape
            != (
                parameters.state_count,
            )
        ):
            raise RuntimeError(
                "Unexpected HMM posterior shape."
            )

        if not np.isfinite(
            posterior
        ).all():
            raise RuntimeError(
                "HMM posterior contains "
                "invalid values."
            )

        current_state = int(
            np.argmax(
                posterior
            )
        )

        current_regime = (
            state_names[
                current_state
            ]
        )

        dominant_probability = float(
            posterior[
                current_state
            ]
        )

        confidence = (
            self._context_confidence(
                posterior
            )
        )

        transition_matrix = (
            parameters.transition_matrix
        )

        next_state_probability = (
            posterior
            @ transition_matrix
        )

        next_state = int(
            np.argmax(
                next_state_probability
            )
        )

        next_regime = (
            state_names[
                next_state
            ]
        )

        expected_stay_probability = float(
            np.sum(
                posterior
                * np.diag(
                    transition_matrix
                )
            )
        )

        switch_probability = float(
            np.clip(
                1.0
                - expected_stay_probability,
                0.0,
                1.0,
            )
        )

        directional_probability = (
            posterior
            @ state_outcome_probability
        )

        directional_probability = (
            directional_probability
            / directional_probability.sum()
        )

        short_probability = float(
            directional_probability[
                0
            ]
        )

        neutral_probability = float(
            directional_probability[
                1
            ]
        )

        long_probability = float(
            directional_probability[
                2
            ]
        )

        directional_edge = float(
            long_probability
            - short_probability
        )

        evidence = {
            "regime_available":
                True,

            "model_name":
                metadata[
                    "model_name"
                ],

            "hmm_feature_version":
                metadata[
                    "hmm_feature_version"
                ],

            "state_count":
                int(
                    parameters.state_count
                ),

            "selected_seed":
                int(
                    metadata[
                        "selected_seed"
                    ]
                ),

            "current_state":
                current_state,

            "current_regime":
                current_regime,

            "dominant_state_probability":
                dominant_probability,

            "context_confidence":
                confidence,

            "next_state":
                next_state,

            "next_regime":
                next_regime,

            "next_regime_probability":
                float(
                    next_state_probability[
                        next_state
                    ]
                ),

            "expected_stay_probability":
                expected_stay_probability,

            "switch_probability":
                switch_probability,

            "short_probability":
                short_probability,

            "neutral_probability":
                neutral_probability,

            "long_probability":
                long_probability,

            "directional_edge_secondary":
                directional_edge,

            "observation_count":
                int(
                    sequence.observation_count
                ),

            "current_segment_observations":
                int(
                    sequence.segment_lengths[
                        -1
                    ]
                ),
        }

        # EvidenceValue intentionally allows
        # only scalar values, so expose each
        # posterior component separately.
        for state_index in range(
            parameters.state_count
        ):
            evidence[
                f"state_{state_index}_probability"
            ] = float(
                posterior[
                    state_index
                ]
            )

            evidence[
                f"next_state_{state_index}_probability"
            ] = float(
                next_state_probability[
                    state_index
                ]
            )

        return self.build_opinion(
            context=
                context,

            # Frozen Seat 13 policy:
            # contextual intelligence only.
            score=
                0.0,

            confidence=
                confidence,

            rationale=(
                "Validated causal Gaussian HMM "
                "identified the current latent "
                f"EUR/USD regime as "
                f"{current_regime} "
                f"(posterior "
                f"{dominant_probability:.3f}); "
                f"estimated regime-switch "
                f"probability is "
                f"{switch_probability:.3f}."
            ),

            evidence=
                evidence,
        )