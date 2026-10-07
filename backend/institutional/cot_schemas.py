from datetime import (
    date,
    datetime
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator
)


# ============================================================
# CANONICAL COT CONSTANTS
# ============================================================

COT_REPORT_TYPE_TFF_FUTURES_ONLY = (
    "TFF_FUTURES_ONLY"
)

EURO_FX_CFTC_CONTRACT_MARKET_CODE = (
    "099741"
)


# ============================================================
# COT POSITIONING SNAPSHOT
# ============================================================

class COTPositioningSnapshot(BaseModel):

    """
    Immutable representation of one exact
    version of a CFTC positioning report.

    `available_at` is the earliest timestamp
    at which this exact version is allowed to
    participate in live or historical analysis.

    This is deliberately distinct from
    `report_date`.

    Example:

        report_date:
            Tuesday position snapshot.

        available_at:
            Official publication timestamp,
            normally several days later.

    Corrected/revised reports must be stored as
    new versions with a later available_at rather
    than overwriting history.
    """

    model_config = ConfigDict(
        frozen=True
    )

    report_type: str = Field(
        min_length=1
    )

    cftc_contract_market_code: str = Field(
        min_length=1
    )

    market_and_exchange_names: str = Field(
        min_length=1
    )

    contract_market_name: str = Field(
        min_length=1
    )

    commodity_name: str = Field(
        min_length=1
    )

    report_date: date

    available_at: datetime

    open_interest: int = Field(
        ge=0
    )

    dealer_long: int = Field(
        ge=0
    )

    dealer_short: int = Field(
        ge=0
    )

    dealer_spread: int = Field(
        ge=0
    )

    asset_manager_long: int = Field(
        ge=0
    )

    asset_manager_short: int = Field(
        ge=0
    )

    asset_manager_spread: int = Field(
        ge=0
    )

    leveraged_money_long: int = Field(
        ge=0
    )

    leveraged_money_short: int = Field(
        ge=0
    )

    leveraged_money_spread: int = Field(
        ge=0
    )

    other_reportables_long: int = Field(
        ge=0
    )

    other_reportables_short: int = Field(
        ge=0
    )

    other_reportables_spread: int = Field(
        ge=0
    )

    nonreportable_long: int = Field(
        ge=0
    )

    nonreportable_short: int = Field(
        ge=0
    )

    source_record_id: str = Field(
        min_length=1
    )

    content_hash: str = Field(
        min_length=64,
        max_length=64
    )

    source: str = Field(
        min_length=1
    )

    ingested_at: datetime


    @model_validator(
        mode="after"
    )
    def validate_temporal_contract(self):

        if (
            self.available_at.tzinfo
            is None
        ):

            raise ValueError(
                "COT available_at must "
                "be timezone-aware."
            )


        if (
            self.ingested_at.tzinfo
            is None
        ):

            raise ValueError(
                "COT ingested_at must "
                "be timezone-aware."
            )


        if (
            self.available_at.date()
            < self.report_date
        ):

            raise ValueError(
                "COT available_at cannot "
                "precede report_date."
            )


        return self