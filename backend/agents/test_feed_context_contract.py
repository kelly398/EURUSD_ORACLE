from datetime import datetime, timedelta, timezone

import pytest

from backend.agents.contracts import AgentContext
from backend.market_state.schemas import (
    CandleBar,
    DataQualityStatus,
    MarketStateSnapshot,
    TickSample,
)


FEED_A = "mt5_eurusd_v1_" + "a" * 64
FEED_B = "mt5_eurusd_v1_" + "b" * 64
AS_OF = datetime(2026, 11, 3, 14, 5, tzinfo=timezone.utc)


def candle(feed_id):
    return CandleBar(
        symbol="EURUSD",
        timeframe="M5",
        timestamp=AS_OF - timedelta(minutes=5),
        open=1.1,
        high=1.101,
        low=1.099,
        close=1.1005,
        tick_volume=10,
        spread_points=2,
        real_volume=0,
        source="MT5",
        feed_id=feed_id,
    )


def context(*, context_feed=FEED_A, snapshot_feed=FEED_A, candle_feed=FEED_A,
            tick_feed=FEED_A, include_tick=False):
    ticks = ()
    if include_tick:
        ticks = (TickSample(
            symbol="EURUSD",
            timestamp=AS_OF - timedelta(seconds=1),
            source_time_msc=int((AS_OF - timedelta(seconds=1)).timestamp() * 1000),
            bid=1.1,
            ask=1.1002,
            spread_pips=2,
            feed_id=tick_feed,
        ),)
    return AgentContext(
        symbol="EURUSD",
        feed_id=context_feed,
        timeframe="M5",
        as_of=AS_OF,
        market_state=MarketStateSnapshot(
            symbol="EURUSD",
            feed_id=snapshot_feed,
            timeframe="M5",
            as_of=AS_OF,
            data_quality=DataQualityStatus.PASS,
            candle_count=1,
        ),
        candles=(candle(candle_feed),),
        ticks=ticks,
    )


def test_context_preserves_one_feed_across_snapshot_and_history():
    result = context()

    assert result.feed_id == FEED_A
    assert result.market_state.feed_id == FEED_A
    assert result.candles[0].feed_id == FEED_A


@pytest.mark.parametrize("kwargs, message", [
    ({"snapshot_feed": FEED_B}, "does not match MarketStateSnapshot"),
    ({"candle_feed": FEED_B}, "mixed candle feed IDs"),
])
def test_context_rejects_snapshot_or_candle_feed_mismatch(kwargs, message):
    with pytest.raises(ValueError, match=message):
        context(**kwargs)


def test_context_rejects_tick_from_another_feed():
    with pytest.raises(ValueError, match="mixed tick feed IDs"):
        context(tick_feed=FEED_B, include_tick=True)
