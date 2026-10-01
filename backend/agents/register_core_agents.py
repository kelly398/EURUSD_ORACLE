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


    return registry