from datetime import (
    date,
    datetime,
    timezone
)

from pydantic import ValidationError

from backend.agents.contracts import (
    AgentContext
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)

from backend.market_state.schemas import (
    CandleBar,
    DataQualityStatus,
    MarketStateSnapshot
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder
)


def make_cot(
    *,
    report_date,
    available_at,
    content_hash,
    source_record_id,
    asset_manager_long,
    contract_code=(
        EURO_FX_CFTC_CONTRACT_MARKET_CODE
    )
):

    return COTPositioningSnapshot(

        report_type=(
            COT_REPORT_TYPE_TFF_FUTURES_ONLY
        ),

        cftc_contract_market_code=(
            contract_code
        ),

        market_and_exchange_names=(
            "EURO FX - "
            "CHICAGO MERCANTILE EXCHANGE"
        ),

        contract_market_name=(
            "EURO FX"
        ),

        commodity_name=(
            "EURO FX"
        ),

        report_date=
            report_date,

        available_at=
            available_at,

        open_interest=
            800_000,

        dealer_long=
            50_000,

        dealer_short=
            250_000,

        dealer_spread=
            8_000,

        asset_manager_long=
            asset_manager_long,

        asset_manager_short=
            280_000,

        asset_manager_spread=
            40_000,

        leveraged_money_long=
            125_000,

        leveraged_money_short=
            165_000,

        leveraged_money_spread=
            20_000,

        other_reportables_long=
            28_000,

        other_reportables_short=
            20_000,

        other_reportables_spread=
            4_000,

        nonreportable_long=
            80_000,

        nonreportable_short=
            58_000,

        source_record_id=
            source_record_id,

        content_hash=
            content_hash,

        source="CFTC",

        ingested_at=
            available_at
    )


def build_base_context(
    *,
    as_of,
    cot_data=tuple()
):

    candle_timestamp = (
        datetime(
            2026,
            9,
            30,
            12,
            0,
            tzinfo=timezone.utc
        )
    )


    candle = CandleBar(

        symbol="EURUSD",

        timeframe="M5",

        timestamp=
            candle_timestamp,

        open=1.1700,

        high=1.1710,

        low=1.1690,

        close=1.1705,

        tick_volume=100,

        spread_points=10,

        real_volume=0.0,

        source="MT5"
    )


    market_state = (
        MarketStateSnapshot(

            symbol="EURUSD",

            timeframe="M5",

            as_of=as_of,

            data_quality=(
                DataQualityStatus.PASS
            ),

            candle_count=1
        )
    )


    return AgentContext(

        symbol="EURUSD",

        timeframe="M5",

        as_of=as_of,

        market_state=market_state,

        candles=(
            candle,
        ),

        ticks=tuple(),

        cot_data=cot_data
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COT CONTEXT CONTRACT TEST"
    )

    print(
        "=" * 70
    )


    as_of = datetime(
        2026,
        9,
        30,
        12,
        5,
        tzinfo=timezone.utc
    )


    report_one = make_cot(

        report_date=date(
            2026,
            9,
            15
        ),

        available_at=datetime(
            2026,
            9,
            18,
            19,
            30,
            tzinfo=timezone.utc
        ),

        content_hash=(
            "1" * 64
        ),

        source_record_id=(
            "report-1-original"
        ),

        asset_manager_long=
            480_000
    )


    report_two_original = make_cot(

        report_date=date(
            2026,
            9,
            22
        ),

        available_at=datetime(
            2026,
            9,
            25,
            19,
            30,
            tzinfo=timezone.utc
        ),

        content_hash=(
            "2" * 64
        ),

        source_record_id=(
            "report-2-original"
        ),

        asset_manager_long=
            490_000
    )


    report_two_future_revision = make_cot(

        report_date=date(
            2026,
            9,
            22
        ),

        available_at=datetime(
            2026,
            10,
            1,
            14,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=(
            "3" * 64
        ),

        source_record_id=(
            "report-2-future-correction"
        ),

        asset_manager_long=
            495_000
    )


    future_report = make_cot(

        report_date=date(
            2026,
            9,
            29
        ),

        available_at=datetime(
            2026,
            10,
            2,
            19,
            30,
            tzinfo=timezone.utc
        ),

        content_hash=(
            "4" * 64
        ),

        source_record_id=(
            "report-3-future"
        ),

        asset_manager_long=
            500_000
    )


    # ==================================
    # BACKWARD COMPATIBILITY
    # ==================================

    empty_context = (
        build_base_context(
            as_of=as_of
        )
    )


    assert (
        empty_context.cot_data
        == tuple()
    )


    print(
        "\nEmpty COT backward compatibility: PASS"
    )


    # ==================================
    # HISTORICAL SNAPSHOT LOCK
    # ==================================

    replay_builder = (
        HistoricalReplayBuilder()
    )


    locked = (
        replay_builder
        ._lock_cot_data_as_of(

            cot_data=(

                future_report,

                report_two_future_revision,

                report_one,

                report_two_original
            ),

            as_of=as_of
        )
    )


    assert (
        len(
            locked
        )
        == 2
    )


    assert (
        locked[0].report_date
        == date(
            2026,
            9,
            15
        )
    )


    assert (
        locked[1].report_date
        == date(
            2026,
            9,
            22
        )
    )


    assert (
        locked[1].source_record_id
        == "report-2-original"
    )


    assert (
        locked[1].asset_manager_long
        == 490_000
    )


    print(
        "Historical publication lock: PASS"
    )

    print(
        "Future report exclusion: PASS"
    )

    print(
        "Future revision exclusion: PASS"
    )

    print(
        "Chronological COT ordering: PASS"
    )


    # ==================================
    # VALID CONTEXT
    # ==================================

    context = (
        build_base_context(
            as_of=as_of,
            cot_data=locked
        )
    )


    assert (
        context.cot_data
        == locked
    )


    print(
        "Snapshot-locked AgentContext: PASS"
    )


    # ==================================
    # DIRECT LOOK-AHEAD REJECTION
    # ==================================

    try:

        build_base_context(

            as_of=as_of,

            cot_data=(
                future_report,
            )
        )

    except ValidationError:

        pass

    else:

        raise AssertionError(
            "Future COT report was "
            "incorrectly accepted."
        )


    print(
        "Direct COT look-ahead guard: PASS"
    )


    # ==================================
    # DUPLICATE REPORT DATE
    # ==================================

    try:

        build_base_context(

            as_of=datetime(
                2026,
                10,
                3,
                tzinfo=timezone.utc
            ),

            cot_data=(

                report_two_original,

                report_two_future_revision
            )
        )

    except ValidationError:

        pass

    else:

        raise AssertionError(
            "Multiple versions of one "
            "report_date were accepted."
        )


    print(
        "Single-version-per-report guard: PASS"
    )


    # ==================================
    # MIXED CONTRACT SERIES
    # ==================================

    other_contract = make_cot(

        report_date=date(
            2026,
            9,
            29
        ),

        available_at=datetime(
            2026,
            9,
            29,
            10,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=(
            "5" * 64
        ),

        source_record_id=(
            "other-contract"
        ),

        asset_manager_long=
            1,

        contract_code="000000"
    )


    try:

        build_base_context(

            as_of=as_of,

            cot_data=(

                report_one,

                other_contract
            )
        )

    except ValidationError:

        pass

    else:

        raise AssertionError(
            "Mixed COT contract series "
            "was incorrectly accepted."
        )


    print(
        "Mixed-contract guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "COT CONTEXT CONTRACT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()