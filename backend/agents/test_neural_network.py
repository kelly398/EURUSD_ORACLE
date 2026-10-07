from datetime import (
    datetime,
    timedelta,
    timezone,
)

from time import (
    perf_counter,
)

from backend.agents.contracts import (
    AgentCategory,
    AgentDirection,
    AgentSignalType,
)

from backend.agents.implementations.neural_network_agent import (
    NeuralNetworkAgent,
)

from backend.market_state.candle_repository import (
    CandleRepository,
)

from backend.market_state.tick_repository import (
    TickRepository,
)

from backend.models.feature_builder import (
    MLFeatureBuilder,
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder,
)

from backend.models.neural_model_registry import (
    NeuralModelRegistry,
)


MODEL_NAME = (
    "neural_network_eurusd_m5_v1"
)


def expected_direction_from_score(
    score,
):

    if score >= 0.15:

        return AgentDirection.LONG

    if score <= -0.15:

        return AgentDirection.SHORT

    return AgentDirection.NEUTRAL


def member_vote_agreement(
    member_probabilities,
):

    member_predictions = []

    for probabilities in (
        member_probabilities
    ):

        predicted_class = max(

            (
                0,
                1,
                2,
            ),

            key=lambda class_id:
                probabilities[
                    class_id
                ],
        )

        member_predictions.append(
            predicted_class
        )

    highest_vote_count = max(

        member_predictions.count(
            class_id
        )

        for class_id
        in (
            0,
            1,
            2,
        )
    )

    return (

        highest_vote_count

        / len(
            member_predictions
        )
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL NETWORK AGENT TEST"
    )

    print(
        "=" * 70
    )


    # ==================================
    # FIXED HISTORICAL SNAPSHOT
    # ==================================

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
            minutes=5,
        )
    )


    print(
        f"\nHistorical anchor: "
        f"{anchor}"
    )


    # ==================================
    # LOAD SNAPSHOT-LOCKED DATA
    # ==================================

    candle_repository = (
        CandleRepository(
            source="MT5",
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


    tick_start = (

        as_of

        - timedelta(
            minutes=65,
        )
    )


    ticks = (
        tick_repository
        .get_between(

            start=
                tick_start,

            end=
                as_of,

            symbol=
                "EURUSD",
        )
    )


    print(
        f"Candles loaded: "
        f"{len(candles)}"
    )

    print(
        f"Ticks loaded: "
        f"{len(ticks)}"
    )


    assert (
        len(candles)
        >= 140
    )


    assert (
        len(ticks)
        > 0
    )


    # ==================================
    # HISTORICAL REPLAY CONTEXT
    # ==================================

    replay_builder = (
        HistoricalReplayBuilder(

            symbol="EURUSD",

            timeframe="M5",

            timeframe_minutes=5,

            candle_limit=200,

            tick_lookback_minutes=65,
        )
    )


    replay = (
        replay_builder.build(

            candles=
                candles,

            ticks=
                ticks,

            anchor_timestamp=
                anchor,
        )
    )


    context = (
        replay.context
    )


    print(
        f"Replay as-of: "
        f"{context.as_of}"
    )

    print(
        f"Replay candles: "
        f"{replay.candle_count}"
    )

    print(
        f"Replay ticks: "
        f"{replay.tick_count}"
    )


    # ==================================
    # LIVE FEATURE CONTRACT
    # ==================================

    feature_builder = (
        MLFeatureBuilder()
    )


    feature_vector = (
        feature_builder.build(
            context
        )
    )


    assert (
        feature_vector.feature_version
        == "eurusd_m5_v1"
    )


    assert (
        len(
            feature_vector.feature_names
        )
        == 27
    )


    assert (
        len(
            feature_vector.values
        )
        == 27
    )


    # ==================================
    # INDEPENDENT PRODUCTION ARTIFACT
    # ==================================

    registry = (
        NeuralModelRegistry()
    )


    artifact = (
        registry.load(
            MODEL_NAME
        )
    )


    metadata = (
        artifact.metadata
    )


    assert (
        artifact.model_name
        == MODEL_NAME
    )


    assert (
        artifact.member_count
        == 3
    )


    assert (
        artifact.feature_count
        == 27
    )


    model_feature_names = (
        artifact.feature_names
    )


    live_feature_names = tuple(
        feature_vector.feature_names
    )


    # Same feature schema is mandatory.
    assert (
        set(
            model_feature_names
        )
        == set(
            live_feature_names
        )
    )


    assert (
        len(
            model_feature_names
        )
        == 27
    )


    # ==================================
    # TRAINING-TIME FEATURE ORDER
    # ==================================

    feature_dict = (
        feature_vector.as_dict()
    )


    ordered_values = tuple(

        float(
            feature_dict[
                name
            ]
        )

        for name
        in model_feature_names
    )


    assert (
        len(
            ordered_values
        )
        == 27
    )


    # ==================================
    # EXPECTED PRODUCTION INFERENCE
    # ==================================

    expected = (
        artifact.infer(

            values=
                ordered_values,

            feature_names=
                model_feature_names,
        )
    )


    expected_short = float(
        expected.probabilities[
            0
        ]
    )


    expected_neutral = float(
        expected.probabilities[
            1
        ]
    )


    expected_long = float(
        expected.probabilities[
            2
        ]
    )


    expected_score = (

        expected_long

        - expected_short
    )


    expected_raw_confidence = float(
        expected.confidence
    )


    expected_disagreement = float(
        expected.probability_disagreement
    )


    expected_adjusted_confidence = max(

        0.0,

        min(

            1.0,

            (
                expected_raw_confidence

                - expected_disagreement
            ),
        ),
    )


    expected_vote_agreement = (
        member_vote_agreement(

            expected
            .member_probabilities
        )
    )


    expected_class_name = {

        0:
            "short",

        1:
            "neutral",

        2:
            "long",

    }[
        expected.predicted_class
    ]


    # ==================================
    # REAL SEAT 12 EXECUTION
    # ==================================

    agent = (
        NeuralNetworkAgent()
    )


    # Prove lazy loading.
    assert (
        agent._artifact
        is None
    )


    first_started = (
        perf_counter()
    )


    opinion_one = (
        agent.run(
            context
        )
    )


    first_duration_ms = (

        perf_counter()
        - first_started

    ) * 1000.0


    # Artifact must now remain resident.
    assert (
        agent._artifact
        is not None
    )


    loaded_artifact = (
        agent._artifact
    )


    second_started = (
        perf_counter()
    )


    opinion_two = (
        agent.run(
            context
        )
    )


    second_duration_ms = (

        perf_counter()
        - second_started

    ) * 1000.0


    # Second vote must reuse the exact
    # already-loaded production artifact.
    assert (
        agent._artifact
        is loaded_artifact
    )


    print(
        f"\nFirst Seat 12 run: "
        f"{first_duration_ms:.2f} ms"
    )

    print(
        f"Warm Seat 12 run: "
        f"{second_duration_ms:.2f} ms"
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "SEAT 12 OPINION"
    )

    print(
        "-" * 70
    )


    print(
        f"Direction: "
        f"{opinion_one.direction.value}"
    )

    print(
        f"Score: "
        f"{opinion_one.score:.6f}"
    )

    print(
        f"Confidence: "
        f"{opinion_one.confidence:.6f}"
    )

    print(
        "Raw confidence: "
        f"{opinion_one.evidence['raw_confidence']:.6f}"
    )

    print(
        "Ensemble disagreement: "
        f"{opinion_one.evidence['ensemble_disagreement']:.6f}"
    )

    print(
        "Member vote agreement: "
        f"{opinion_one.evidence['member_vote_agreement']:.6f}"
    )

    print(
        "Short probability: "
        f"{opinion_one.evidence['short_probability']:.6f}"
    )

    print(
        "Neutral probability: "
        f"{opinion_one.evidence['neutral_probability']:.6f}"
    )

    print(
        "Long probability: "
        f"{opinion_one.evidence['long_probability']:.6f}"
    )


    # ==================================
    # AGENT CONTRACT
    # ==================================

    assert (
        opinion_one.agent_id
        == "neural_network"
    )


    assert (
        opinion_one.agent_name
        == "Neural Network"
    )


    assert (
        opinion_one.category
        == AgentCategory.MACHINE_LEARNING
    )


    assert (
        opinion_one.signal_type
        == AgentSignalType.DIRECTIONAL
    )


    assert (
        opinion_one.as_of
        == context.as_of
    )


    assert (
        -1.0
        <= opinion_one.score
        <= 1.0
    )


    assert (
        0.0
        <= opinion_one.confidence
        <= 1.0
    )


    assert (
        opinion_one.direction
        == expected_direction_from_score(
            expected_score
        )
    )


    # ==================================
    # PRODUCTION METADATA EVIDENCE
    # ==================================

    assert (
        opinion_one.evidence[
            "model_name"
        ]
        == MODEL_NAME
    )


    assert (
        opinion_one.evidence[
            "dataset_version"
        ]
        == metadata[
            "dataset_version"
        ]
    )


    assert (
        opinion_one.evidence[
            "feature_version"
        ]
        == metadata[
            "feature_version"
        ]
    )


    assert (
        opinion_one.evidence[
            "label_version"
        ]
        == metadata[
            "label_version"
        ]
    )


    assert (
        opinion_one.evidence[
            "feature_count"
        ]
        == 27
    )


    assert (
        opinion_one.evidence[
            "member_count"
        ]
        == 3
    )


    assert abs(

        opinion_one.evidence[
            "temperature"
        ]

        - artifact.temperature

    ) < 1e-12


    # ==================================
    # EXACT INFERENCE EQUALITY
    # ==================================

    assert abs(

        opinion_one.evidence[
            "short_probability"
        ]

        - expected_short

    ) < 1e-12


    assert abs(

        opinion_one.evidence[
            "neutral_probability"
        ]

        - expected_neutral

    ) < 1e-12


    assert abs(

        opinion_one.evidence[
            "long_probability"
        ]

        - expected_long

    ) < 1e-12


    assert abs(

        opinion_one.score

        - expected_score

    ) < 1e-12


    assert abs(

        opinion_one.evidence[
            "directional_edge"
        ]

        - expected_score

    ) < 1e-12


    assert abs(

        opinion_one.evidence[
            "raw_confidence"
        ]

        - expected_raw_confidence

    ) < 1e-12


    assert abs(

        opinion_one.evidence[
            "ensemble_disagreement"
        ]

        - expected_disagreement

    ) < 1e-12


    assert abs(

        opinion_one.evidence[
            "ensemble_adjusted_confidence"
        ]

        - expected_adjusted_confidence

    ) < 1e-12


    assert abs(

        opinion_one.confidence

        - expected_adjusted_confidence

    ) < 1e-12


    assert abs(

        opinion_one.evidence[
            "member_vote_agreement"
        ]

        - expected_vote_agreement

    ) < 1e-12


    assert (
        opinion_one.evidence[
            "predicted_class"
        ]
        == expected_class_name
    )


    probability_sum = (

        opinion_one.evidence[
            "short_probability"
        ]

        + opinion_one.evidence[
            "neutral_probability"
        ]

        + opinion_one.evidence[
            "long_probability"
        ]
    )


    assert abs(

        probability_sum
        - 1.0

    ) < 1e-10


    # ==================================
    # ANTI-LOOKAHEAD
    # ==================================

    assert (
        context.candles[-1].timestamp
        == anchor
    )


    assert (
        context.candles[-1].timestamp
        < context.as_of
    )


    assert (
        context.ticks[-1].timestamp
        < context.as_of
    )


    # ==================================
    # DETERMINISM
    # ==================================

    assert (
        opinion_one.score
        == opinion_two.score
    )


    assert (
        opinion_one.confidence
        == opinion_two.confidence
    )


    assert (
        opinion_one.direction
        == opinion_two.direction
    )


    assert (
        opinion_one.evidence[
            "short_probability"
        ]
        == opinion_two.evidence[
            "short_probability"
        ]
    )


    assert (
        opinion_one.evidence[
            "neutral_probability"
        ]
        == opinion_two.evidence[
            "neutral_probability"
        ]
    )


    assert (
        opinion_one.evidence[
            "long_probability"
        ]
        == opinion_two.evidence[
            "long_probability"
        ]
    )


    assert (
        opinion_one.evidence[
            "raw_confidence"
        ]
        == opinion_two.evidence[
            "raw_confidence"
        ]
    )


    assert (
        opinion_one.evidence[
            "ensemble_disagreement"
        ]
        == opinion_two.evidence[
            "ensemble_disagreement"
        ]
    )


    assert (
        opinion_one.evidence[
            "member_vote_agreement"
        ]
        == opinion_two.evidence[
            "member_vote_agreement"
        ]
    )


    print(
        "\nHistorical replay: PASS"
    )

    print(
        "27-feature contract: PASS"
    )

    print(
        "Training feature order: PASS"
    )

    print(
        "Production artifact inference: PASS"
    )

    print(
        "Calibrated probability contract: PASS"
    )

    print(
        "Ensemble disagreement contract: PASS"
    )

    print(
        "Confidence adjustment contract: PASS"
    )

    print(
        "BaseAgent direction contract: PASS"
    )

    print(
        "Lazy artifact reuse: PASS"
    )

    print(
        "Timestamp leakage guards: PASS"
    )

    print(
        "Deterministic inference: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "NEURAL NETWORK AGENT "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
