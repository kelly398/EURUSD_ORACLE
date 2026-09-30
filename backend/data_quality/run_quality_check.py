from backend.data_quality.quality_engine import (
    DataQualityEngine
)


def main():

    engine = DataQualityEngine(

        symbol="EURUSD",

        timeframe="M5",

        timeframe_minutes=5,

        lookback_hours=6
    )


    report = engine.run()


    print(
        "\n"
        + "=" * 60
    )

    print(
        "EUR/USD ORACLE "
        "DATA QUALITY REPORT"
    )

    print(
        "=" * 60
    )


    print(
        f"STATUS: "
        f"{report['status']}"
    )


    print(
        f"Bars checked: "
        f"{report['bars_checked']}"
    )


    print(
        f"MT5 broker time: "
        f"{report['broker_now']}"
    )


    print(
        f"Latest tick: "
        f"{report['latest_tick']}"
    )


    print(
        f"Tick lag seconds: "
        f"{report['tick_lag_seconds']}"
    )


    print(
        "Latest broker candle: "
        f"{report['latest_broker_candle']}"
    )


    print(
        "Expected latest candle: "
        f"{report['expected_latest_candle']}"
    )


    print(
        f"Issues: "
        f"{len(report['issues'])}"
    )


    for issue in report["issues"]:

        print(
            f"[{issue['severity']}] "
            f"{issue['check']}: "
            f"{issue['message']}"
        )


    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
    