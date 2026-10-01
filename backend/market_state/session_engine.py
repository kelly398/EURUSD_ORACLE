from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SessionEngineResult:

    session: str

    active_sessions: tuple[str, ...]

    overlap: bool

    market_open: bool

    utc_time: datetime

    london_time: datetime

    new_york_time: datetime

    tokyo_time: datetime


class SessionEngine:

    def __init__(self):

        self.london_tz = ZoneInfo(
            "Europe/London"
        )

        self.new_york_tz = ZoneInfo(
            "America/New_York"
        )

        self.tokyo_tz = ZoneInfo(
            "Asia/Tokyo"
        )


    @staticmethod
    def _validate_datetime(
        as_of
    ):

        if as_of.tzinfo is None:

            raise ValueError(
                "SessionEngine requires "
                "a timezone-aware datetime."
            )


    @staticmethod
    def _within_session(
        local_datetime,
        start_hour,
        end_hour
    ):

        local_time = (
            local_datetime.time()
        )


        start = time(
            hour=start_hour
        )

        end = time(
            hour=end_hour
        )


        return (
            start
            <= local_time
            < end
        )


    def _forex_market_open(
        self,
        new_york_time
    ):

        weekday = (
            new_york_time.weekday()
        )

        local_time = (
            new_york_time.time()
        )


        # Python weekday:
        #
        # Monday    = 0
        # Tuesday   = 1
        # Wednesday = 2
        # Thursday  = 3
        # Friday    = 4
        # Saturday  = 5
        # Sunday    = 6


        # Monday through Thursday
        # are continuously open.
        if weekday in (
            0,
            1,
            2,
            3
        ):

            return True


        # Friday closes at
        # approximately 17:00
        # New York time.
        if weekday == 4:

            return (
                local_time
                < time(17, 0)
            )


        # Saturday is closed.
        if weekday == 5:

            return False


        # Sunday reopens at
        # approximately 17:00
        # New York time.
        if weekday == 6:

            return (
                local_time
                >= time(17, 0)
            )


        return False


    def analyze(
        self,
        as_of: datetime
    ) -> SessionEngineResult:

        self._validate_datetime(
            as_of
        )


        utc_time = (
            as_of.astimezone(
                ZoneInfo("UTC")
            )
        )


        london_time = (
            utc_time.astimezone(
                self.london_tz
            )
        )


        new_york_time = (
            utc_time.astimezone(
                self.new_york_tz
            )
        )


        tokyo_time = (
            utc_time.astimezone(
                self.tokyo_tz
            )
        )


        market_open = (
            self._forex_market_open(
                new_york_time
            )
        )


        if not market_open:

            return SessionEngineResult(

                session="market_closed",

                active_sessions=tuple(),

                overlap=False,

                market_open=False,

                utc_time=utc_time,

                london_time=
                    london_time,

                new_york_time=
                    new_york_time,

                tokyo_time=
                    tokyo_time
            )


        # ==================================
        # SESSION WINDOWS
        # ==================================
        #
        # We define sessions using their
        # LOCAL market clocks.
        #
        # Tokyo:
        # 09:00 - 18:00 JST
        #
        # London:
        # 08:00 - 17:00 London time
        #
        # New York:
        # 08:00 - 17:00 New York time
        #
        # ZoneInfo automatically handles
        # DST for London and New York.
        # ==================================

        tokyo_active = (
            self._within_session(
                tokyo_time,
                9,
                18
            )
        )


        london_active = (
            self._within_session(
                london_time,
                8,
                17
            )
        )


        new_york_active = (
            self._within_session(
                new_york_time,
                8,
                17
            )
        )


        active_sessions = []


        if tokyo_active:

            active_sessions.append(
                "tokyo"
            )


        if london_active:

            active_sessions.append(
                "london"
            )


        if new_york_active:

            active_sessions.append(
                "new_york"
            )


        active_sessions = tuple(
            active_sessions
        )


        # ==================================
        # PRIMARY SESSION LABEL
        # ==================================

        if (
            london_active
            and new_york_active
        ):

            session = (
                "london_new_york_overlap"
            )


        elif (
            tokyo_active
            and london_active
        ):

            session = (
                "tokyo_london_overlap"
            )


        elif new_york_active:

            session = (
                "new_york"
            )


        elif london_active:

            session = (
                "london"
            )


        elif tokyo_active:

            session = (
                "tokyo"
            )


        else:

            session = (
                "off_session"
            )


        overlap = (
            len(active_sessions)
            > 1
        )


        return SessionEngineResult(

            session=session,

            active_sessions=
                active_sessions,

            overlap=overlap,

            market_open=True,

            utc_time=utc_time,

            london_time=
                london_time,

            new_york_time=
                new_york_time,

            tokyo_time=
                tokyo_time
        )
        