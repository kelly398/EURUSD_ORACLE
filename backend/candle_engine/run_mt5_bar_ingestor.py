import time

from backend.candle_engine.mt5_bar_ingestor import (
    MT5BarIngestor
)


LOOP_INTERVAL_SECONDS = 5

RECONNECT_DELAY_SECONDS = 5


def main():

    ingestor = MT5BarIngestor(

        symbol="EURUSD",

        timeframe="M5",

        timeframe_minutes=5,

        bootstrap_hours=24,

        repair_lookback_minutes=60
    )


    print(
        "EUR/USD Canonical M5 "
        "Bar Ingestor started.",
        flush=True
    )


    print(
        "Source: HFM / MT5",
        flush=True
    )


    while True:

        try:

            # ==================================
            # CONNECT / RECONNECT
            # ==================================

            if not ingestor.connect():

                print(
                    "MT5 unavailable. "
                    f"Retrying in "
                    f"{RECONNECT_DELAY_SECONDS} "
                    "seconds...",
                    flush=True
                )

                time.sleep(
                    RECONNECT_DELAY_SECONDS
                )

                continue


            print(
                "M5 bar ingestor connected "
                "to MT5.",
                flush=True
            )


            # ==================================
            # INGESTION LOOP
            # ==================================

            while True:

                if not (
                    ingestor.provider
                    .is_healthy()
                ):

                    raise RuntimeError(
                        "MT5 connection became "
                        "unhealthy."
                    )


                ingestor.ingest_once()


                time.sleep(
                    LOOP_INTERVAL_SECONDS
                )


        except KeyboardInterrupt:

            print(
                "\nM5 bar ingestor stopping...",
                flush=True
            )

            break


        except Exception as error:

            print(
                "M5 INGESTOR ERROR | "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True
            )


            print(
                "M5 bar ingestor will attempt "
                "reconnection.",
                flush=True
            )


        finally:

            ingestor.disconnect()


        time.sleep(
            RECONNECT_DELAY_SECONDS
        )


    print(
        "M5 bar ingestor stopped cleanly.",
        flush=True
    )


if __name__ == "__main__":

    main()