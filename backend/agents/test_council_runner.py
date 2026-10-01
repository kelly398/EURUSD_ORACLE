from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.agents.agent_registry import (
    AgentRegistry
)

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentContext,
    AgentDirection
)

from backend.agents.council_runner import (
    AgentRunStatus,
    CouncilRunner
)

from backend.market_state.schemas import (
    CandleBar,
    DataQualityStatus,
    MarketStateSnapshot
)


# ============================================================
# TEST AGENTS
# ============================================================

class TestTrendMaster(
    BaseAgent
):

    agent_id = "trend_master"

    agent_name = "Trend Master"

    category = (
        AgentCategory.TECHNICAL
    )


    def analyze(
        self,
        context
    ):

        return self.build_opinion(

            context=context,

            score=0.65,

            confidence=0.85,

            rationale=(
                "Synthetic bullish "
                "trend opinion."
            ),

            evidence={
                "test": True
            }
        )


class TestPriceAction(
    BaseAgent
):

    agent_id = "price_action"

    agent_name = "Price Action"

    category = (
        AgentCategory.TECHNICAL
    )


    def analyze(
        self,
        context
    ):

        return self.build_opinion(

            context=context,

            score=-0.40,

            confidence=0.70,

            rationale=(
                "Synthetic bearish "
                "price-action opinion."
            ),

            evidence={
                "test": True
            }
        )


class TestSupportResistance(
    BaseAgent
):

    agent_id = "support_resistance"

    agent_name = (
        "Support / Resistance"
    )

    category = (
        AgentCategory.TECHNICAL
    )


    def analyze(
        self,
        context
    ):

        # Deliberate failure.
        #
        # The purpose is to prove that
        # one broken Council member does
        # not terminate the entire run.
        raise RuntimeError(
            "Intentional test failure."
        )


class TestMomentum(
    BaseAgent
):

    agent_id = "momentum"

    agent_name = "Momentum"

    category = (
        AgentCategory.TECHNICAL
    )


    def analyze(
        self,
        context
    ):

        return self.build_opinion(

            context=context,

            score=0.05,

            confidence=0.60,

            rationale=(
                "Synthetic neutral "
                "momentum opinion."
            ),

            evidence={
                "test": True
            }
        )


# ============================================================
# SYNTHETIC CONTEXT
# ============================================================

def create_context():

    start = datetime(
        2026,
        1,
        1,
        0,
        0,
        tzinfo=timezone.utc
    )


    candles = []

    previous_close = (
        1.10000
    )


    for index in range(80):

        open_price = (
            previous_close
        )


        close_price = (

            open_price

            + 0.00001
        )


        high_price = (

            max(
                open_price,
                close_price
            )

            + 0.00002
        )


        low_price = (

            min(
                open_price,
                close_price
            )

            - 0.00002
        )


        candle = CandleBar(

            symbol="EURUSD",

            timeframe="M5",

            timestamp=(

                start

                + timedelta(
                    minutes=
                        5 * index
                )
            ),

            open=
                open_price,

            high=
                high_price,

            low=
                low_price,

            close=
                close_price,

            tick_volume=100,

            spread_points=15,

            real_volume=0.0,

            source="MT5"
        )


        candles.append(
            candle
        )


        previous_close = (
            close_price
        )


    as_of = (

        candles[-1].timestamp

        + timedelta(
            minutes=5
        )
    )


    snapshot = (
        MarketStateSnapshot(

            symbol="EURUSD",

            timeframe="M5",

            as_of=as_of,

            data_quality=
                DataQualityStatus.PASS,

            candle_count=
                len(candles)
        )
    )


    return AgentContext(

        symbol="EURUSD",

        timeframe="M5",

        as_of=as_of,

        market_state=
            snapshot,

        candles=
            tuple(candles)
    )


