import time

from backend.candle_engine.mt5_bar_ingestor import (
    MT5BarIngestor
)


def main():

    ingestor = MT5BarIngestor(

        symbol="EURUSD",

        timeframe="M5",

        timeframe_minutes=5,

        bootstrap_hours=24,

        repair_lookback_minutes=60
    )


    if not ingestor.connect():

        print(
            "Unable to connect to MT5."
        )

        return


    print(
        "EUR/USD Canonical M5 "
        "Bar Ingestor started."
    )

    print(
        "Source: HFM / MT5"
    )


    try:

        while True:

            ingestor.ingest_once()

            time.sleep(5)


    except KeyboardInterrupt:

        print(
            "\nM5 bar ingestor stopping..."
        )


    finally:

        ingestor.disconnect()

        print(
            "MT5 disconnected cleanly."
        )


if __name__ == "__main__":

    main()
    