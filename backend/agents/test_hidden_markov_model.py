import math
import sys

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import numpy as np

from backend.agents.contracts import (
    AgentContext,
    AgentDirection,
    AgentSignalType,
)

from backend.agents.implementations.hidden_markov_model_agent import (
    HiddenMarkovModelAgent,
)

from backend.market_state.candle_repository import (
    CandleRepository,
)

from backend.market_state.tick_repository import (
    TickRepository,
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder,
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


def expected_confidence(
    posterior,
):
    posterior = np.asarray(
        posterior,
        dtype=np.float64,
    )

    safe = np.clip(
        posterior,
        1e-15,
        1.0,
    )

    normalized_entropy = (
        -np.sum(
            safe
            * np.log(
                safe
            )
        )
        / math.log(
            posterior.shape[
                0
            ]
        )
    )

    return float(
        1.0
        - normalized_entropy
    )


def build_historical_context():
    anchor = datetime(
        2026,
        9,
        30,
        12,
        0,
        tzinfo=timezone.utc,
    )

    as_of = (
        anchor
        + timedelta(
            minutes=5
        )
    )

    candle_repository = (
        CandleRepository(
            source="MT5"
        )
    )

    tick_repository = (
        TickRepository()
    )

    candles = (
        candle_repository
        .get_latest_as_of(
            latest_timestamp=
                anchor,

            symbol=
                "EURUSD",

            timeframe=
                "M5",

            limit=
                200,
        )
    )

    ticks = (
        tick_repository
        .get_between(
            start=(
                as_of
                - timedelta(
                    minutes=65
                )
            ),

            end=
                as_of,

            symbol=
                "EURUSD",
        )
    )

    assert (
        len(candles)
        == 200
    )

    replay = (
        HistoricalReplayBuilder(
            symbol="EURUSD",
            timeframe="M5",
            timeframe_minutes=5,
            candle_limit=200,
            tick_lookback_minutes=65,
        )
        .build(
            candles=
                candles,

            ticks=
                ticks,

            anchor_timestamp=
                anchor,
        )
    )

    return (
        replay.context
    )


def main():
    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "HIDDEN MARKOV MODEL AGENT TEST"
    )

    print(
        "=" * 70
    )

    assert (
        "hmmlearn"
        not in sys.modules
    )

    context = (
        build_historical_context()
    )

    registry = (
        HMMModelRegistry()
    )

    artifact = (
        registry.load()
    )

    builder = (
        HMMObservationBuilder(
            symbol="EURUSD",
            timeframe="M5",
            timeframe_minutes=5,
        )
    )

    sequence = (
        builder.build(
            context.candles
        )
    )

    assert (
        sequence.latest_as_of
        == context.as_of
    )

    standardized = (
        artifact[
            "standardizer"
        ]
        .transform(
            values=
                sequence.values,

            feature_names=
                sequence.feature_names,
        )
    )

    expected_filter = (
        causal_forward_filter(
            values=
                standardized,

            parameters=
                artifact[
                    "parameters"
                ],

            segment_lengths=
                sequence.segment_lengths,
        )
    )

    expected_posterior = (
        expected_filter
        .latest_state_probability
    )

    expected_state = int(
        np.argmax(
            expected_posterior
        )
    )

    expected_regime = (
        artifact[
            "state_names"
        ][
            expected_state
        ]
    )

    expected_context_confidence = (
        expected_confidence(
            expected_posterior
        )
    )

    expected_next = (
        expected_posterior

        @ artifact[
            "parameters"
        ].transition_matrix
    )

    expected_next_state = int(
        np.argmax(
            expected_next
        )
    )

    expected_stay = float(
        np.sum(
            expected_posterior
            * np.diag(
                artifact[
                    "parameters"
                ].transition_matrix
            )
        )
    )

    expected_switch = (
        1.0
        - expected_stay
    )

    expected_directional = (
        expected_posterior

        @ artifact[
            "state_outcome_probability"
        ]
    )

    # ==================================
    # REAL SEAT 13 EXECUTION
    # ==================================

    agent = (
        HiddenMarkovModelAgent(
            registry=
                registry
        )
    )

    opinion_one = (
        agent.run(
            context
        )
    )

    opinion_two = (
        agent.run(
            context
        )
    )

    assert (
        opinion_one
        == opinion_two
    )

    assert (
        opinion_one.agent_id
        == "hidden_markov_model"
    )

    assert (
        opinion_one.agent_name
        == "Hidden Markov Model"
    )

    assert (
        opinion_one.signal_type
        == AgentSignalType.CONTEXTUAL
    )

    assert (
        opinion_one.direction
        == AgentDirection.NEUTRAL
    )

    assert (
        opinion_one.score
        == 0.0
    )

    assert (
        opinion_one.as_of
        == context.as_of
    )

    assert (
        opinion_one.evidence[
            "regime_available"
        ]
        is True
    )

    assert (
        opinion_one.evidence[
            "current_state"
        ]
        == expected_state
    )

    assert (
        opinion_one.evidence[
            "current_regime"
        ]
        == expected_regime
    )

    assert np.isclose(
        opinion_one.confidence,
        expected_context_confidence,
        atol=1e-12,
        rtol=0.0,
    )

    assert np.isclose(
        opinion_one.evidence[
            "dominant_state_probability"
        ],
        expected_posterior[
            expected_state
        ],
        atol=1e-12,
        rtol=0.0,
    )

    assert np.isclose(
        opinion_one.evidence[
            "switch_probability"
        ],
        expected_switch,
        atol=1e-12,
        rtol=0.0,
    )

    assert (
        opinion_one.evidence[
            "next_state"
        ]
        == expected_next_state
    )

    for state_index in range(
        5
    ):
        assert np.isclose(
            opinion_one.evidence[
                f"state_{state_index}_probability"
            ],
            expected_posterior[
                state_index
            ],
            atol=1e-12,
            rtol=0.0,
        )

        assert np.isclose(
            opinion_one.evidence[
                f"next_state_{state_index}_probability"
            ],
            expected_next[
                state_index
            ],
            atol=1e-12,
            rtol=0.0,
        )

    assert np.isclose(
        opinion_one.evidence[
            "short_probability"
        ],
        expected_directional[
            0
        ],
        atol=1e-12,
        rtol=0.0,
    )

    assert np.isclose(
        opinion_one.evidence[
            "neutral_probability"
        ],
        expected_directional[
            1
        ],
        atol=1e-12,
        rtol=0.0,
    )

    assert np.isclose(
        opinion_one.evidence[
            "long_probability"
        ],
        expected_directional[
            2
        ],
        atol=1e-12,
        rtol=0.0,
    )

    assert np.isclose(
        opinion_one.evidence[
            "directional_edge_secondary"
        ],
        (
            expected_directional[
                2
            ]
            - expected_directional[
                0
            ]
        ),
        atol=1e-12,
        rtol=0.0,
    )

    # ==================================
    # POST-GAP / WARM-UP SAFETY
    # ==================================
    #
    # Fewer than 13 current contiguous
    # candles must never cause the last
    # pre-gap regime to be reused.
    # ==================================

    warmup_context = (
        AgentContext(
            symbol=
                context.symbol,

            timeframe=
                context.timeframe,

            as_of=
                context.as_of,

            market_state=
                context.market_state,

            candles=
                tuple(
                    context.candles[
                        -12:
                    ]
                ),

            ticks=
                context.ticks,
        )
    )

    warmup_opinion = (
        agent.run(
            warmup_context
        )
    )

    assert (
        warmup_opinion.signal_type
        == AgentSignalType.CONTEXTUAL
    )

    assert (
        warmup_opinion.direction
        == AgentDirection.NEUTRAL
    )

    assert (
        warmup_opinion.score
        == 0.0
    )

    assert (
        warmup_opinion.confidence
        == 0.0
    )

    assert (
        warmup_opinion.evidence[
            "regime_available"
        ]
        is False
    )

    assert (
        warmup_opinion.evidence[
            "latest_contiguous_candles"
        ]
        == 12
    )

    assert (
        "hmmlearn"
        not in sys.modules
    )

    print(
        f"\nCurrent regime: "
        f"{opinion_one.evidence['current_regime']}"
    )

    print(
        "Dominant posterior: "
        f"{opinion_one.evidence['dominant_state_probability']:.6f}"
    )

    print(
        "Context confidence: "
        f"{opinion_one.confidence:.6f}"
    )

    print(
        "Switch probability: "
        f"{opinion_one.evidence['switch_probability']:.6f}"
    )

    print(
        "Secondary directional edge: "
        f"{opinion_one.evidence['directional_edge_secondary']:+.6f}"
    )

    print(
        "\nContextual signal contract: PASS"
    )

    print(
        "Exact causal posterior: PASS"
    )

    print(
        "Entropy confidence: PASS"
    )

    print(
        "Transition intelligence: PASS"
    )

    print(
        "Directional evidence isolation: PASS"
    )

    print(
        "Deterministic execution: PASS"
    )

    print(
        "Post-gap warm-up safety: PASS"
    )

    print(
        "Runtime hmmlearn import: NO"
    )

    print(
        "\n"
        + "=" * 70
    )

    print(
        "HIDDEN MARKOV MODEL AGENT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":
    main()