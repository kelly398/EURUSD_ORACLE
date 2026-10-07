from backend.agents.implementations.trend_master_agent import (
    TrendMasterAgent
)

from backend.agents.implementations.price_action_agent import (
    PriceActionAgent
)

from backend.agents.implementations.support_resistance_agent import (
    SupportResistanceAgent
)

from backend.agents.implementations.momentum_agent import (
    MomentumAgent
)

from backend.agents.implementations.volatility_agent import (
    VolatilityAgent
)

from backend.agents.implementations.fibonacci_agent import (
    FibonacciAgent
)

from backend.agents.implementations.pattern_recognition_agent import (
    PatternRecognitionAgent
)

from backend.agents.implementations.market_structure_agent import (
    MarketStructureAgent
)

from backend.agents.implementations.statistical_probability_agent import (
    StatisticalProbabilityAgent
)

from backend.agents.implementations.monte_carlo_agent import (
    MonteCarloAgent
)

from backend.agents.implementations.mean_reversion_agent import (
    MeanReversionAgent
)

from backend.agents.implementations.time_series_agent import (
    TimeSeriesAgent
)

from backend.agents.implementations.anomaly_detection_agent import (
    AnomalyDetectionAgent
)

from backend.agents.implementations.liquidity_hunter_agent import (
    LiquidityHunterAgent
)

from backend.agents.implementations.smart_money_agent import (
    SmartMoneyAgent
)

from backend.agents.implementations.order_flow_agent import (
    OrderFlowAgent
)

from backend.agents.implementations.london_session_agent import (
    LondonSessionAgent
)

from backend.agents.implementations.new_york_session_agent import (
    NewYorkSessionAgent
)

from backend.agents.implementations.ml_tree_ensemble_agent import (
    MLTreeEnsembleAgent
)

def register_core_agents(
    registry
):

    # Seats 1-8
    registry.register(
        TrendMasterAgent()
    )

    registry.register(
        PriceActionAgent()
    )

    registry.register(
        SupportResistanceAgent()
    )

    registry.register(
        MomentumAgent()
    )

    registry.register(
        VolatilityAgent()
    )

    registry.register(
        FibonacciAgent()
    )

    registry.register(
        PatternRecognitionAgent()
    )

    registry.register(
        MarketStructureAgent()
    )


    # Seat 9
    registry.register(
        StatisticalProbabilityAgent()
    )


    # Seat 10
    registry.register(
        MonteCarloAgent()
    )

    # Seat 11
    registry.register(
        MLTreeEnsembleAgent()
    )

    # Seat 14
    registry.register(
        MeanReversionAgent()
    )


    # Seat 15
    registry.register(
        TimeSeriesAgent()
    )


    # Seat 16
    registry.register(
        AnomalyDetectionAgent()
    )

    # Seat 17
    registry.register(
        LiquidityHunterAgent()
    )

    # Seat 18
    registry.register(
        SmartMoneyAgent()
    )

    # Seat 19
    registry.register(
        OrderFlowAgent()
    )
    # Seat 20
    registry.register(
        LondonSessionAgent()
    )
    # Seat 21
    registry.register(
        NewYorkSessionAgent()
    )
    

    return registry