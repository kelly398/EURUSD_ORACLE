import time

from backend.data_engine.mt5_tick_ingestor import (
    MT5TickIngestor
)


LOOP_INTERVAL_SECONDS = 1

RECONNECT_DELAY_SECONDS = 5


def main():

    ingestor = MT5TickIngestor(

        symbol="EURUSD",

        bootstrap_minutes=30,

        overlap_seconds=2,

        catchup_window_minutes=60
    )


    print(
        "EUR/USD MT5 Tick Ingestor started.",
        flush=True
    )


    print(
        "Mode: bounded recovery + "
        "continuous live ingestion",
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
                "Tick ingestor connected "
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
                "\nTick ingestor stopping...",
                flush=True
            )

            break


        except Exception as error:

            print(
                "TICK INGESTOR ERROR | "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True
            )


            print(
                "Tick ingestor will attempt "
                "reconnection.",
                flush=True
            )


        finally:

            ingestor.disconnect()


        time.sleep(
            RECONNECT_DELAY_SECONDS
        )


    print(
        "Tick ingestor stopped cleanly.",
        flush=True
    )


if __name__ == "__main__":

    main()