from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.agents.dealer_behaviour_features import (
    DealerBehaviourFeatureEngine
)

from backend.market_state.schemas import (
    TickSample
)


def make_tick(
    *,
    timestamp,
    bid,
    ask,
    source_time_msc,
    symbol="EURUSD"
):

    spread_pips = round(
        (
            ask
            - bid
        )
        * 10000.0,
        10
    )


    return TickSample(

        symbol=
            symbol,

        timestamp=
            timestamp,

        source_time_msc=
            source_time_msc,

        bid=
            bid,

        ask=
            ask,

        spread_pips=
            spread_pips
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "DEALER BEHAVIOUR FEATURE TEST"
    )

    print(
        "=" * 70
    )


    as_of = datetime(
        2026,
        1,
        5,
        12,
        10,
        tzinfo=timezone.utc
    )


    # ==================================
    # 60-MINUTE BASELINE
    # ==================================

    baseline_start = (
        as_of
        - timedelta(
            minutes=35
        )
    )


    baseline = tuple(

        make_tick(

            timestamp=(
                baseline_start
                + timedelta(
                    minutes=index
                )
            ),

            bid=
                1.1000,

            ask=
                1.1002,

            source_time_msc=
                index
        )

        for index
        in range(
            30
        )
    )


    # ==================================
    # RECENT 5-MINUTE QUOTE SEQUENCE
    # ==================================
    #
    # Transitions:
    #
    # 1. bid only up
    # 2. ask only up
    # 3. both up
    # 4. both down
    # 5. bid up / ask down
    #
    # Classification:
    #
    # one-sided    = 2
    # synchronized = 2
    # opposed      = 1
    # ==================================

    recent_start = (
        as_of
        - timedelta(
            minutes=5
        )
    )


    recent = (

        make_tick(
            timestamp=
                recent_start,
            bid=1.1000,
            ask=1.1002,
            source_time_msc=100
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=30
                )
            ),
            bid=1.1001,
            ask=1.1002,
            source_time_msc=101
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=60
                )
            ),
            bid=1.1001,
            ask=1.1003,
            source_time_msc=102
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=90
                )
            ),
            bid=1.1002,
            ask=1.1004,
            source_time_msc=103
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=120
                )
            ),
            bid=1.1001,
            ask=1.1003,
            source_time_msc=104
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=150
                )
            ),
            bid=1.1002,
            ask=1.1002,
            source_time_msc=105
        )
    )


    ticks = (
        baseline
        + recent
    )


    engine = (
        DealerBehaviourFeatureEngine()
    )


    features = (
        engine.analyze(
            ticks=ticks,
            as_of=as_of
        )
    )


    # ==================================
    # BASE CONTRACT
    # ==================================

    assert (
        features.as_of
        == as_of
    )

    assert (
        features.recent_tick_count
        == 6
    )

    assert (
        features.baseline_tick_count
        == 30
    )


    print(
        "\nWindow contract: PASS"
    )


    # ==================================
    # BID SIDE
    # ==================================

    assert (
        features.bid_up_updates
        == 3
    )

    assert (
        features.bid_down_updates
        == 1
    )

    assert (
        features.bid_directional_updates
        == 4
    )

    assert abs(
        features.bid_update_imbalance
        - 0.5
    ) < 1e-12


    print(
        "Bid-side update contract: PASS"
    )


    # ==================================
    # ASK SIDE
    # ==================================

    assert (
        features.ask_up_updates
        == 2
    )

    assert (
        features.ask_down_updates
        == 2
    )

    assert (
        features.ask_directional_updates
        == 4
    )

    assert abs(
        features.ask_update_imbalance
        - 0.0
    ) < 1e-12


    print(
        "Ask-side update contract: PASS"
    )


    assert abs(
        features.side_update_asymmetry
        - 0.0
    ) < 1e-12


    print(
        "Side-update asymmetry: PASS"
    )


    # ==================================
    # QUOTE CHANGE STRUCTURE
    # ==================================

    assert (
        features.quote_change_updates
        == 5
    )

    assert (
        features.one_sided_updates
        == 2
    )

    assert (
        features.synchronized_updates
        == 2
    )

    assert (
        features.opposed_updates
        == 1
    )


    assert abs(
        features.one_sided_update_ratio
        - 0.4
    ) < 1e-12

    assert abs(
        features.synchronized_update_ratio
        - 0.4
    ) < 1e-12

    assert abs(
        features.opposed_update_ratio
        - 0.2
    ) < 1e-12


    assert abs(
        (
            features
            .one_sided_update_ratio

            + features
            .synchronized_update_ratio

            + features
            .opposed_update_ratio
        )
        - 1.0
    ) < 1e-12


    print(
        "Quote-change decomposition: PASS"
    )


    # ==================================
    # SPREAD MANAGEMENT
    # ==================================

    assert (
        features.spread_widen_updates
        == 1
    )

    assert (
        features.spread_narrow_updates
        == 2
    )

    assert (
        features.spread_directional_updates
        == 3
    )


    assert abs(
        features.spread_update_imbalance
        - (
            -1.0
            / 3.0
        )
    ) < 1e-12


    assert abs(
        features.net_spread_change_pips
        - (
            -2.0
        )
    ) < 1e-9


    assert abs(
        features.mean_absolute_spread_change_pips
        - 0.8
    ) < 1e-9


    print(
        "Spread-management contract: PASS"
    )


    # ==================================
    # EXISTING BASELINE FEATURE PARITY
    # ==================================

    assert abs(
        features.tick_activity_ratio
        - 2.4
    ) < 1e-12


    assert abs(
        features.average_spread_pips
        - 1.5
    ) < 1e-9


    assert abs(
        features.baseline_spread_pips
        - 2.0
    ) < 1e-9


    assert abs(
        features.spread_ratio
        - 0.75
    ) < 1e-9


    print(
        "Existing microstructure parity: PASS"
    )


    # ==================================
    # INPUT ORDER MUST NOT MATTER
    # ==================================

    reversed_features = (
        engine.analyze(

            ticks=tuple(
                reversed(
                    ticks
                )
            ),

            as_of=as_of
        )
    )


    assert (
        reversed_features
        == features
    )


    print(
        "Deterministic tick ordering: PASS"
    )


    # ==================================
    # FUTURE-TICK GUARD
    # ==================================

    future_tick = (
        make_tick(

            timestamp=
                as_of,

            bid=1.1002,
            ask=1.1004,
            source_time_msc=999
        )
    )


    try:

        engine.analyze(

            ticks=(
                ticks
                + (
                    future_tick,
                )
            ),

            as_of=
                as_of
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Future tick was accepted."
        )


    print(
        "Future-tick causal guard: PASS"
    )


    # ==================================
    # MIXED-SYMBOL GUARD
    # ==================================

    mixed_tick = (
        make_tick(

            timestamp=(
                as_of
                - timedelta(
                    seconds=15
                )
            ),

            bid=1.2500,
            ask=1.2502,
            source_time_msc=998,
            symbol="GBPUSD"
        )
    )


    try:

        engine.analyze(

            ticks=(
                ticks
                + (
                    mixed_tick,
                )
            ),

            as_of=
                as_of
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Mixed-symbol ticks were accepted."
        )


    print(
        "Single-symbol guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "DEALER BEHAVIOUR FEATURE TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()