# ============================================================
# TEST
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COUNCIL RUNNER TEST"
    )

    print(
        "=" * 70
    )


    context = (
        create_context()
    )


    registry = (
        AgentRegistry()
    )


    # Register deliberately out of
    # Council-seat order.
    #
    # The runner must restore
    # deterministic seat ordering.
    registry.register(
        TestMomentum()
    )

    registry.register(
        TestTrendMaster()
    )

    registry.register(
        TestSupportResistance()
    )

    registry.register(
        TestPriceAction()
    )


    runner = (
        CouncilRunner(

            registry=registry,

            max_workers=4
        )
    )


    result = (
        runner.run_primary(

            context=context,

            require_all_registered=False
        )
    )


    print(
        f"\nAs-of: "
        f"{result.as_of}"
    )


    print(
        f"Successful: "
        f"{result.succeeded_count}"
    )


    print(
        f"Failed: "
        f"{result.failed_count}"
    )


    print(
        f"Missing primary agents: "
        f"{len(result.missing_agent_ids)}"
    )


    print(
        f"Council complete: "
        f"{result.is_complete}"
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "EXECUTION RESULTS"
    )

    print(
        "-" * 70
    )


    for execution in (
        result.executions
    ):

        print(
            f"{execution.seat_number:02d} | "
            f"{execution.agent_name:<22} | "
            f"{execution.status.value:<7} | "
            f"{execution.duration_ms:.3f} ms"
        )


        if (
            execution.opinion
            is not None
        ):

            print(
                "     "
                f"direction="
                f"{execution.opinion.direction.value}, "
                f"score="
                f"{execution.opinion.score:.4f}, "
                f"confidence="
                f"{execution.opinion.confidence:.4f}"
            )


        if (
            execution.error
            is not None
        ):

            print(
                "     "
                f"error="
                f"{execution.error}"
            )


    # ==================================
    # DETERMINISTIC ORDER
    # ==================================

    seat_order = [

        execution.seat_number

        for execution
        in result.executions
    ]


    assert (
        seat_order
        == [
            1,
            2,
            3,
            4
        ]
    )


    # ==================================
    # SUCCESS / FAILURE ISOLATION
    # ==================================

    assert (
        result.succeeded_count
        == 3
    )


    assert (
        result.failed_count
        == 1
    )


    assert (
        len(
            result.missing_agent_ids
        )
        == 24
    )


    assert (
        result.is_complete
        is False
    )


    # ==================================
    # OPINION DIRECTIONS
    # ==================================

    opinions_by_id = {

        opinion.agent_id:
            opinion

        for opinion
        in result.opinions
    }


    assert (
        opinions_by_id[
            "trend_master"
        ].direction
        == AgentDirection.LONG
    )


    assert (
        opinions_by_id[
            "price_action"
        ].direction
        == AgentDirection.SHORT
    )


    assert (
        opinions_by_id[
            "momentum"
        ].direction
        == AgentDirection.NEUTRAL
    )


    # ==================================
    # FAILED AGENT CAPTURED
    # ==================================

    failure = (
        result.failed_executions[0]
    )


    assert (
        failure.agent_id
        == "support_resistance"
    )


    assert (
        failure.status
        == AgentRunStatus.ERROR
    )


    assert (
        "Intentional test failure"
        in failure.error
    )


    # ==================================
    # STRICT COMPLETENESS GATE
    # ==================================

    strict_gate_triggered = False


    try:

        runner.run_primary(

            context=context,

            require_all_registered=True
        )


    except RuntimeError:

        strict_gate_triggered = (
            True
        )


    assert (
        strict_gate_triggered
        is True
    )


    print(
        "\nStrict completeness gate: PASS"
    )


    # ==================================
    # TIMESTAMP SYNCHRONIZATION
    # ==================================

    for opinion in (
        result.opinions
    ):

        assert (
            opinion.as_of
            == context.as_of
        )


    print(
        "Timestamp synchronization: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "COUNCIL RUNNER "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    