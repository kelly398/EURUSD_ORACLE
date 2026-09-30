from abc import ABC, abstractmethod


class MarketDataProvider(ABC):


    @abstractmethod
    def connect(self):
        pass


    @abstractmethod
    def disconnect(self):
        pass


    @abstractmethod
    def get_price(self, symbol):
        pass


    @abstractmethod
    def get_candles(self, symbol, timeframe, count):
        pass