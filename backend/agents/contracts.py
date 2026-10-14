from datetime import datetime
from enum import Enum
from typing import Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator
)

from backend.market_state.schemas import (
    CandleBar,
    DataQualityStatus,
    MarketStateSnapshot,
    TickSample
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)

# ============================================================
# EVIDENCE VALUE TYPE
# ============================================================

EvidenceValue = Union[
    str,
    int,
    float,
    bool,
    None
]


# ============================================================
# AGENT CATEGORY
# ============================================================

class AgentCategory(str, Enum):

    TECHNICAL = "technical"

    STATISTICAL = "statistical"

    MACHINE_LEARNING = "machine_learning"

    MICROSTRUCTURE = "microstructure"

    SESSION = "session"

    INSTITUTIONAL = "institutional"

    MACRO = "macro"

    GOVERNANCE = "governance"


# ============================================================
# AGENT EXECUTION STAGE
# ============================================================

class AgentStage(str, Enum):

    PRIMARY = "primary"

    GOVERNANCE = "governance"

    SYNTHESIS = "synthesis"


# ============================================================
# COUNCIL SEAT SPECIFICATION
# ============================================================

class AgentSpec(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    seat_number: int = Field(
        ge=1,
        le=31
    )

    agent_id: str = Field(
        min_length=1
    )

    agent_name: str = Field(
        min_length=1
    )

    category: AgentCategory

    stage: AgentStage

    purpose: str = Field(
        min_length=1
    )

    required_inputs: tuple[
        str,
        ...
    ] = tuple()

    voting_member: bool = True


# ============================================================
# AGENT DIRECTION
# ============================================================

class AgentDirection(str, Enum):

    LONG = "long"

    SHORT = "short"

    NEUTRAL = "neutral"


# ============================================================
# AGENT SIGNAL TYPE
# ============================================================

class AgentSignalType(str, Enum):

    DIRECTIONAL = "directional"

    CONTEXTUAL = "contextual"

    GOVERNANCE = "governance"


# ============================================================
# STANDARD AGENT CONTEXT
# ============================================================

class AgentContext(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    symbol: str

    timeframe: str

    as_of: datetime

    market_state: MarketStateSnapshot

    candles: tuple[
        CandleBar,
        ...
    ]

    ticks: tuple[
        TickSample,
        ...
    ] = tuple()

    cot_data: tuple[
        COTPositioningSnapshot,
        ...
    ] = tuple()

    ecb_data: tuple[
        CentralBankRateSnapshot,
        ...
    ] = tuple()

    fed_data: tuple[
        CentralBankRateSnapshot,
        ...
    ] = tuple()

    economic_calendar: tuple[
        EconomicCalendarEventSnapshot,
        ...
    ] = tuple()

    @model_validator(
        mode="after"
    )
    def validate_context(self):

        # ==================================
        # SYMBOL CONSISTENCY
        # ==================================

        if (
            self.market_state.symbol
            != self.symbol
        ):

            raise ValueError(
                "AgentContext symbol "
                "does not match "
                "MarketStateSnapshot."
            )


        # ==================================
        # TIMEFRAME CONSISTENCY
        # ==================================

        if (
            self.market_state.timeframe
            != self.timeframe
        ):

            raise ValueError(
                "AgentContext timeframe "
                "does not match "
                "MarketStateSnapshot."
            )


        # ==================================
        # TIMESTAMP CONSISTENCY
        # ==================================

        if (
            self.market_state.as_of
            != self.as_of
        ):

            raise ValueError(
                "AgentContext as_of "
                "does not match "
                "MarketStateSnapshot."
            )


        # ==================================
        # DATA QUALITY GATE
        # ==================================

        if (
            self.market_state.data_quality
            != DataQualityStatus.PASS
        ):

            raise ValueError(
                "Agents may only receive "
                "PASS market-state data."
            )


        # ==================================
        # CANDLE AVAILABILITY
        # ==================================

        if not self.candles:

            raise ValueError(
                "AgentContext requires "
                "canonical candle history."
            )


        # ==================================
        # CANDLE IDENTITY CHECKS
        # ==================================

        for candle in self.candles:

            if (
                candle.symbol
                != self.symbol
            ):

                raise ValueError(
                    "AgentContext contains "
                    "mixed candle symbols."
                )


            if (
                candle.timeframe
                != self.timeframe
            ):

                raise ValueError(
                    "AgentContext contains "
                    "mixed candle timeframes."
                )


        # ==================================
        # STRICT CHRONOLOGICAL ORDER
        # ==================================

        for previous, current in zip(
            self.candles,
            self.candles[1:]
        ):

            if (
                current.timestamp
                <= previous.timestamp
            ):

                raise ValueError(
                    "AgentContext candles "
                    "must be in strict "
                    "chronological order."
                )


        # ==================================
        # ANTI LOOK-AHEAD CHECK
        # ==================================

        if (
            self.candles[-1].timestamp
            >= self.as_of
        ):

            raise ValueError(
                "AgentContext contains "
                "a candle at or beyond "
                "the analysis timestamp. "
                "Possible look-ahead leakage."
            )

                # ==================================
        # OPTIONAL RAW TICK VALIDATION
        # ==================================

        for tick in self.ticks:

            if (
                tick.symbol
                != self.symbol
            ):

                raise ValueError(
                    "AgentContext contains "
                    "mixed tick symbols."
                )


            if (
                tick.timestamp
                >= self.as_of
            ):

                raise ValueError(
                    "AgentContext contains "
                    "a tick at or beyond "
                    "the analysis timestamp. "
                    "Possible look-ahead leakage."
                )


        # Equal timestamps are legitimate
        # because MT5 can publish multiple
        # quote changes within the same
        # millisecond.
        for previous, current in zip(
            self.ticks,
            self.ticks[1:]
        ):

            if (
                current.timestamp
                < previous.timestamp
            ):

                raise ValueError(
                    "AgentContext ticks must "
                    "be in chronological order."
                )

            if (
                current.timestamp
                == previous.timestamp
                and
                current.source_time_msc
                < previous.source_time_msc
            ):

                raise ValueError(
                    "AgentContext ticks have "
                    "invalid source-time order."
                )

        # ==================================
        # OPTIONAL COT POSITIONING HISTORY
        # ==================================
        #
        # COT information is governed by
        # `available_at`, not merely the
        # underlying report_date.
        #
        # No report or later correction may
        # leak into a context before it was
        # actually available.
        # ==================================

        for item in self.cot_data:

            if (
                item.available_at
                > self.as_of
            ):

                raise ValueError(
                    "AgentContext contains COT "
                    "positioning that was not yet "
                    "available at the analysis "
                    "timestamp. Possible "
                    "look-ahead leakage."
                )

        # Exactly one causal version may appear
        # for each report_date, and the history
        # must be strictly chronological by
        # report_date.
        for previous, current in zip(
            self.cot_data,
            self.cot_data[1:]
        ):

            if (
                current.report_date
                <= previous.report_date
            ):

                raise ValueError(
                    "AgentContext COT history "
                    "must be in strict "
                    "report-date order with "
                    "one version per report."
                )

        # A single context represents one
        # coherent positioning series.
        if self.cot_data:

            first_report_type = (
                self.cot_data[0]
                .report_type
            )

            first_contract_code = (
                self.cot_data[0]
                .cftc_contract_market_code
            )

            for item in self.cot_data[1:]:

                if (
                    item.report_type
                    != first_report_type
                ):

                    raise ValueError(
                        "AgentContext contains "
                        "mixed COT report types."
                    )

                if (
                    item
                    .cftc_contract_market_code
                    != first_contract_code
                ):

                    raise ValueError(
                        "AgentContext contains "
                        "mixed COT contract "
                        "market codes."
                    )

        # ==================================
        # ECB MACRO DATA
        # ==================================

        ecb_source_ids = set()

        previous_ecb_key = None


        for item in self.ecb_data:

            if (
                item.available_at
                > self.as_of
            ):

                raise ValueError(
                    "AgentContext contains ECB "
                    "data that was not yet "
                    "available at the analysis "
                    "timestamp. Possible "
                    "look-ahead leakage."
                )


            if (
                item.central_bank
                != "ECB"
            ):

                raise ValueError(
                    "AgentContext ecb_data "
                    "contains a non-ECB "
                    "central-bank snapshot."
                )


            if (
                item.source_record_id
                in ecb_source_ids
            ):

                raise ValueError(
                    "AgentContext ecb_data "
                    "contains multiple causal "
                    "versions of the same "
                    "source record."
                )


            ecb_source_ids.add(
                item.source_record_id
            )


            current_ecb_key = (

                item.reference_date,

                item.rate_name,

                item.source_record_id
            )


            if (
                previous_ecb_key
                is not None

                and

                current_ecb_key
                <= previous_ecb_key
            ):

                raise ValueError(
                    "AgentContext ecb_data "
                    "must be in strict "
                    "reference-date, rate-name, "
                    "source-record order."
                )


            previous_ecb_key = (
                current_ecb_key
            )


        # ==================================
        # FED POLICY-RATE CONTEXT
        # ==================================
        # Structural and temporal validation.
        # This does not certify historical
        # source-version availability.
        # ==================================

        seen_fed_ids = set()
        previous_fed_key = None
        fed_decisions = {}

        for item in self.fed_data:

            if not isinstance(item, CentralBankRateSnapshot):
                raise ValueError(
                    "AgentContext fed_data requires rate snapshots."
                )

            if (
                item.central_bank != "FED"
                or item.source != "FED_FOMC_RELEASE"
            ):
                raise ValueError(
                    "AgentContext fed_data requires official Fed source identity."
                )

            if (
                item.available_at > self.as_of
                or item.ingested_at > self.as_of
            ):
                raise ValueError(
                    "AgentContext contains Fed evidence observed after as_of."
                )

            if item.ingested_at < item.available_at:
                raise ValueError(
                    "AgentContext Fed ingestion predates reported availability."
                )

            bound_by_name = {
                "federal_funds_target_lower": "lower",
                "federal_funds_target_upper": "upper",
            }

            bound = bound_by_name.get(item.rate_name)

            if bound is None:
                raise ValueError(
                    "AgentContext fed_data contains unsupported rate name."
                )

            suffix = ":" + bound
            record_id = item.source_record_id

            if (
                not record_id.startswith("FOMC:monetary")
                or not record_id.endswith(suffix)
                or len(record_id)
                <= len("FOMC:monetary") + len(suffix)
            ):
                raise ValueError(
                    "AgentContext Fed record identity does not match its bound."
                )

            if record_id in seen_fed_ids:
                raise ValueError(
                    "AgentContext contains duplicate Fed source record."
                )

            seen_fed_ids.add(record_id)

            key = (
                item.reference_date,
                item.rate_name,
                record_id,
            )

            if (
                previous_fed_key is not None
                and key <= previous_fed_key
            ):
                raise ValueError(
                    "AgentContext Fed history must be strictly ordered."
                )

            previous_fed_key = key

            statement_id = record_id[:-len(suffix)]
            decision = fed_decisions.setdefault(
                statement_id, {}
            )

            if bound in decision:
                raise ValueError(
                    "AgentContext contains duplicate Fed rate bound."
                )

            decision[bound] = item

        for pair in fed_decisions.values():

            if set(pair) != {"lower", "upper"}:
                raise ValueError(
                    "AgentContext requires complete Fed lower/upper pairs."
                )

            lower = pair["lower"]
            upper = pair["upper"]

            if (
                lower.reference_date != upper.reference_date
                or lower.available_at != upper.available_at
                or lower.ingested_at != upper.ingested_at
            ):
                raise ValueError(
                    "AgentContext Fed decision pair has inconsistent provenance."
                )

            if (
                lower.value_percent > upper.value_percent
                or lower.content_hash == upper.content_hash
            ):
                raise ValueError(
                    "AgentContext Fed decision pair is inconsistent."
                )

        # ==================================
        # ECONOMIC CALENDAR
        # ==================================
        #
        # scheduled_at may legitimately be
        # later than as_of. Future scheduled
        # events are useful information if the
        # exact version was already public.
        # ==================================

        calendar_event_ids = set()

        previous_calendar_key = None


        for item in self.economic_calendar:

            if (
                item.available_at
                > self.as_of
            ):

                raise ValueError(
                    "AgentContext contains an "
                    "economic-calendar version "
                    "that was not yet available "
                    "at the analysis timestamp. "
                    "Possible look-ahead leakage."
                )


            if (
                item.source_event_id
                in calendar_event_ids
            ):

                raise ValueError(
                    "AgentContext economic "
                    "calendar contains multiple "
                    "causal versions of the "
                    "same event."
                )


            calendar_event_ids.add(
                item.source_event_id
            )


            current_calendar_key = (

                item.scheduled_at,

                item.source_event_id
            )


            if (
                previous_calendar_key
                is not None

                and

                current_calendar_key
                <= previous_calendar_key
            ):

                raise ValueError(
                    "AgentContext economic "
                    "calendar must be in strict "
                    "scheduled-time and "
                    "source-event order."
                )


            previous_calendar_key = (
                current_calendar_key
            )


        return self


# ============================================================
# STANDARDIZED AGENT OPINION
# ============================================================

class AgentOpinion(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    agent_id: str

    agent_name: str

    category: AgentCategory

    signal_type: AgentSignalType

    version: str

    as_of: datetime

    direction: AgentDirection

    score: float = Field(
        ge=-1.0,
        le=1.0
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0
    )

    rationale: str = Field(
        min_length=1
    )

    evidence: dict[
        str,
        EvidenceValue
    ] = Field(
        default_factory=dict
    )


    @model_validator(
        mode="after"
    )
    def validate_signal_role(self):

        # ==================================
        # CONTEXTUAL AGENT RULES
        # ==================================
        #
        # Contextual agents may express
        # confidence about market conditions,
        # but they must never manufacture a
        # directional LONG or SHORT vote.
        # ==================================

        if (
            self.signal_type
            == AgentSignalType.CONTEXTUAL
        ):

            if (
                self.direction
                != AgentDirection.NEUTRAL
            ):

                raise ValueError(
                    "Contextual opinions must "
                    "be directionally neutral."
                )


            if (
                abs(self.score)
                > 1e-12
            ):

                raise ValueError(
                    "Contextual opinions must "
                    "have directional score 0."
                )


        return self