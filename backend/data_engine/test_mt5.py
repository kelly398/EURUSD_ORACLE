from backend.data_engine.mt5_provider import MT5Provider



def main():


    broker = MT5Provider()


    connected = broker.connect()



    if connected:

        print(
            "MT5 CONNECTION SUCCESS"
        )


        price = broker.get_price(
            "EURUSD"
        )


        print(price)



        broker.disconnect()



    else:

        print(
            "MT5 CONNECTION FAILED"
        )



if __name__ == "__main__":

    main()
