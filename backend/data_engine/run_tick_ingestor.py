import signal
import time

from backend.data_engine.mt5_tick_ingestor import (
    MT5TickIngestor
)

from backend.runtime.process_lock import (
    SingleInstanceLock
)


LOOP_INTERVAL_SECONDS = 1

RECONNECT_DELAY_SECONDS = 5


def _request_shutdown(
    signum,
    frame
):

    raise KeyboardInterrupt


def _install_signal_handlers():

    if hasattr(
        signal,
        "SIGBREAK"
    ):

        signal.signal(
            signal.SIGBREAK,
            _request_shutdown
        )


    if hasattr(
        signal,
        "SIGTERM"
    ):

        signal.signal(
            signal.SIGTERM,
            _request_shutdown
        )


def run_worker():

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


def main():

    _install_signal_handlers()


    lock = (
        SingleInstanceLock(
            "tick_ingestor"
        )
    )


    if not lock.acquire():

        print(
            "Another EUR/USD tick ingestor "
            "is already running.",
            flush=True
        )

        return


    try:

        run_worker()

    except KeyboardInterrupt:
        # SIGBREAK may arrive during the reconnect sleep outside run_worker's
        # inner try block. Shutdown remains a clean exit in that state too.
        print("Tick ingestor stopped during reconnect.", flush=True)


    finally:

        lock.release()


if __name__ == "__main__":

    main()
