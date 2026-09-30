from backend.data_engine.tick_collector import TickCollector



def main():

    collector = TickCollector()

    collector.start()



if __name__ == "__main__":

    main()
