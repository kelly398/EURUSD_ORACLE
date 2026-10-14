
"""F3.4: read-only, fail-closed FRED/ALFRED v1 vintage acquisition.

The request specifies a SINGLE effective-date observation for one
vintage date for each of the two FOMC target-range series.

A verified HTTPS response is current evidence of an ALFRED query
result, not historical intraday attestation.

No database, broker, or replay writes.
An API key is never returned or logged.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
import re
import ssl

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

import certifi

from backend.macro.fed_rate_protocol import (
    parse_fed_rate_percent,
)

from backend.macro.fed_rate_vintage_protocol import (
    ALFRED_RATE_SERIES,
    FedAlfredVintageObservation,
)


ALFRED_API_ENDPOINT = (
    "https://api.stlouisfed.org/fred/series/observations"
)

MAX_ALFRED_RESPONSE_BYTES = 262_144

_API_KEY_PATTERN = re.compile(
    r"[a-z0-9]{32}\Z",
    re.ASCII,
)

_ISO_DATE_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z",
    re.ASCII,
)

_DECIMAL_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z",
    re.ASCII,
)


class FedAlfredClientError(ValueError):
    """The request or response failed a provenance check."""


class _NoRedirect(HTTPRedirectHandler):

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        raise FedAlfredClientError(
            "ALFRED redirects are forbidden."
        )


@dataclass(frozen=True)
class FedAlfredSourceRecord:
    """One HTTPS download with independently rehashable bytes.

    endpoint deliberately excludes query parameters and credentials.

    An observation is not certified as historically available intraday.
    """

    endpoint: str
    raw_response: bytes
    observation: FedAlfredVintageObservation

    @property
    def response_sha256(self) -> str:
        return hashlib.sha256(
            self.raw_response
        ).hexdigest()

    @property
    def persistence_permitted(self) -> bool:
        return False


@dataclass(frozen=True)
class FedAlfredSourcePair:
    """The complete lower/upper source acquisition."""

    lower: FedAlfredSourceRecord
    upper: FedAlfredSourceRecord

    @property
    def observations(self) -> tuple[
        FedAlfredVintageObservation,
        FedAlfredVintageObservation,
    ]:
        return (
            self.lower.observation,
            self.upper.observation,
        )

    @property
    def persistence_permitted(self) -> bool:
        return False


def _strict_date(value: object, name: str) -> date:
    if (
        type(value) is not str
        or not _ISO_DATE_PATTERN.fullmatch(value)
    ):
        raise FedAlfredClientError(
            f"Invalid {name} date format."
        )

    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FedAlfredClientError(
            f"Invalid {name} date."
        ) from None


def _unique_json_keys(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise FedAlfredClientError(
                "Duplicate ALFRED JSON field."
            )
        result[key] = value

    return result


def _reject_json_constant(value):
    raise FedAlfredClientError(
        "Nonfinite ALFRED JSON number."
    )


def _utc_timestamp(value) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
    ):
        raise FedAlfredClientError(
            "Fetch clock must be timezone-aware."
        )

    try:
        if value.utcoffset() is None:
            raise ValueError("Missing offset")

        return value.astimezone(timezone.utc)

    except (TypeError, ValueError, OverflowError):
        raise FedAlfredClientError(
            "Invalid fetch clock."
        ) from None


class FedAlfredClient:
    """Acquire both target-range limits through the official API.

    An injected opener is supported for deterministic offline tests.

    The default opener uses certifi-verified TLS and refuses redirects.

    A registered FRED_API_KEY must be supplied through the environment
    or explicitly at construction.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 20.0,
        opener=None,
        clock=None,
    ):
        key = (
            os.environ.get("FRED_API_KEY")
            if api_key is None
            else api_key
        )

        if (
            not isinstance(key, str)
            or not _API_KEY_PATTERN.fullmatch(key)
        ):
            raise FedAlfredClientError(
                "A registered 32-character FRED_API_KEY is required."
            )

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(
                timeout_seconds, (float, int)
            )
            or not 0 < timeout_seconds <= 120
        ):
            raise FedAlfredClientError(
                "Invalid ALFRED request timeout."
            )

        if opener is not None and not callable(opener):
            raise TypeError(
                "opener must be callable."
            )

        if clock is not None and not callable(clock):
            raise TypeError(
                "clock must be callable."
            )

        self._api_key = key
        self._timeout = float(timeout_seconds)

        self._clock = (
            clock
            if clock is not None
            else lambda: datetime.now(timezone.utc)
        )

        if opener is None:
            context = ssl.create_default_context(
                cafile=certifi.where()
            )

            self._open = build_opener(
                HTTPSHandler(context=context),
                _NoRedirect(),
            ).open
        else:
            self._open = opener

    @staticmethod
    def _check_dates(
        observation_date: date,
        vintage_date: date,
    ):
        if (
            type(observation_date) is not date
            or type(vintage_date) is not date
        ):
            raise FedAlfredClientError(
                "ALFRED dates must be date objects."
            )

        if vintage_date < observation_date:
            raise FedAlfredClientError(
                "ALFRED effective-date vintage "
                "cannot predate observation."
            )

    def _request(
        self,
        series_id: str,
        observation_date: date,
        vintage_date: date,
    ):
        if series_id not in ALFRED_RATE_SERIES:
            raise FedAlfredClientError(
                "Unsupported ALFRED policy-rate series."
            )

        self._check_dates(
            observation_date,
            vintage_date,
        )

        params = (
            ("series_id", series_id),
            (
                "realtime_start",
                vintage_date.isoformat(),
            ),
            (
                "realtime_end",
                vintage_date.isoformat(),
            ),
            (
                "observation_start",
                observation_date.isoformat(),
            ),
            (
                "observation_end",
                observation_date.isoformat(),
            ),
            ("units", "lin"),
            ("output_type", "1"),
            ("file_type", "json"),
            ("limit", "1"),
            ("offset", "0"),
            ("api_key", self._api_key),
        )

        return Request(
            ALFRED_API_ENDPOINT
            + "?"
            + urlencode(params),
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": (
                    "EURUSD-ORACLE-FED-Seat25/1.0"
                ),
            },
            method="GET",
        )

    @staticmethod
    def _parse_response(
        body,
        series_id,
        observation_date,
        vintage_date,
        fetched_at,
    ):
        try:
            content = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_unique_json_keys,
                parse_constant=_reject_json_constant,
            )

        except FedAlfredClientError:
            raise

        except (
            UnicodeError,
            ValueError,
            RecursionError,
        ):
            raise FedAlfredClientError(
                "Invalid ALFRED JSON response."
            ) from None

        if not isinstance(content, dict):
            raise FedAlfredClientError(
                "ALFRED payload must be an object."
            )

        vintage = vintage_date.isoformat()
        effective = observation_date.isoformat()

        if (
            content.get("realtime_start") != vintage
            or content.get("realtime_end") != vintage
            or content.get("observation_start") != effective
            or content.get("observation_end") != effective
            or content.get("file_type") != "json"
            or content.get("units") != "lin"
            or type(content.get("output_type")) is not int
            or content["output_type"] != 1
            or type(content.get("count")) is not int
            or content["count"] != 1
            or type(content.get("offset")) is not int
            or content["offset"] != 0
            or type(content.get("limit")) is not int
            or content["limit"] != 1
        ):
            raise FedAlfredClientError(
                "ALFRED response metadata mismatch."
            )

        if (
            "series_id" in content
            and content["series_id"] != series_id
        ):
            raise FedAlfredClientError(
                "ALFRED returned wrong series identity."
            )

        rows = content.get("observations")

        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise FedAlfredClientError(
                "Expected exactly one ALFRED observation."
            )

        row = rows[0]

        if (
            _strict_date(
                row.get("date"),
                "observation",
            )
            != observation_date
        ):
            raise FedAlfredClientError(
                "ALFRED observation date mismatch."
            )

        start = _strict_date(
            row.get("realtime_start"),
            "realtime_start",
        )

        end = _strict_date(
            row.get("realtime_end"),
            "realtime_end",
        )

        if not start <= vintage_date <= end:
            raise FedAlfredClientError(
                "ALFRED realtime window excludes vintage."
            )

        value = row.get("value")

        if (
            type(value) is not str
            or not _DECIMAL_PATTERN.fullmatch(value)
        ):
            raise FedAlfredClientError(
                "Invalid ALFRED policy-rate value."
            )

        try:
            parse_fed_rate_percent(value)

        except (ValueError, TypeError):
            raise FedAlfredClientError(
                "Invalid ALFRED policy-rate value."
            ) from None

        return FedAlfredVintageObservation(
            series_id=series_id,
            observation_date=observation_date,
            vintage_date=vintage_date,
            realtime_start=start,
            realtime_end=end,
            value_percent=value,
            response_sha256=hashlib.sha256(
                body
            ).hexdigest(),
            fetched_at=fetched_at,
        )

    def _download_one(
        self,
        series_id: str,
        observation_date: date,
        vintage_date: date,
    ) -> FedAlfredSourceRecord:
        request = self._request(
            series_id,
            observation_date,
            vintage_date,
        )

        declared_length = None

        try:
            with self._open(
                request,
                timeout=self._timeout,
            ) as response:
                if getattr(
                    response, "status", None
                ) != 200:
                    raise FedAlfredClientError(
                        "ALFRED returned non-200 status."
                    )

                if response.geturl() != request.full_url:
                    raise FedAlfredClientError(
                        "ALFRED final URL changed."
                    )

                headers = response.headers

                content_type = headers.get(
                    "Content-Type",
                    "",
                )

                if (
                    content_type.split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                ):
                    raise FedAlfredClientError(
                        "ALFRED did not return JSON MIME type."
                    )

                if "charset=" in content_type.lower():
                    charset = (
                        content_type.lower()
                        .split("charset=", 1)[1]
                        .split(";", 1)[0]
                        .strip()
                        .strip('"')
                    )

                    if charset not in ("utf-8", "utf8"):
                        raise FedAlfredClientError(
                            "Unsupported ALFRED charset."
                        )

                if (
                    headers.get(
                        "Content-Encoding",
                        "identity",
                    ).lower()
                    != "identity"
                ):
                    raise FedAlfredClientError(
                        "Compressed ALFRED responses "
                        "are forbidden."
                    )

                length = headers.get(
                    "Content-Length"
                )

                if length is not None:
                    if (
                        not length.isascii()
                        or not length.isdecimal()
                        or int(length)
                        > MAX_ALFRED_RESPONSE_BYTES
                    ):
                        raise FedAlfredClientError(
                            "Invalid ALFRED response length."
                        )

                    declared_length = int(length)

                body = response.read(
                    MAX_ALFRED_RESPONSE_BYTES + 1
                )

        except FedAlfredClientError:
            raise

        except (
            HTTPError,
            URLError,
            OSError,
            TimeoutError,
        ):
            # urllib exceptions can contain the API key URL.
            # Do not include exception details in this error.
            raise FedAlfredClientError(
                "ALFRED HTTPS request failed."
            ) from None

        if (
            not isinstance(body, bytes)
            or not body
            or len(body) > MAX_ALFRED_RESPONSE_BYTES
            or b"\x00" in body
        ):
            raise FedAlfredClientError(
                "Invalid or oversized ALFRED response body."
            )

        if (
            declared_length is not None
            and declared_length != len(body)
        ):
            raise FedAlfredClientError(
                "ALFRED response length mismatch."
            )

        fetched_at = _utc_timestamp(
            self._clock()
        )

        if fetched_at.date() < vintage_date:
            raise FedAlfredClientError(
                "ALFRED vintage is later than fetch clock."
            )

        observation = self._parse_response(
            body,
            series_id,
            observation_date,
            vintage_date,
            fetched_at,
        )

        return FedAlfredSourceRecord(
            endpoint=ALFRED_API_ENDPOINT,
            raw_response=body,
            observation=observation,
        )

    def fetch_pair(
        self,
        observation_date: date,
        vintage_date: date,
    ) -> FedAlfredSourcePair:
        """Return both source records or fail without a partial pair."""

        self._check_dates(
            observation_date,
            vintage_date,
        )

        lower = self._download_one(
            ALFRED_RATE_SERIES[0],
            observation_date,
            vintage_date,
        )

        upper = self._download_one(
            ALFRED_RATE_SERIES[1],
            observation_date,
            vintage_date,
        )

        if (
            upper.observation.fetched_at
            < lower.observation.fetched_at
        ):
            raise FedAlfredClientError(
                "ALFRED fetch clocks are out of order."
            )

        if (
            parse_fed_rate_percent(
                lower.observation.value_percent
            )
            > parse_fed_rate_percent(
                upper.observation.value_percent
            )
        ):
            raise FedAlfredClientError(
                "ALFRED lower target exceeds upper target."
            )

        return FedAlfredSourcePair(
            lower=lower,
            upper=upper,
        )
