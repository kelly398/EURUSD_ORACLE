from sqlalchemy import (
    Index,
    UniqueConstraint
)

from backend.database.models import (
    Base,
    CentralBankRate,
    EconomicCalendarEvent
)


def constraint_names(
    table
):

    return {
        constraint.name

        for constraint
        in table.constraints

        if isinstance(
            constraint,
            UniqueConstraint
        )
    }


def index_names(
    table
):

    return {
        index.name

        for index
        in table.indexes

        if isinstance(
            index,
            Index
        )
    }


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "MACRO STORAGE MODEL TEST"
    )

    print("=" * 70)


    # ========================================================
    # TABLE REGISTRATION
    # ========================================================

    assert (
        "central_bank_rates"
        in Base.metadata.tables
    )

    assert (
        "economic_calendar_events"
        in Base.metadata.tables
    )


    print(
        "\nMacro table registration: PASS"
    )


    # ========================================================
    # CENTRAL BANK RATE TABLE
    # ========================================================

    rate_table = (
        CentralBankRate.__table__
    )


    expected_rate_columns = {
        "id",
        "central_bank",
        "rate_name",
        "reference_date",
        "available_at",
        "value_percent",
        "source_record_id",
        "content_hash",
        "source",
        "ingested_at"
    }


    assert (
        set(
            rate_table.columns.keys()
        )
        == expected_rate_columns
    )


    assert (
        "uq_central_bank_rate_version"
        in constraint_names(
            rate_table
        )
    )


    assert (
        "ix_central_bank_rates_bank_rate_available"
        in index_names(
            rate_table
        )
    )


    assert (
        "ix_central_bank_rates_bank_rate_reference"
        in index_names(
            rate_table
        )
    )


    assert (
        rate_table
        .c
        .value_percent
        .nullable
        is False
    )


    assert (
        rate_table
        .c
        .available_at
        .nullable
        is False
    )


    assert (
        rate_table
        .c
        .content_hash
        .type
        .length
        == 64
    )


    print(
        "Central-bank version storage: PASS"
    )


    # ========================================================
    # ECONOMIC CALENDAR TABLE
    # ========================================================

    event_table = (
        EconomicCalendarEvent.__table__
    )


    expected_event_columns = {
        "id",
        "source_event_id",
        "event_name",
        "jurisdiction",
        "currency",
        "scheduled_at",
        "available_at",
        "actual",
        "forecast",
        "previous",
        "unit",
        "content_hash",
        "source",
        "ingested_at"
    }


    assert (
        set(
            event_table.columns.keys()
        )
        == expected_event_columns
    )


    assert (
        "uq_economic_calendar_event_version"
        in constraint_names(
            event_table
        )
    )


    assert (
        "ix_economic_calendar_currency_scheduled_available"
        in index_names(
            event_table
        )
    )


    assert (
        "ix_economic_calendar_event_available"
        in index_names(
            event_table
        )
    )


    assert (
        event_table
        .c
        .scheduled_at
        .nullable
        is False
    )


    assert (
        event_table
        .c
        .available_at
        .nullable
        is False
    )


    assert (
        event_table
        .c
        .actual
        .nullable
        is True
    )


    assert (
        event_table
        .c
        .forecast
        .nullable
        is True
    )


    assert (
        event_table
        .c
        .previous
        .nullable
        is True
    )


    assert (
        event_table
        .c
        .currency
        .type
        .length
        == 3
    )


    assert (
        event_table
        .c
        .content_hash
        .type
        .length
        == 64
    )


    print(
        "Economic-calendar version storage: PASS"
    )


    # ========================================================
    # NO UPDATE-IN-PLACE VERSION IDENTITY
    # ========================================================

    rate_unique = next(

        constraint

        for constraint
        in rate_table.constraints

        if (
            isinstance(
                constraint,
                UniqueConstraint
            )

            and

            constraint.name
            == "uq_central_bank_rate_version"
        )
    )


    assert tuple(
        column.name

        for column
        in rate_unique.columns
    ) == (
        "source",
        "central_bank",
        "rate_name",
        "source_record_id",
        "content_hash"
    )


    event_unique = next(

        constraint

        for constraint
        in event_table.constraints

        if (
            isinstance(
                constraint,
                UniqueConstraint
            )

            and

            constraint.name
            == "uq_economic_calendar_event_version"
        )
    )


    assert tuple(
        column.name

        for column
        in event_unique.columns
    ) == (
        "source",
        "source_event_id",
        "content_hash"
    )


    print(
        "Revision-preserving identities: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "MACRO STORAGE MODEL TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()