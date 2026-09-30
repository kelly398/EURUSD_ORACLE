from backend.candle_engine.candle_builder import CandleBuilder


class FakeTick:

    def __init__(self, bid):

        self.bid = bid



def main():

    ticks = [

        FakeTick(1.13570),

        FakeTick(1.13580),

        FakeTick(1.13565),

        FakeTick(1.13585)

    ]


    builder = CandleBuilder()


    candle = builder.build_candle(

        ticks,

        "EURUSD",

        "M5"

    )


    print(candle)



if __name__ == "__main__":

    main()
    