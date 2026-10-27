"""Authoritative implementation catalogue for all 31 Council seats."""
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

from backend.agents.implementations.ml_tree_ensemble_agent import (
    MLTreeEnsembleAgent
)

from backend.agents.implementations.neural_network_agent import (
    NeuralNetworkAgent
)

from backend.agents.implementations.hidden_markov_model_agent import (
    HiddenMarkovModelAgent
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

from backend.agents.implementations.cot_positioning_agent import (
    COTPositioningAgent
)

from backend.agents.implementations.dealer_behaviour_agent import (
    DealerBehaviourAgent
)

from backend.agents.implementations.ecb_agent import (
    ECBAgent
)


from backend.agents.agent_registry import AgentRegistry
from backend.agents.implementations.fed_agent import FedAgent
from backend.agents.implementations.rate_differential_agent import RateDifferentialAgent
from backend.agents.implementations.economic_calendar_agent import EconomicCalendarAgent
from backend.agents.implementations.risk_sentiment_agent import RiskSentimentAgent
from backend.agents.implementations.devils_advocate_agent import DevilsAdvocateAgent
from backend.agents.implementations.risk_officer_agent import RiskOfficerAgent
from backend.agents.implementations.chief_strategist_agent import ChiefStrategistAgent


CORE_AGENT_FACTORIES = (
    TrendMasterAgent, PriceActionAgent, SupportResistanceAgent, MomentumAgent,
    VolatilityAgent, FibonacciAgent, PatternRecognitionAgent, MarketStructureAgent,
    StatisticalProbabilityAgent, MonteCarloAgent, MLTreeEnsembleAgent,
    NeuralNetworkAgent, HiddenMarkovModelAgent, MeanReversionAgent, TimeSeriesAgent,
    AnomalyDetectionAgent, LiquidityHunterAgent, SmartMoneyAgent, OrderFlowAgent,
    LondonSessionAgent, NewYorkSessionAgent, COTPositioningAgent,
    DealerBehaviourAgent, ECBAgent, FedAgent, RateDifferentialAgent,
    EconomicCalendarAgent, RiskSentimentAgent, DevilsAdvocateAgent,
    RiskOfficerAgent, ChiefStrategistAgent,
)


def register_core_agents(registry, *, agent_ids=None):
    """Register the full catalogue, or an explicit subset, atomically.

    Registration makes implementations available. CouncilRunner separately
    enforces mode, causal inputs and source admission before dispatch.
    """
    if not isinstance(registry, AgentRegistry):
        raise TypeError("Core registration requires AgentRegistry.")
    factories = {factory.agent_id: factory for factory in CORE_AGENT_FACTORIES}
    if len(factories) != AgentRegistry.EXPECTED_COUNCIL_SIZE:
        raise RuntimeError("Core catalogue must contain exactly 31 unique implementations.")
    requested = tuple(factories) if agent_ids is None else tuple(agent_ids)
    if len(requested) != len(set(requested)):
        raise ValueError("Registration request contains duplicate agent IDs.")
    if set(requested)-factories.keys():
        raise ValueError("Registration request contains unknown agent IDs.")
    for agent_id in requested:
        if registry.is_registered(agent_id):
            raise ValueError(f"Agent already registered: {agent_id}")
    selected = set(requested)
    registry.register_many(factory() for factory in CORE_AGENT_FACTORIES if factory.agent_id in selected)
    return registry
