from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentContext,
    AgentDirection
)

from backend.market_state.market_state_engine import (
    MarketStateEngine
)

from backend.market_state.candle_repository import (
    CandleRepository
)


class FrameworkTestAgent(
    BaseAgent
):

    agent_id = "framework_test"

    agent_name = (
        "Framework Test Agent"
    )

    category = (
        AgentCategory.GOVERNANCE
    )

    version = "1.0.0"


    def __init__(
        self,
        test_score
    ):

        self.test_score = (
            test_score
        )

        super().__init__()


    def analyze(
        self,
        context
    ):

        return self.build_opinion(

            context=context,

            score=
                self.test_score,

            confidence=0.80,

            rationale=(
                "Deterministic framework "
                "validation opinion."
            ),

            evidence={
                "test_score":
                    self.test_score,

                "candle_count":
                    len(
                        context.candles
                    )
            }
        )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "BASE AGENT FRAMEWORK TEST"
    )

    print(
        "=" * 70
    )


    # ==================================
    # BUILD SYNCHRONIZED MARKET STATE
    # ==================================

    market_engine = (
        MarketStateEngine(

            symbol="EURUSD",

            timeframe="M5",

            timeframe_minutes=5,

            candle_limit=200
        )
    )


    unified_result = (
        market_engine.build_snapshot()
    )


    snapshot = (
        unified_result.snapshot
    )


    anchor = (
        unified_result
        .anchor_candle_timestamp
    )


    # ==================================
    # LOAD EXACT SAME CANDLE HISTORY
    # ==================================

    repository = (
        CandleRepository(
            source="MT5"
        )
    )


    candles = (
        repository.get_latest_as_of(

            latest_timestamp=
                anchor,

            symbol="EURUSD",

            timeframe="M5",

            limit=200
        )
    )


    # ==================================
    # BUILD STANDARD AGENT CONTEXT
    # ==================================

    context = AgentContext(

        symbol=
            snapshot.symbol,

        timeframe=
            snapshot.timeframe,

        as_of=
            snapshot.as_of,

        market_state=
            snapshot,

        candles=
            tuple(candles)
    )


    print(
        f"\nContext symbol: "
        f"{context.symbol}"
    )

    print(
        f"Context timeframe: "
        f"{context.timeframe}"
    )

    print(
        f"Context as-of: "
        f"{context.as_of}"
    )

    print(
        f"Candles: "
        f"{len(context.candles)}"
    )


    # ==================================
    # LONG OPINION TEST
    # ==================================

    long_agent = (
        FrameworkTestAgent(
            test_score=0.65
        )
    )


    long_opinion = (
        long_agent.run(
            context
        )
    )


    print(
        "\nLONG TEST"
    )

    print(
        "-" * 70
    )

    print(
        f"Direction: "
        f"{long_opinion.direction.value}"
    )

    print(
        f"Score: "
        f"{long_opinion.score:.4f}"
    )

    print(
        f"Confidence: "
        f"{long_opinion.confidence:.4f}"
    )


    assert (
        long_opinion.direction
        == AgentDirection.LONG
    )


    # ==================================
    # SHORT OPINION TEST
    # ==================================

    short_agent = (
        FrameworkTestAgent(
            test_score=-0.55
        )
    )


    short_opinion = (
        short_agent.run(
            context
        )
    )


    print(
        "\nSHORT TEST"
    )

    print(
        "-" * 70
    )

    print(
        f"Direction: "
        f"{short_opinion.direction.value}"
    )

    print(
        f"Score: "
        f"{short_opinion.score:.4f}"
    )

    print(
        f"Confidence: "
        f"{short_opinion.confidence:.4f}"
    )


    assert (
        short_opinion.direction
        == AgentDirection.SHORT
    )


    # ==================================
    # NEUTRAL OPINION TEST
    # ==================================

    neutral_agent = (
        FrameworkTestAgent(
            test_score=0.05
        )
    )


    neutral_opinion = (
        neutral_agent.run(
            context
        )
    )


    print(
        "\nNEUTRAL TEST"
    )

    print(
        "-" * 70
    )

    print(
        f"Direction: "
        f"{neutral_opinion.direction.value}"
    )

    print(
        f"Score: "
        f"{neutral_opinion.score:.4f}"
    )

    print(
        f"Confidence: "
        f"{neutral_opinion.confidence:.4f}"
    )


    assert (
        neutral_opinion.direction
        == AgentDirection.NEUTRAL
    )


    # ==================================
    # CONTRACT ASSERTIONS
    # ==================================

    assert (
        long_opinion.as_of
        == snapshot.as_of
    )


    assert (
        short_opinion.as_of
        == snapshot.as_of
    )


    assert (
        neutral_opinion.as_of
        == snapshot.as_of
    )


    assert (
        long_opinion.agent_id
        == "framework_test"
    )


    assert (
        long_opinion.category
        == AgentCategory.GOVERNANCE
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "BASE AGENT FRAMEWORK "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    