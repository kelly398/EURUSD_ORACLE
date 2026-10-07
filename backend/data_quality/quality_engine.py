from datetime import (
    datetime,
    timedelta,
    timezone
)

from sqlalchemy import func

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    Tick,
    BrokerCandle
)

from backend.candle_engine.timeframe import (
    floor_time
)

from backend.data_engine.mt5_provider import (
    MT5Provider
)

from backend.data_quality.event_storage import (
    DataQualityEventStorage
)


class DataQualityEngine:

    def __init__(
        self,
        symbol="EURUSD",
        timeframe="M5",
        timeframe_minutes=5,
        lookback_hours=6
    ):

        self.symbol = symbol
        self.timeframe = timeframe

        self.timeframe_minutes = (
            timeframe_minutes
        )

        self.lookback_hours = (
            lookback_hours
        )

        self.event_storage = (
            DataQualityEventStorage()
        )

        # Live MT5 connection used only
        # to obtain the authoritative
        # current broker market clock.
        self.provider = MT5Provider()


    def _issue(
        self,
        report,
        check_name,
        severity,
        message
    ):

        report["issues"].append(
            {
                "check": check_name,
                "severity": severity,
                "message": message
            }
        )

        if severity in (
            "WARNING",
            "ERROR",
            "CRITICAL"
        ):

            self.event_storage.save(
                symbol=self.symbol,
                timeframe=self.timeframe,
                check_name=check_name,
                severity=severity,
                message=message
            )


    def run(self):

        report = {

            "symbol":
                self.symbol,

            "timeframe":
                self.timeframe,

            "status":
                "PASS",

            "issues":
                [],

            "bars_checked":
                0,

            "latest_tick":
                None,

            "broker_now":
                None,

            "tick_lag_seconds":
                None,

            "latest_broker_candle":
                None,

            "expected_latest_candle":
                None
        }


        session = SessionLocal()


        try:

            # =====================================
            # 1. DATABASE MARKET STATE
            # =====================================

            latest_tick = (
                session.query(
                    func.max(
                        Tick.timestamp
                    )
                )
                .filter(
                    Tick.symbol
                    == self.symbol
                )
                .scalar()
            )


            latest_bar = (
                session.query(
                    func.max(
                        BrokerCandle.timestamp
                    )
                )
                .filter(
                    BrokerCandle.symbol
                    == self.symbol,

                    BrokerCandle.timeframe
                    == self.timeframe
                )
                .scalar()
            )


            report["latest_tick"] = (
                latest_tick
            )

            report[
                "latest_broker_candle"
            ] = latest_bar


            # =====================================
            # 2. BASIC AVAILABILITY
            # =====================================

            if latest_tick is None:

                self._issue(
                    report,
                    "tick_availability",
                    "CRITICAL",
                    "No EURUSD ticks exist "
                    "in database."
                )


            if latest_bar is None:

                self._issue(
                    report,
                    "bar_availability",
                    "CRITICAL",
                    "No canonical M5 bars exist."
                )

                report["status"] = "FAIL"

                return report


            # =====================================
            # 3. LIVE MT5 BROKER CLOCK
            # =====================================

            broker_now = None


            if self.provider.connect():

                try:

                    current_price = (
                        self.provider.get_price(
                            self.symbol
                        )
                    )


                    if current_price is not None:

                        broker_now = (
                            datetime.fromtimestamp(
                                current_price[
                                    "time_msc"
                                ] / 1000,
                                tz=timezone.utc
                            )
                        )

                finally:

                    self.provider.disconnect()


            if broker_now is None:

                self._issue(
                    report,
                    "broker_clock",
                    "CRITICAL",
                    "Unable to obtain current "
                    "MT5 broker timestamp."
                )

            else:

                report["broker_now"] = (
                    broker_now
                )


                # The current M5 candle is
                # still forming.
                current_boundary = (
                    floor_time(
                        broker_now,
                        self.timeframe_minutes
                    )
                )


                # Therefore the previous
                # boundary is the latest
                # candle allowed to be
                # marked complete.
                expected_latest = (
                    current_boundary
                    - timedelta(
                        minutes=
                        self.timeframe_minutes
                    )
                )


                report[
                    "expected_latest_candle"
                ] = expected_latest


            # =====================================
            # 4. RAW TICK FRESHNESS
            # =====================================

            if (
                broker_now is not None
                and latest_tick is not None
            ):

                tick_lag_seconds = (
                    broker_now
                    - latest_tick
                ).total_seconds()


                report[
                    "tick_lag_seconds"
                ] = tick_lag_seconds


                if tick_lag_seconds > 120:

                    self._issue(
                        report,
                        "tick_freshness",
                        "CRITICAL",
                        (
                            "Raw tick database "
                            f"is "
                            f"{tick_lag_seconds:.1f} "
                            "seconds behind MT5."
                        )
                    )


                elif tick_lag_seconds > 30:

                    self._issue(
                        report,
                        "tick_freshness",
                        "WARNING",
                        (
                            "Raw tick database "
                            f"is "
                            f"{tick_lag_seconds:.1f} "
                            "seconds behind MT5."
                        )
                    )


                elif tick_lag_seconds < -5:

                    self._issue(
                        report,
                        "tick_clock",
                        "CRITICAL",
                        (
                            "Database contains a "
                            "tick timestamp ahead "
                            "of the MT5 broker "
                            "clock."
                        )
                    )


            # =====================================
            # 5. CANONICAL BAR FRESHNESS
            # =====================================

            expected_latest = report[
                "expected_latest_candle"
            ]


            if (
                latest_bar is not None
                and expected_latest is not None
            ):

                if latest_bar < expected_latest:

                    lag_minutes = int(
                        (
                            expected_latest
                            - latest_bar
                        ).total_seconds()
                        / 60
                    )


                    self._issue(
                        report,
                        "bar_freshness",
                        "CRITICAL",
                        (
                            "Canonical M5 data "
                            f"is {lag_minutes} "
                            "minutes behind the "
                            "latest completed "
                            "broker candle."
                        )
                    )


                elif latest_bar > expected_latest:

                    self._issue(
                        report,
                        "future_candle",
                        "CRITICAL",
                        (
                            "A broker candle newer "
                            "than the latest "
                            "completed M5 interval "
                            "is marked complete."
                        )
                    )


            # =====================================
            # 6. LOAD RECENT BARS
            # =====================================

            window_start = (
                latest_bar
                - timedelta(
                    hours=
                    self.lookback_hours
                )
            )


            bars = (
                session.query(
                    BrokerCandle
                )
                .filter(
                    BrokerCandle.symbol
                    == self.symbol,

                    BrokerCandle.timeframe
                    == self.timeframe,

                    BrokerCandle.timestamp
                    >= window_start
                )
                .order_by(
                    BrokerCandle.timestamp.asc()
                )
                .all()
            )


            report["bars_checked"] = (
                len(bars)
            )


            # =====================================
            # 7. BAR STRUCTURE CHECKS
            # =====================================

            for bar in bars:

                # Exact M5 time alignment.
                if (
                    bar.timestamp.minute
                    % self.timeframe_minutes
                    != 0
                    or bar.timestamp.second != 0
                    or bar.timestamp.microsecond != 0
                ):

                    self._issue(
                        report,
                        "time_alignment",
                        "ERROR",
                        (
                            "Misaligned M5 bar: "
                            f"{bar.timestamp}"
                        )
                    )


                # Prices must always
                # be positive.
                if (
                    bar.open <= 0
                    or bar.high <= 0
                    or bar.low <= 0
                    or bar.close <= 0
                ):

                    self._issue(
                        report,
                        "positive_prices",
                        "CRITICAL",
                        (
                            "Non-positive price "
                            f"at {bar.timestamp}"
                        )
                    )


                # High must be greater
                # than or equal to every
                # other OHLC value.
                if (
                    bar.high
                    < max(
                        bar.open,
                        bar.close,
                        bar.low
                    )
                ):

                    self._issue(
                        report,
                        "ohlc_integrity",
                        "CRITICAL",
                        (
                            "Invalid HIGH at "
                            f"{bar.timestamp}"
                        )
                    )


                # Low must be less than
                # or equal to every other
                # OHLC value.
                if (
                    bar.low
                    > min(
                        bar.open,
                        bar.close,
                        bar.high
                    )
                ):

                    self._issue(
                        report,
                        "ohlc_integrity",
                        "CRITICAL",
                        (
                            "Invalid LOW at "
                            f"{bar.timestamp}"
                        )
                    )


                if bar.tick_volume <= 0:

                    self._issue(
                        report,
                        "tick_volume",
                        "WARNING",
                        (
                            "Zero tick volume "
                            f"at {bar.timestamp}"
                        )
                    )


                if not bar.is_complete:

                    self._issue(
                        report,
                        "bar_completion",
                        "CRITICAL",
                        (
                            "Incomplete broker "
                            "bar stored as "
                            f"canonical data at "
                            f"{bar.timestamp}"
                        )
                    )


            # =====================================
            # 8. M5 GAP CHECK
            # =====================================

            expected_gap = timedelta(
                minutes=
                self.timeframe_minutes
            )


            for previous, current in zip(
                bars,
                bars[1:]
            ):

                actual_gap = (
                    current.timestamp
                    - previous.timestamp
                )


                if actual_gap > expected_gap:

                    missing_start = (
                        previous.timestamp
                        + expected_gap
                    )


                    # Only flag the gap as
                    # corrupted if market ticks
                    # actually existed during
                    # the missing interval.
                    ticks_during_gap = (
                        session.query(
                            func.count(
                                Tick.id
                            )
                        )
                        .filter(
                            Tick.symbol
                            == self.symbol,

                            Tick.timestamp
                            >= missing_start,

                            Tick.timestamp
                            < current.timestamp
                        )
                        .scalar()
                    )


                    if ticks_during_gap > 0:

                        self._issue(
                            report,
                            "candle_gap",
                            "CRITICAL",
                            (
                                "Missing canonical "
                                "M5 candle(s) between "
                                f"{previous.timestamp} "
                                "and "
                                f"{current.timestamp}; "
                                f"{ticks_during_gap} "
                                "ticks existed."
                            )
                        )


            # =====================================
            # 9. TICK DUPLICATE PROTECTION
            # =====================================
            #
            # Tick duplication is enforced at the
            # database-storage layer by the
            # non-null UNIQUE constraint:
            #
            #     uq_tick_fingerprint
            #
            # A full COUNT(DISTINCT fingerprint)
            # scan is intentionally not performed
            # in the live quality path because the
            # tick table contains tens of millions
            # of rows and the database constraint
            # already guarantees this invariant.
            #
            # Deep historical integrity audits
            # belong in an offline audit workflow.


            # =====================================
            # 10. FINAL QUALITY STATUS
            # =====================================

            severities = {
                issue["severity"]
                for issue
                in report["issues"]
            }


            if (
                "CRITICAL" in severities
                or "ERROR" in severities
            ):

                report["status"] = "FAIL"


            elif "WARNING" in severities:

                report["status"] = (
                    "WARNING"
                )


            else:

                report["status"] = (
                    "PASS"
                )


            return report


        finally:

            session.close()
            