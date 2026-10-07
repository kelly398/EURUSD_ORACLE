from datetime import (
    datetime,
    timedelta,
    timezone
)

from time import (
    perf_counter
)

from backend.agents.contracts import (
    AgentCategory,
    AgentSignalType
)

from backend.agents.implementations.ml_tree_ensemble_agent import (
    MLTreeEnsembleAgent
)

from backend.market_state.candle_repository import (
    CandleRepository
)

from backend.market_state.tick_repository import (
    TickRepository
)

from backend.models.feature_builder import (
    MLFeatureBuilder
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder
)

from backend.models.model_registry import (
    ModelRegistry
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "ML TREE ENSEMBLE AGENT TEST"
    )

    print(
        "=" * 70
    )


    # ==================================
    # FIXED HISTORICAL SNAPSHOT
    # ==================================
    #
    # Wednesday during an active
    # trading period already contained
    # in the validated historical DB.
    # ==================================

    anchor = datetime(
        2026,
        9,
        30,
        12,
        0,
        tzinfo=timezone.utc
    )


    as_of = (
        anchor
        + timedelta(
            minutes=5
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
                200
        )
    )


    tick_start = (

        as_of

        - timedelta(
            minutes=65
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
                "EURUSD"
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
    # HISTORICAL AGENT CONTEXT
    # ==================================

    replay_builder = (
        HistoricalReplayBuilder(

            symbol="EURUSD",

            timeframe="M5",

            timeframe_minutes=5,

            candle_limit=200,

            tick_lookback_minutes=65
        )
    )


    replay = (
        replay_builder.build(

            candles=
                candles,

            ticks=
                ticks,

            anchor_timestamp=
                anchor
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
    # FEATURE CONTRACT
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
    # MODEL ARTIFACT CONTRACT
    # ==================================

    registry = (
        ModelRegistry(

            "backend/models/registry/"
            "xgboost_eurusd_m5_v1"
        )
    )


    artifact = (
        registry.load()
    )


    model = (
        artifact[
            "model"
        ]
    )


    metadata = (
        artifact[
            "metadata"
        ]
    )


    model_feature_names = tuple(
        metadata[
            "features"
        ]
    )


    live_feature_names = tuple(
        feature_vector.feature_names
    )


    # Same schema is required.
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
    # EXPECTED MODEL INFERENCE
    # ==================================
    #
    # IMPORTANT:
    #
    # Build the prediction vector using
    # the exact training-time feature
    # order stored in metadata.
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


    expected_probabilities = (
        model.predict_proba(
            [
                ordered_values
            ]
        )[0]
    )


    expected_by_class = {

        int(
            class_id
        ):
            float(
                probability
            )

        for class_id, probability
        in zip(
            model.classes_,
            expected_probabilities
        )
    }


    # ==================================
    # REAL SEAT 11 EXECUTION
    # ==================================

    agent = (
        MLTreeEnsembleAgent()
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


    print(
        f"\nCold Seat 11 run: "
        f"{first_duration_ms:.2f} ms"
    )

    print(
        f"Warm Seat 11 run: "
        f"{second_duration_ms:.2f} ms"
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "SEAT 11 OPINION"
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
    # AGENT CONTRACT ASSERTIONS
    # ==================================

    assert (
        opinion_one.agent_id
        == "ml_tree_ensemble"
    )


    assert (
        opinion_one.agent_name
        == "ML XGBoost / Random Forest"
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


    # ==================================
    # MODEL ORDER ASSERTIONS
    # ==================================

    assert (
        abs(
            opinion_one.evidence[
                "short_probability"
            ]
            - expected_by_class[0]
        )
        < 0.000001
    )


    assert (
        abs(
            opinion_one.evidence[
                "neutral_probability"
            ]
            - expected_by_class[1]
        )
        < 0.000001
    )


    assert (
        abs(
            opinion_one.evidence[
                "long_probability"
            ]
            - expected_by_class[2]
        )
        < 0.000001
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


    assert (
        abs(
            probability_sum
            - 1.0
        )
        < 0.00001
    )


    # ==================================
    # ANTI-LOOKAHEAD ASSERTIONS
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
    # DETERMINISM ASSERTIONS
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
        "Probability contract: PASS"
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
        "ML TREE ENSEMBLE "
        "AGENT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()