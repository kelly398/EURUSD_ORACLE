import time

from backend.data_engine.mt5_tick_ingestor import (
    MT5TickIngestor
)


def main():

    ingestor = MT5TickIngestor(
        symbol="EURUSD",
        bootstrap_minutes=30,
        overlap_seconds=2
    )


    if not ingestor.connect():

        print(
            "Unable to connect to MT5."
        )

        return


    print(
        "EUR/USD MT5 Tick Ingestor started."
    )

    print(
        "Mode: historical recovery + live ingestion"
    )


    try:

        while True:

            ingestor.ingest_once()

            time.sleep(1)


    except KeyboardInterrupt:

        print(
            "\nTick ingestor stopping..."
        )


    finally:

        ingestor.disconnect()

        print(
            "MT5 disconnected cleanly."
        )


if __name__ == "__main__":

    main()
    