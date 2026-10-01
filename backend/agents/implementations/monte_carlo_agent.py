import numpy as np

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class MonteCarloAgent(BaseAgent):

    agent_id = "monte_carlo"

    agent_name = "Monte Carlo"

    category = AgentCategory.STATISTICAL

    version = "1.0.0"


    def __init__(
        self,
        lookback=120,
        horizon_bars=3,
        simulations=5000
    ):

        self.lookback = lookback

        self.horizon_bars = (
            horizon_bars
        )

        self.simulations = (
            simulations
        )

        super().__init__()


    def analyze(
        self,
        context
    ):

        candles = list(
            context.candles
        )


        if (
            len(candles)
            < self.lookback + 1
        ):

            raise RuntimeError(
                "Insufficient candles for "
                "Monte Carlo agent."
            )


        recent = (
            candles[
                -(
                    self.lookback
                    + 1
                ):
            ]
        )


        closes = np.array(

            [
                candle.close
                for candle in recent
            ],

            dtype=float
        )


        returns_pips = (

            np.diff(closes)
            * 10000.0
        )


        if len(returns_pips) == 0:

            raise RuntimeError(
                "No returns available for "
                "Monte Carlo simulation."
            )


        # Deterministic seed means the
        # exact same market snapshot gives
        # the exact same Council opinion.
        seed = (

            int(
                context.as_of.timestamp()
                * 1000
            )

            + 10
        ) % (2 ** 32)


        rng = np.random.default_rng(
            seed
        )


        sampled_returns = rng.choice(

            returns_pips,

            size=(
                self.simulations,
                self.horizon_bars
            ),

            replace=True
        )


        terminal_moves = (
            sampled_returns.sum(
                axis=1
            )
        )


        liquidity = (
            context.market_state.liquidity
        )


        if (
            liquidity is not None

            and

            liquidity.average_spread_pips
            is not None
        ):

            cost_barrier = max(
                0.0,
                float(
                    liquidity.average_spread_pips
                )
            )

        else:

            cost_barrier = 0.0


        probability_up = float(

            np.mean(
                terminal_moves
                > cost_barrier
            )
        )


        probability_down = float(

            np.mean(
                terminal_moves
                < -cost_barrier
            )
        )


        probability_neutral = max(

            0.0,

            1.0
            - probability_up
            - probability_down
        )


        score = (

            probability_up
            - probability_down
        )


        sample_factor = min(
            1.0,
            len(returns_pips) / 100.0
        )


        confidence = (

            min(
                1.0,
                abs(score) / 0.35
            )

            * sample_factor
        )


        expected_move_pips = float(
            np.mean(
                terminal_moves
            )
        )


        return self.build_opinion(

            context=context,

            score=float(score),

            confidence=float(
                confidence
            ),

            rationale=(
                f"{self.simulations} empirical "
                "bootstrap paths over "
                f"{self.horizon_bars} M5 bars "
                "were simulated."
            ),

            evidence={
                "probability_up":
                    probability_up,

                "probability_down":
                    probability_down,

                "probability_neutral":
                    probability_neutral,

                "expected_move_pips":
                    expected_move_pips,

                "cost_barrier_pips":
                    cost_barrier,

                "simulation_count":
                    self.simulations,

                "horizon_bars":
                    self.horizon_bars,

                "seed":
                    seed
            }
        )
        