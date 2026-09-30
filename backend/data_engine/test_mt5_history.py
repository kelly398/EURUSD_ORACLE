from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.data_engine.mt5_provider import (
    MT5Provider
)


def main():

    provider = MT5Provider()

    if not provider.connect():

        print(
            "MT5 CONNECTION FAILED"
        )

        return


    try:

        price = provider.get_price(
            "EURUSD"
        )

        if price is None:

            print(
                "NO EURUSD PRICE"
            )

            return


        broker_now = datetime.fromtimestamp(
            price["time_msc"] / 1000,
            tz=timezone.utc
        )


        start = (
            broker_now
            - timedelta(minutes=10)
        )


        print(
            "\nBROKER TIME:"
        )

        print(
            broker_now
        )


        ticks = provider.get_ticks_range(
            "EURUSD",
            start,
            broker_now
        )


        print(
            "\nTICKS RETURNED:"
        )

        print(
            len(ticks)
        )


        if len(ticks) > 0:

            print(
                "\nFIRST TICK:"
            )

            print(
                ticks[0]
            )

            print(
                "\nLAST TICK:"
            )

            print(
                ticks[-1]
            )


        rates = provider.get_candles(
            "EURUSD",
            "M5",
            start,
            broker_now
        )


        print(
            "\nM5 BARS RETURNED:"
        )

        print(
            len(rates)
        )


        for rate in rates:

            timestamp = datetime.fromtimestamp(
                int(rate["time"]),
                tz=timezone.utc
            )

            print(
                {
                    "timestamp":
                        timestamp,

                    "open":
                        float(rate["open"]),

                    "high":
                        float(rate["high"]),

                    "low":
                        float(rate["low"]),

                    "close":
                        float(rate["close"]),

                    "tick_volume":
                        int(
                            rate["tick_volume"]
                        ),

                    "spread":
                        int(rate["spread"]),

                    "real_volume":
                        int(
                            rate["real_volume"]
                        ),
                }
            )


    finally:

        provider.disconnect()

        print(
            "\nMT5 disconnected cleanly."
        )


if __name__ == "__main__":

    main()
    