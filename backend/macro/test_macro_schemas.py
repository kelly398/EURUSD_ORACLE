from datetime import (
    date,
    datetime,
    timezone
)

from pydantic import (
    ValidationError
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def make_rate(
    **overrides
):

    values = dict(

        central_bank="ECB",

        rate_name=(
            "deposit_facility_rate"
        ),

        reference_date=date(
            2026,
            1,
            22
        ),

        available_at=datetime(
            2026,
            1,
            21,
            13,
            15,
            tzinfo=timezone.utc
        ),

        value_percent=2.0,

        source_record_id=(
            "ecb-rate-2026-01-22"
        ),

        content_hash=HASH_A,

        source="ECB",

        ingested_at=datetime(
            2026,
            1,
            21,
            13,
            16,
            tzinfo=timezone.utc
        )
    )


    values.update(
        overrides
    )


    return CentralBankRateSnapshot(
        **values
    )


def make_event(
    **overrides
):

    values = dict(

        source_event_id=(
            "ecb-policy-2026-01"
        ),

        event_name=(
            "ECB Monetary Policy Decision"
        ),

        jurisdiction="EURO_AREA",

        currency="EUR",

        scheduled_at=datetime(
            2026,
            1,
            22,
            13,
            15,
            tzinfo=timezone.utc
        ),

        available_at=datetime(
            2026,
            1,
            10,
            12,
            0,
            tzinfo=timezone.utc
        ),

        actual=None,

        forecast=None,

        previous=None,

        unit=None,

        content_hash=HASH_A,

        source="ECB",

        ingested_at=datetime(
            2026,
            1,
            10,
            12,
            1,
            tzinfo=timezone.utc
        )
    )


    values.update(
        overrides
    )


    return EconomicCalendarEventSnapshot(
        **values
    )


def assert_validation_error(
    factory,
    **overrides
):

    try:

        factory(
            **overrides
        )

    except ValidationError:

        return


    raise AssertionError(
        "Expected validation failure."
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "MACRO CAUSAL SCHEMA TEST"
    )

    print(
        "=" * 70
    )


    # ========================================================
    # CENTRAL BANK RATE CONTRACT
    # ========================================================

    rate = (
        make_rate()
    )


    assert (
        rate.central_bank
        == "ECB"
    )

    assert (
        rate.rate_name
        == "deposit_facility_rate"
    )

    assert (
        rate.value_percent
        == 2.0
    )


    print(
        "\nCentral-bank rate schema: PASS"
    )


    # Policy rates may historically be negative.
    negative_rate = (
        make_rate(
            value_percent=-0.50
        )
    )

    assert (
        negative_rate.value_percent
        == -0.50
    )


    print(
        "Negative-rate support: PASS"
    )


    # Announcement may precede the date on which
    # the rate becomes economically effective.
    advance_announcement = (
        make_rate(
            reference_date=date(
                2026,
                1,
                22
            ),
            available_at=datetime(
                2026,
                1,
                21,
                13,
                15,
                tzinfo=timezone.utc
            )
        )
    )

    assert (
        advance_announcement
        .available_at
        .date()
        < advance_announcement
        .reference_date
    )


    print(
        "Advance-announcement causality: PASS"
    )


    # ========================================================
    # ECONOMIC CALENDAR CONTRACT
    # ========================================================

    scheduled = (
        make_event()
    )


    assert (
        scheduled.available_at
        < scheduled.scheduled_at
    )

    assert (
        scheduled.actual
        is None
    )


    print(
        "Pre-event calendar visibility: PASS"
    )


    released = (
        make_event(

            available_at=datetime(
                2026,
                1,
                22,
                13,
                15,
                5,
                tzinfo=timezone.utc
            ),

            actual=2.0,

            previous=2.0,

            unit="percent",

            content_hash=HASH_B,

            ingested_at=datetime(
                2026,
                1,
                22,
                13,
                15,
                6,
                tzinfo=timezone.utc
            )
        )
    )


    assert (
        released.source_event_id
        == scheduled.source_event_id
    )

    assert (
        released.available_at
        > released.scheduled_at
    )

    assert (
        released.content_hash
        != scheduled.content_hash
    )

    assert (
        released.actual
        == 2.0
    )


    print(
        "Post-event release version: PASS"
    )


    # ========================================================
    # TIMEZONE-AWARE REQUIREMENT
    # ========================================================

    assert_validation_error(

        make_rate,

        available_at=datetime(
            2026,
            1,
            21,
            13,
            15
        )
    )


    assert_validation_error(

        make_rate,

        ingested_at=datetime(
            2026,
            1,
            21,
            13,
            16
        )
    )


    assert_validation_error(

        make_event,

        scheduled_at=datetime(
            2026,
            1,
            22,
            13,
            15
        )
    )


    assert_validation_error(

        make_event,

        available_at=datetime(
            2026,
            1,
            10,
            12,
            0
        )
    )


    assert_validation_error(

        make_event,

        ingested_at=datetime(
            2026,
            1,
            10,
            12,
            1
        )
    )


    print(
        "Timezone-aware provenance: PASS"
    )


    # ========================================================
    # VERSION HASH CONTRACT
    # ========================================================

    assert_validation_error(

        make_rate,

        content_hash="short"
    )


    assert_validation_error(

        make_event,

        content_hash="short"
    )


    print(
        "64-character content hash: PASS"
    )


    # ========================================================
    # IMMUTABILITY
    # ========================================================

    try:

        rate.value_percent = 3.0

    except (
        ValidationError,
        TypeError
    ):

        pass

    else:

        raise AssertionError(
            "Central-bank rate snapshot "
            "was mutable."
        )


    try:

        scheduled.actual = 2.0

    except (
        ValidationError,
        TypeError
    ):

        pass

    else:

        raise AssertionError(
            "Economic-calendar snapshot "
            "was mutable."
        )


    print(
        "Frozen macro snapshots: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "MACRO CAUSAL SCHEMA TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()