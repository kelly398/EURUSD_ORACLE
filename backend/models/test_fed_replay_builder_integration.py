
"""F4.3.2: Offline Fed historical replay admission boundary tests.

Run:
    python -m backend.models.test_fed_replay_builder_integration

Nonempty Fed replay input intentionally remains disabled pending
independently attested historical source-version provenance.

No database, HTTP, broker, or HMM execution.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.market_state.schemas import (
    CandleBar,
    LiquidityState,
    MarketDirection,
    MomentumSnapshot,
    MomentumState,
    SpreadState,
    StructureState,
    TickSample,
    TrendState,
    VolatilityRegime,
    VolatilityState,
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder
)


UTC = timezone.utc

ANCHOR = datetime(
    2026, 9, 16, 18, 0, tzinfo=UTC
)

AS_OF = ANCHOR + timedelta(minutes=5)


def candles():
    start = ANCHOR - timedelta(minutes=5 * 139)

    return tuple(
        CandleBar(
            symbol="EURUSD",
            timeframe="M5",
            timestamp=start + timedelta(minutes=5 * i),
            open=1.10,
            high=1.11,
            low=1.09,
            close=1.10,
            tick_volume=100,
            spread_points=10,
            real_volume=0.0,
            source="TEST",
        )
        for i in range(140)
    )


def ticks():
    return (
        TickSample(
            symbol="EURUSD",
            timestamp=ANCHOR + timedelta(minutes=1),
            source_time_msc=0,
            bid=1.10,
            ask=1.1001,
            spread_pips=1.0,
        ),
    )


def fed_pair(
    released_at=ANCHOR,
    ingested_at=ANCHOR + timedelta(minutes=1),
):
    """Synthetic Fed-shaped records; not certified F1 evidence."""

    return tuple(
        CentralBankRateSnapshot(
            central_bank="FED",
            source="FED_FOMC_RELEASE",
            source_record_id=f"FOMC:monetary20260916a:{bound}",
            rate_name=f"federal_funds_target_{bound}",
            reference_date=date(2026, 9, 17),
            available_at=released_at,
            ingested_at=ingested_at,
            value_percent=value,
            content_hash=digest * 64,
        )
        for bound, value, digest in (
            ("lower", 3.75, "a"),
            ("upper", 4.0, "b"),
        )
    )


def builder_with_stub_engines():
    """Keep full Pydantic context creation, mock only market engines."""

    builder = HistoricalReplayBuilder()

    builder.trend_engine.analyze_detailed = Mock(
        return_value=SimpleNamespace(
            state=TrendState(
                direction=MarketDirection.NEUTRAL,
                strength=0,
            )
        )
    )

    builder.volatility_engine.analyze_detailed = Mock(
        return_value=SimpleNamespace(
            state=VolatilityState(
                regime=VolatilityRegime.NORMAL
            )
        )
    )

    builder.momentum_engine.analyze_detailed = Mock(
        return_value=SimpleNamespace(
            state=MomentumSnapshot(
                state=MomentumState.NEUTRAL,
                strength=0,
            )
        )
    )

    builder.structure_engine.analyze_detailed = Mock(
        return_value=SimpleNamespace(
            state=StructureState(
                label="range",
                confidence=1,
            )
        )
    )

    builder.liquidity_engine.analyze_detailed = Mock(
        return_value=SimpleNamespace(
            state=LiquidityState(
                spread_state=SpreadState.NORMAL
            )
        )
    )

    builder.session_engine.analyze = Mock(
        return_value=SimpleNamespace(
            session="test_session"
        )
    )

    return builder


class FedReplayBuilderIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.candle_window = candles()
        cls.tick_window = ticks()

    def setUp(self):
        self.builder = builder_with_stub_engines()

    def run_build(self, **kwargs):
        return self.builder.build(
            self.candle_window,
            self.tick_window,
            ANCHOR,
            **kwargs
        )

    def test_01_legacy_build_default_has_empty_fed_context(self):
        result = self.run_build()

        self.assertEqual(result.as_of, AS_OF)
        self.assertEqual(result.context.fed_data, ())
        self.assertEqual(result.context.ecb_data, ())
        self.assertEqual(
            result.context.economic_calendar,
            ()
        )

    def test_02_explicit_empty_fed_input_accepted(self):
        result = self.run_build(fed_data=())

        self.assertEqual(
            result.context.fed_data,
            ()
        )

    def test_03_legacy_seven_positional_arguments_unchanged(self):
        result = self.builder.build(
            self.candle_window,
            self.tick_window,
            ANCHOR,
            False,
            (),
            (),
            (),
        )

        self.assertEqual(
            result.context.fed_data,
            ()
        )

    def test_04_fed_pair_denied_before_market_engines(self):
        with self.assertRaisesRegex(
            ValueError,
            "Fed replay admission is disabled",
        ):
            self.run_build(
                fed_data=fed_pair()
            )

        self.builder.trend_engine.analyze_detailed.assert_not_called()

    def test_05_future_fed_input_also_denied(self):
        future = ANCHOR + timedelta(days=1)

        with self.assertRaisesRegex(
            ValueError,
            "Fed replay admission is disabled",
        ):
            self.run_build(
                fed_data=fed_pair(
                    future,
                    future,
                )
            )

    def test_06_generator_input_cannot_bypass_gate(self):
        data = (
            item
            for item in fed_pair()
        )

        with self.assertRaisesRegex(
            ValueError,
            "Fed replay admission is disabled",
        ):
            self.run_build(fed_data=data)

    def test_07_none_is_rejected(self):
        with self.assertRaisesRegex(
            TypeError,
            "cannot be None",
        ):
            self.run_build(
                fed_data=None
            )

    def test_08_empty_input_passes_through_lock_at_information_time(self):
        with patch(
            "backend.models.historical_replay.lock_fed_data_as_of",
            return_value=(),
        ) as mock_lock:

            self.run_build(
                fed_data=()
            )

            mock_lock.assert_called_once_with(
                fed_data=(),
                as_of=AS_OF,
            )

    def test_09_nonempty_lock_result_cannot_bypass_admission(self):
        with patch(
            "backend.models.historical_replay.lock_fed_data_as_of",
            return_value=fed_pair(),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Fed replay admission is disabled",
            ):
                self.run_build(fed_data=())

        self.builder.trend_engine.analyze_detailed.assert_not_called()

    def test_10_nonempty_input_never_reaches_lock(self):
        with patch(
            "backend.models.historical_replay.lock_fed_data_as_of",
            return_value=(),
        ) as mock_lock:

            with self.assertRaisesRegex(
                ValueError,
                "Fed replay admission is disabled",
            ):
                self.run_build(
                    fed_data=fed_pair()
                )

            mock_lock.assert_not_called()

    def test_11_empty_helper_delegates_to_pure_lock(self):
        self.assertEqual(
            self.builder._lock_fed_data_as_of(
                (),
                AS_OF,
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
