from backend.agents.contracts import (
    AgentCategory,
    AgentSpec,
    AgentStage
)


COUNCIL_MANIFEST = (

    # ========================================================
    # TECHNICAL / MARKET BEHAVIOUR
    # ========================================================

    AgentSpec(
        seat_number=1,
        agent_id="trend_master",
        agent_name="Trend Master",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate persistent directional "
            "trend conditions."
        ),
        required_inputs=(
            "market_state",
            "candles"
        )
    ),

    AgentSpec(
        seat_number=2,
        agent_id="price_action",
        agent_name="Price Action",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate recent candle behaviour, "
            "rejection and directional pressure."
        ),
        required_inputs=(
            "candles",
        )
    ),

    AgentSpec(
        seat_number=3,
        agent_id="support_resistance",
        agent_name="Support / Resistance",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate price relative to meaningful "
            "support and resistance zones."
        ),
        required_inputs=(
            "candles",
        )
    ),

    AgentSpec(
        seat_number=4,
        agent_id="momentum",
        agent_name="Momentum",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate current directional force "
            "and momentum acceleration."
        ),
        required_inputs=(
            "market_state",
            "candles"
        )
    ),

    AgentSpec(
        seat_number=5,
        agent_id="volatility",
        agent_name="Volatility",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate the current volatility regime "
            "and volatility-adjusted opportunity."
        ),
        required_inputs=(
            "market_state",
            "candles"
        )
    ),

    AgentSpec(
        seat_number=6,
        agent_id="fibonacci",
        agent_name="Fibonacci",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate retracement and extension "
            "relationships around confirmed swings."
        ),
        required_inputs=(
            "candles",
            "market_structure"
        )
    ),

    AgentSpec(
        seat_number=7,
        agent_id="pattern_recognition",
        agent_name="Pattern Recognition",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Detect recurring candle and geometric "
            "price patterns."
        ),
        required_inputs=(
            "candles",
        )
    ),

    AgentSpec(
        seat_number=8,
        agent_id="market_structure",
        agent_name="Market Structure",
        category=AgentCategory.TECHNICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate confirmed swing structure "
            "and structural transitions."
        ),
        required_inputs=(
            "market_state",
            "candles"
        )
    ),

    # ========================================================
    # STATISTICAL / QUANTITATIVE
    # ========================================================

    AgentSpec(
        seat_number=9,
        agent_id="statistical_probability",
        agent_name="Statistical Probability",
        category=AgentCategory.STATISTICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Estimate direction from empirical "
            "conditional probabilities."
        ),
        required_inputs=(
            "candles",
            "market_state"
        )
    ),

    AgentSpec(
        seat_number=10,
        agent_id="monte_carlo",
        agent_name="Monte Carlo",
        category=AgentCategory.STATISTICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Estimate directional outcome "
            "distributions through simulation."
        ),
        required_inputs=(
            "candles",
            "market_state"
        )
    ),

    AgentSpec(
        seat_number=11,
        agent_id="ml_tree_ensemble",
        agent_name="ML XGBoost / Random Forest",
        category=AgentCategory.MACHINE_LEARNING,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Generate predictions from validated "
            "tree-based machine-learning models."
        ),
        required_inputs=(
            "feature_vector",
            "model_artifact"
        )
    ),

    AgentSpec(
        seat_number=12,
        agent_id="neural_network",
        agent_name="Neural Network",
        category=AgentCategory.MACHINE_LEARNING,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Generate nonlinear predictions from "
            "validated neural models."
        ),
        required_inputs=(
            "feature_vector",
            "model_artifact"
        )
    ),

    AgentSpec(
        seat_number=13,
        agent_id="hidden_markov_model",
        agent_name="Hidden Markov Model",
        category=AgentCategory.STATISTICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Estimate latent market regime and "
            "regime-transition probabilities."
        ),
        required_inputs=(
            "feature_vector",
            "model_artifact"
        )
    ),

    AgentSpec(
        seat_number=14,
        agent_id="mean_reversion",
        agent_name="Mean Reversion",
        category=AgentCategory.STATISTICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Estimate whether price displacement "
            "is statistically likely to revert."
        ),
        required_inputs=(
            "candles",
            "market_state"
        )
    ),

    AgentSpec(
        seat_number=15,
        agent_id="time_series",
        agent_name="Time Series",
        category=AgentCategory.STATISTICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate temporal dependence and "
            "short-horizon directional forecasts."
        ),
        required_inputs=(
            "candles",
        )
    ),

    AgentSpec(
        seat_number=16,
        agent_id="anomaly_detection",
        agent_name="Anomaly Detection",
        category=AgentCategory.STATISTICAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Detect abnormal market behaviour "
            "that may invalidate ordinary models."
        ),
        required_inputs=(
            "candles",
            "market_state"
        )
    ),

    # ========================================================
    # MICROSTRUCTURE / INSTITUTIONAL
    # ========================================================

    AgentSpec(
        seat_number=17,
        agent_id="liquidity_hunter",
        agent_name="Liquidity Hunter",
        category=AgentCategory.MICROSTRUCTURE,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate liquidity concentration, "
            "sweeps and likely liquidity targets."
        ),
        required_inputs=(
            "liquidity_state",
            "market_structure",
            "tick_features"
        )
    ),

    AgentSpec(
        seat_number=18,
        agent_id="smart_money",
        agent_name="Smart Money",
        category=AgentCategory.MICROSTRUCTURE,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate displacement, liquidity "
            "behaviour and institutional-style structure."
        ),
        required_inputs=(
            "candles",
            "market_structure",
            "tick_features"
        )
    ),

    AgentSpec(
        seat_number=19,
        agent_id="order_flow",
        agent_name="Order Flow",
        category=AgentCategory.MICROSTRUCTURE,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate tick-level directional "
            "pressure and microstructure imbalance."
        ),
        required_inputs=(
            "tick_features",
            "liquidity_state"
        )
    ),

    AgentSpec(
        seat_number=20,
        agent_id="london_session",
        agent_name="London Session",
        category=AgentCategory.SESSION,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate EUR/USD behaviour in the "
            "London trading session."
        ),
        required_inputs=(
            "session_state",
            "candles"
        )
    ),

    AgentSpec(
        seat_number=21,
        agent_id="new_york_session",
        agent_name="New York Session",
        category=AgentCategory.SESSION,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate EUR/USD behaviour in the "
            "New York trading session."
        ),
        required_inputs=(
            "session_state",
            "candles"
        )
    ),

    AgentSpec(
        seat_number=22,
        agent_id="cot_positioning",
        agent_name="COT / Institutional Positioning",
        category=AgentCategory.INSTITUTIONAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate institutional positioning "
            "from validated positioning data."
        ),
        required_inputs=(
            "cot_data",
            "market_state"
        )
    ),

    AgentSpec(
        seat_number=23,
        agent_id="dealer_behaviour",
        agent_name="Dealer Behaviour",
        category=AgentCategory.INSTITUTIONAL,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate dealer-style inventory and "
            "microstructure behaviour."
        ),
        required_inputs=(
            "tick_features",
            "market_state"
        )
    ),

    # ========================================================
    # MACROECONOMIC
    # ========================================================

    AgentSpec(
        seat_number=24,
        agent_id="ecb",
        agent_name="ECB",
        category=AgentCategory.MACRO,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate ECB policy and euro-side "
            "monetary conditions."
        ),
        required_inputs=(
            "ecb_data",
            "economic_calendar"
        )
    ),

    AgentSpec(
        seat_number=25,
        agent_id="fed",
        agent_name="Federal Reserve",
        category=AgentCategory.MACRO,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate Federal Reserve policy and "
            "US dollar monetary conditions."
        ),
        required_inputs=(
            "fed_data",
            "economic_calendar"
        )
    ),

    AgentSpec(
        seat_number=26,
        agent_id="rate_differential",
        agent_name="Rate Differential",
        category=AgentCategory.MACRO,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate relative euro and dollar "
            "interest-rate conditions."
        ),
        required_inputs=(
            "rates_data",
        )
    ),

    AgentSpec(
        seat_number=27,
        agent_id="economic_calendar",
        agent_name="Economic Calendar",
        category=AgentCategory.MACRO,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate scheduled macroeconomic "
            "event risk and surprise information."
        ),
        required_inputs=(
            "economic_calendar",
        )
    ),

    AgentSpec(
        seat_number=28,
        agent_id="risk_sentiment",
        agent_name="Risk Sentiment",
        category=AgentCategory.MACRO,
        stage=AgentStage.PRIMARY,
        purpose=(
            "Evaluate broader cross-asset "
            "risk-on and risk-off conditions."
        ),
        required_inputs=(
            "cross_asset_data",
            "market_state"
        )
    ),

    # ========================================================
    # GOVERNANCE
    # ========================================================

    AgentSpec(
        seat_number=29,
        agent_id="devils_advocate",
        agent_name="Devil's Advocate",
        category=AgentCategory.GOVERNANCE,
        stage=AgentStage.GOVERNANCE,
        purpose=(
            "Challenge dominant Council reasoning "
            "and search for contradictory evidence."
        ),
        required_inputs=(
            "primary_agent_opinions",
            "market_state"
        )
    ),

    AgentSpec(
        seat_number=30,
        agent_id="risk_officer",
        agent_name="Risk Officer",
        category=AgentCategory.GOVERNANCE,
        stage=AgentStage.GOVERNANCE,
        purpose=(
            "Evaluate whether current conditions "
            "justify taking market risk."
        ),
        required_inputs=(
            "primary_agent_opinions",
            "market_state",
            "risk_state"
        )
    ),

    # ========================================================
    # SYNTHESIS
    # ========================================================

    AgentSpec(
        seat_number=31,
        agent_id="chief_strategist",
        agent_name="Chief Strategist",
        category=AgentCategory.GOVERNANCE,
        stage=AgentStage.SYNTHESIS,
        purpose=(
            "Synthesize analytical and governance "
            "opinions into the final Council view."
        ),
        required_inputs=(
            "primary_agent_opinions",
            "governance_opinions",
            "market_state"
        )
    )
)
