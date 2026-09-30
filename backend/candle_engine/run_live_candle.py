import time

from backend.candle_engine.live_candle_engine import (
    LiveCandleEngine
)


def main():

    engine = LiveCandleEngine(
        symbol="EURUSD",
        timeframe_minutes=5
    )

    print(
        "EUR/USD M5 Candle Engine started."
    )

    print(
        "Market clock source: MT5 tick timestamps"
    )


    try:

        while True:

            engine.generate_latest_completed_candle()

            time.sleep(2)


    except KeyboardInterrupt:

        print(
            "\nM5 Candle Engine stopped cleanly."
        )


if __name__ == "__main__":

    main()
    