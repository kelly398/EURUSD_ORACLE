
"""MT5 market-data adapter with explicit transport and UTC-clock quarantine.

The default path never guesses broker offsets. An explicitly supplied, server-
bound forward clock policy can convert a documented broker wall-clock feed.
That path preserves original fields and cannot enter legacy ingestion storage.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from copy import deepcopy
import os

import MetaTrader5 as mt5

from backend.data_engine.base_provider import MarketDataProvider


MAX_FUTURE_SECONDS = 5.0
MAX_QUOTE_AGE_SECONDS = 180.0
MT5_INITIALIZE_TIMEOUT_MS = 60000


class MT5TimestampIntegrityError(RuntimeError):
    """Raw MT5 timestamps cannot safely be treated as current UTC instants."""


class MT5BrokerProfileMismatch(RuntimeError):
    """The selected local MT5 profile does not match the observed terminal."""


def _utc_now():
    return datetime.now(timezone.utc)


def _check_raw_clock_msc(time_msc, *, symbol, max_age_seconds=None):
    """Validate raw clock as UTC; do NOT apply a presumed broker offset."""
    try:
        msc = int(time_msc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MT5TimestampIntegrityError("MT5 timestamp is invalid.") from exc

    if msc <= 0:
        raise MT5TimestampIntegrityError("MT5 timestamp is nonpositive.")

    observed = _utc_now()
    skew_seconds = msc / 1000.0 - observed.timestamp()

    if skew_seconds > MAX_FUTURE_SECONDS:
        raise MT5TimestampIntegrityError(
            f"MT5 {symbol} raw timestamp is {skew_seconds:+.1f}s ahead of UTC; "
            "broker timestamp domain not verified. Ingestion is blocked."
        )

    if max_age_seconds is not None and skew_seconds < -max_age_seconds:
        raise MT5TimestampIntegrityError(
            f"MT5 {symbol} raw quote is {-skew_seconds:.1f}s old; "
            "fresh market state not established. Ingestion is blocked."
        )


def _check_live_tick(tick, symbol):
    if tick is None:
        raise MT5TimestampIntegrityError(
            f"No MT5 {symbol} quote for clock check."
        )

    try:
        seconds = int(tick.time)
        msc = int(tick.time_msc)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise MT5TimestampIntegrityError(
            "MT5 tick has no valid raw time fields."
        ) from exc

    if not 0 <= msc - seconds * 1000 < 1000:
        raise MT5TimestampIntegrityError(
            "MT5 tick time and time_msc disagree."
        )

    _check_raw_clock_msc(
        msc,
        symbol=symbol,
        max_age_seconds=MAX_QUOTE_AGE_SECONDS,
    )


def _utc_range(date_from, date_to):
    for name, value in (
        ("date_from", date_from),
        ("date_to", date_to),
    ):
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(f"{name} must be timezone-aware.")

    start = date_from.astimezone(timezone.utc)
    end = date_to.astimezone(timezone.utc)

    if start >= end:
        raise ValueError("MT5 query range must be strictly increasing.")

    return start, end


class MT5Provider(MarketDataProvider):

    TIMEFRAMES = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }

    def __init__(self, *, clock_policy=None, broker_profile=None):
        # Keep the original HFM wall-clock epoch path separate from the
        # unverified multi-broker Python-API timestamp contract.
        if clock_policy is not None and broker_profile is not None:
            raise ValueError("Clock-policy and multi-broker profile modes must not be combined.")
        if broker_profile is not None:
            from backend.runtime.stage7_universal_mt5_adapter import validate_profile
            validate_profile(broker_profile)
            broker_profile = deepcopy(broker_profile)
        if clock_policy is not None:
            from backend.data_engine.clock_policy import ClockPolicy
            if not isinstance(clock_policy, ClockPolicy):
                raise TypeError("MT5 clock policy must be a validated ClockPolicy.")
            clock_policy = ClockPolicy.model_validate(clock_policy.model_dump())
        self.clock_policy = clock_policy
        self.broker_profile = broker_profile
        self.connected = False

    def _require_profile_binding(self, symbol=None):
        """Check the selected MT5 feed without retaining account identifiers.

        The profile's ``account_ref`` is a *local alias*, not an authenticated
        MT5 account number. This method checks server/company, DEMO/LIVE,
        accounting mode and instrument properties, not independent G01 proof.
        All checks are local and read-only. No account switching or symbol_select.
        """
        profile = self.broker_profile
        if profile is None:
            return
        if not self.connected:
            raise MT5BrokerProfileMismatch("Selected MT5 provider is disconnected.")
        try:
            terminal = mt5.terminal_info()
            account = mt5.account_info()
            if terminal is None or terminal.connected is not True or account is None:
                raise MT5BrokerProfileMismatch("Terminal/account metadata unavailable.")
            if (account.company != profile['broker_name']
                    or account.server != profile['server_name']):
                raise MT5BrokerProfileMismatch("Broker/company or server differs from profile.")

            mode = account.trade_mode
            margin = account.margin_mode
            if type(mode) is not int or type(margin) is not int:
                raise MT5BrokerProfileMismatch("Account mode is not a valid MT5 integer.")
            trade_modes = {0: 'DEMO', 2: 'LIVE'}
            margin_modes = {0: 'NETTING', 1: 'EXCHANGE', 2: 'HEDGING'}
            if (trade_modes.get(mode) != profile['environment']
                    or margin_modes.get(margin) != profile['account_mode']):
                raise MT5BrokerProfileMismatch("Account environment or margin mode differs.")

            expected = profile['instrument']
            wanted = expected['broker_symbol']
            if symbol is not None and symbol != wanted:
                raise MT5BrokerProfileMismatch("Requested symbol differs from selected MT5 feed.")
            spec = mt5.symbol_info(wanted)
            if spec is None:
                raise MT5BrokerProfileMismatch("Selected symbol metadata is unavailable.")
            digits = spec.digits
            if type(digits) is not int or digits != expected['digits']:
                raise MT5BrokerProfileMismatch("Broker symbol precision differs.")

            def measured(value, field):
                if value is None or isinstance(value, bool):
                    raise MT5BrokerProfileMismatch("Invalid symbol measurement: " + field)
                try:
                    number = Decimal(str(value))
                except (InvalidOperation, ValueError, TypeError, OverflowError):
                    raise MT5BrokerProfileMismatch("Invalid symbol measurement: " + field) from None
                if not number.is_finite() or number <= 0:
                    raise MT5BrokerProfileMismatch("Nonpositive symbol measurement: " + field)
                return number

            point = measured(spec.point, 'point')
            contract = measured(spec.trade_contract_size, 'trade_contract_size')
            if (point != Decimal(1).scaleb(-digits)
                    or (expected['point'] is not None
                        and point != measured(expected['point'], 'profile.point'))
                    or contract != measured(expected['contract_size'], 'profile.contract_size')):
                raise MT5BrokerProfileMismatch("Point or contract size differs from profile.")
            if (spec.currency_margin != expected['margin_currency']
                    or spec.currency_profit != expected['profit_currency']):
                raise MT5BrokerProfileMismatch("Symbol currency differs from profile.")

            spread = spec.spread_float
            chart = spec.chart_mode
            trade = spec.trade_mode
            calc = spec.trade_calc_mode
            if (type(spread) is not bool or type(chart) is not int
                    or type(trade) is not int or type(calc) is not int):
                raise MT5BrokerProfileMismatch("Symbol capabilities have invalid types.")
            if (('FLOATING' if spread else 'FIXED') != expected['spread_mode']
                    or {0: 'BID', 1: 'LAST'}.get(chart) != expected['chart_price']
                    or {0: 'DISABLED', 1: 'LONG_ONLY', 2: 'SHORT_ONLY',
                        3: 'CLOSE_ONLY', 4: 'FULL'}.get(trade) != expected['trade_mode']
                    or {0: 'FOREX', 1: 'FOREX_NO_LEVERAGE'}.get(calc)
                        != expected['calculation_mode']):
                raise MT5BrokerProfileMismatch("Symbol capabilities differ from profile.")
        except MT5BrokerProfileMismatch:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise MT5BrokerProfileMismatch("Incomplete selected terminal or symbol metadata.") from exc

    def is_healthy(self):
        if not self.connected:
            return False

        terminal = mt5.terminal_info()
        healthy = bool(terminal is not None and terminal.connected)
        if healthy and self.broker_profile is not None:
            self._require_profile_binding()
        return healthy

    def connect(self):
        if self.is_healthy():
            return True

        mt5.shutdown()
        self.connected = False

        path = os.environ.get(
            "MT5_TERMINAL_PATH", ""
        ).strip()

        if path:
            if not os.path.isabs(path) or not os.path.isfile(path):
                print(
                    "MT5 INITIALIZATION ERROR: invalid MT5_TERMINAL_PATH",
                    flush=True,
                )
                return False

            ok = mt5.initialize(
                path,
                timeout=MT5_INITIALIZE_TIMEOUT_MS,
            )

        else:
            ok = mt5.initialize(
                timeout=MT5_INITIALIZE_TIMEOUT_MS,
            )

        if ok:
            terminal = mt5.terminal_info()

            if terminal is not None and terminal.connected:
                self.connected = True
                try:
                    self._require_profile_binding()
                except MT5BrokerProfileMismatch:
                    self.disconnect()
                    raise
                return True

            mt5.shutdown()

        print(
            "MT5 INITIALIZATION ERROR:",
            mt5.last_error(),
            flush=True,
        )
        return False

    def disconnect(self):
        try:
            mt5.shutdown()
        finally:
            self.connected = False

    def _check_source_clock(self, symbol):
        if self.clock_policy is not None:
            self.get_price(symbol)
            return
        _check_live_tick(
            mt5.symbol_info_tick(symbol),
            symbol,
        )

    def _policy_quote(self, tick, symbol):
        from backend.data_engine.clock_policy import ClockPolicyError
        policy = self.clock_policy
        now = _utc_now()
        policy.check_active(now)
        if symbol != policy.symbol:
            raise ClockPolicyError("Clock policy symbol mismatch.")
        policy.check_identity(mt5.account_info())
        if tick is None:
            raise ClockPolicyError("Clock-bound quote is unavailable.")
        raw = {key: getattr(tick, key) for key in
               ('time','time_msc','bid','ask','last','volume','flags','volume_real')}
        result = policy.tick(raw)
        policy.check_time(result['time_msc'])
        skew = result['time_msc'] / 1000 - now.timestamp()
        if not -MAX_QUOTE_AGE_SECONDS <= skew <= MAX_FUTURE_SECONDS:
            raise ClockPolicyError("Converted quote does not establish a fresh UTC clock.")
        return {'symbol': symbol, **result}

    def _policy_history(self, rows, start, end, *, rates=False):
        from datetime import timedelta
        from backend.data_engine.clock_policy import ClockPolicyError, instant
        if rows is None:
            raise ClockPolicyError("Clock-bound history request failed.")
        result = []
        for raw in rows:
            row = self.clock_policy.rate(raw) if rates else self.clock_policy.tick(raw)
            timestamp = instant(row['time'] * 1000 if rates else row['time_msc'])
            # MT5 range endpoints are inclusive/second-granular. Filter only that
            # narrow boundary excess; quarantine a different query convention.
            if not start <= timestamp < end:
                if timestamp < start-timedelta(seconds=1) or timestamp >= end+timedelta(seconds=1):
                    raise ClockPolicyError("History response violates the bound clock/query convention.")
                continue
            milliseconds = row['time']*1000 if rates else row['time_msc']
            self.clock_policy.check_time(milliseconds)
            _check_raw_clock_msc(milliseconds, symbol=self.clock_policy.symbol)
            result.append(row)
        return result

    def get_price(self, symbol):
        if not self.connected:
            raise RuntimeError("MT5 not connected")
        self._require_profile_binding(symbol)

        tick = mt5.symbol_info_tick(symbol)

        if self.clock_policy is not None:
            return self._policy_quote(tick, symbol)

        if tick is None:
            print(
                "MT5 PRICE ERROR:",
                mt5.last_error(),
                flush=True,
            )
            return None

        _check_live_tick(tick, symbol)

        return {
            "symbol": symbol,
            "bid": float(tick.bid),
            "ask": float(tick.ask),
            "time": int(tick.time),
            "time_msc": int(tick.time_msc),
        }

    def get_ticks_range(
        self,
        symbol,
        date_from: datetime,
        date_to: datetime,
    ):
        if not self.connected:
            raise RuntimeError("MT5 not connected")
        self._require_profile_binding(symbol)

        start, end = _utc_range(date_from, date_to)

        self._check_source_clock(symbol)

        if self.clock_policy is not None:
            raw_start, raw_end = self.clock_policy.query_range(start, end)
            rows = mt5.copy_ticks_range(symbol, raw_start, raw_end, mt5.COPY_TICKS_ALL)
            return self._policy_history(rows, start, end)

        ticks = mt5.copy_ticks_range(
            symbol,
            start,
            end,
            mt5.COPY_TICKS_ALL,
        )

        if ticks is None:
            print(
                "MT5 TICK HISTORY ERROR:",
                mt5.last_error(),
                flush=True,
            )
            return []

        # Reject the entire batch if any row claims future UTC.
        for tick in ticks:
            _check_raw_clock_msc(
                tick["time_msc"],
                symbol=symbol,
            )

        return ticks

    def get_candles(
        self,
        symbol,
        timeframe,
        date_from=None,
        date_to=None,
    ):
        if not self.connected:
            raise RuntimeError("MT5 not connected")
        self._require_profile_binding(symbol)

        mt5_timeframe = self.TIMEFRAMES.get(timeframe)

        if mt5_timeframe is None:
            raise ValueError(
                f"Unsupported timeframe: {timeframe}"
            )

        if date_from is None or date_to is None:
            raise ValueError(
                "date_from and date_to are required"
            )

        start, end = _utc_range(date_from, date_to)

        self._check_source_clock(symbol)

        if self.clock_policy is not None:
            from backend.data_engine.clock_policy import ClockPolicyError
            if timeframe != 'M5':
                raise ClockPolicyError("Forward clock admission is scoped to M5 bars.")
            raw_start, raw_end = self.clock_policy.query_range(start, end)
            rows = mt5.copy_rates_range(symbol, mt5_timeframe, raw_start, raw_end)
            return self._policy_history(rows, start, end, rates=True)

        rates = mt5.copy_rates_range(
            symbol,
            mt5_timeframe,
            start,
            end,
        )

        if rates is None:
            print(
                "MT5 CANDLE HISTORY ERROR:",
                mt5.last_error(),
                flush=True,
            )
            return []

        for rate in rates:
            _check_raw_clock_msc(
                int(rate["time"]) * 1000,
                symbol=symbol,
            )

        return rates
