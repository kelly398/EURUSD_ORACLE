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
# CENTRAL-BANK RATE SNAPSHOT
# ============================================================

class CentralBankRateSnapshot(
    BaseModel
):

    """
    One causally versioned central-bank policy-rate
    observation.

    `reference_date` identifies the economic/effective
    date associated with the observation.

    `available_at` is the earliest timestamp at which
    THIS EXACT VERSION may legally enter analysis.

    `available_at` is deliberately NOT required to be
    on or after `reference_date`: a rate decision may
    be publicly announced before a later effective date.

    Later corrections or changed source contents must
    create a new version with a new content_hash and
    their own available_at timestamp.
    """

    model_config = ConfigDict(
        frozen=True
    )

    central_bank: str = Field(
        min_length=1
    )

    rate_name: str = Field(
        min_length=1
    )

    reference_date: date

    available_at: datetime

    value_percent: float

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
    def validate_causal_timestamps(
        self
    ):

        if (
            self.available_at.tzinfo
            is None
        ):

            raise ValueError(
                "Central-bank rate available_at "
                "must be timezone-aware."
            )


        if (
            self.ingested_at.tzinfo
            is None
        ):

            raise ValueError(
                "Central-bank rate ingested_at "
                "must be timezone-aware."
            )


        return self


# ============================================================
# ECONOMIC CALENDAR EVENT SNAPSHOT
# ============================================================

class EconomicCalendarEventSnapshot(
    BaseModel
):

    """
    One causally versioned economic-calendar event.

    `scheduled_at` is when the economic event is
    scheduled to occur.

    `available_at` is when THIS EXACT VERSION of the
    calendar record became publicly knowable.

    This distinction is essential:

    - scheduled events may be publicly known before
      scheduled_at;
    - actual/forecast/previous values may appear at or
      after scheduled_at;
    - later revisions must receive a new content_hash
      and later available_at rather than overwriting
      an earlier historical version.

    `source_event_id` must represent the stable logical
    event identity supplied or deterministically derived
    by the acquisition layer.
    """

    model_config = ConfigDict(
        frozen=True
    )

    source_event_id: str = Field(
        min_length=1
    )

    event_name: str = Field(
        min_length=1
    )

    jurisdiction: str = Field(
        min_length=1
    )

    currency: str = Field(
        min_length=3,
        max_length=3
    )

    scheduled_at: datetime

    available_at: datetime

    actual: float | None = None

    forecast: float | None = None

    previous: float | None = None

    unit: str | None = None

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
    def validate_causal_timestamps(
        self
    ):

        for (
            field_name,
            value
        ) in (
            (
                "scheduled_at",
                self.scheduled_at
            ),
            (
                "available_at",
                self.available_at
            ),
            (
                "ingested_at",
                self.ingested_at
            )
        ):

            if (
                value.tzinfo
                is None
            ):

                raise ValueError(
                    "Economic-calendar "
                    f"{field_name} must be "
                    "timezone-aware."
                )


        return self