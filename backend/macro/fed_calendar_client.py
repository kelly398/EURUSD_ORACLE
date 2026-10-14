
"""F4.5.5a: Strict HTTPS acquisition of one pinned FOMC schedule document.

Acquires current HTTP entity-body bytes and their SHA-256 in memory only.
This is NOT an original-2024-byte attestation, a source-version certificate,
calendar parsing/admission, persistence, replay, or Council registration.

No network occurs on import or construction. The only network entry point is
FedCalendarClient.fetch(). Tests inject an offline opener.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
import ssl
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
    FED_2026_SCHEDULE_URL,
)


MAX_FED_CALENDAR_HTML_BYTES = 1024 * 1024


class FedCalendarAcquisitionError(ValueError):
    """The pinned official HTML response cannot be safely recorded."""


class _RejectRedirects(HTTPRedirectHandler):
    """Fail before following any HTTP 30x response."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(
            req.full_url,
            code,
            "FOMC schedule redirects forbidden",
            headers,
            fp,
        )


@dataclass(frozen=True)
class FedCalendarAcquisitionRecord:
    source_url: str
    response_url: str
    http_status: int
    request_started_at: datetime
    response_completed_at: datetime
    content_type: str
    content_etag: str | None
    content_last_modified: str | None
    entity_body: bytes
    entity_body_sha256: str
    transport_mode: str

    @property
    def first_observed_at(self) -> datetime:
        """Conservative local completion clock, not a trusted 2024 timestamp."""
        return self.response_completed_at

    @property
    def independently_historical_attested(self) -> bool:
        return False

    @property
    def council_calendar_admission_authorized(self) -> bool:
        return False

    @property
    def historical_replay_authorized(self) -> bool:
        return False

    @property
    def persistence_authorized(self) -> bool:
        return False


def _single_header(headers: Message, name: str) -> str | None:
    values = headers.get_all(name, [])

    if len(values) > 1:
        raise FedCalendarAcquisitionError(
            f"Ambiguous repeated FOMC HTTP header: {name}."
        )

    if not values:
        return None

    value = values[0].strip()

    if (
        not value
        or len(value) > 1024
        or "\n" in value
        or "\r" in value
    ):
        raise FedCalendarAcquisitionError(
            f"Invalid FOMC HTTP header: {name}."
        )

    return value


def _checked_clock(clock) -> datetime:
    value = clock()

    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FedCalendarAcquisitionError(
            "Observation clock must be aware datetime."
        )

    return value.astimezone(timezone.utc)


class FedCalendarClient:
    """Read-only GET for the exact 2024 schedule release over verified HTTPS.

    In default mode urllib uses a certificate-checking SSLContext, disables
    proxy use and refuses all redirects. An injected opener is for offline
    tests only, and its transport is explicitly marked UNVERIFIED.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        opener=None,
        clock=None,
    ):
        if (
            type(timeout_seconds) not in (int, float)
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError(
                "timeout_seconds must be finite and positive."
            )

        if (
            opener is not None
            and not callable(getattr(opener, "open", None))
        ):
            raise TypeError(
                "opener must expose callable open()."
            )

        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable.")

        self.timeout_seconds = float(timeout_seconds)

        self._clock = (
            clock
            if clock is not None
            else lambda: datetime.now(timezone.utc)
        )

        self._ssl_context = ssl.create_default_context()

        self._transport_mode = (
            "DEFAULT_VERIFIED_TLS_CONFIGURATION"
            if opener is None
            else "INJECTED_TRANSPORT_UNVERIFIED"
        )

        self._opener = (
            build_opener(
                ProxyHandler({}),
                HTTPSHandler(context=self._ssl_context),
                _RejectRedirects(),
            )
            if opener is None
            else opener
        )

    def fetch(self) -> FedCalendarAcquisitionRecord:
        """Explicitly download one document; do not parse or admit it."""

        request = Request(
            FED_2026_SCHEDULE_URL,
            headers={
                "Accept": "text/html",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
                "User-Agent": (
                    "EURUSD-ORACLE-FOMC-Calendar-Evidence/1.0"
                ),
            },
            method="GET",
        )

        started = _checked_clock(self._clock)

        try:
            with self._opener.open(
                request,
                timeout=self.timeout_seconds,
            ) as response:

                status = response.getcode()

                if status != 200:
                    raise FedCalendarAcquisitionError(
                        "FOMC schedule response must be HTTP 200."
                    )

                final_url = response.geturl()

                if final_url != FED_2026_SCHEDULE_URL:
                    raise FedCalendarAcquisitionError(
                        "FOMC schedule final URL differs from pinned source."
                    )

                headers = response.info()

                content_type = _single_header(
                    headers,
                    "Content-Type",
                )

                if (
                    content_type is None
                    or content_type.split(";")[0].strip().lower()
                    != "text/html"
                ):
                    raise FedCalendarAcquisitionError(
                        "FOMC schedule response requires text/html."
                    )

                encoding = _single_header(
                    headers,
                    "Content-Encoding",
                )

                if (
                    encoding is not None
                    and encoding.lower() != "identity"
                ):
                    raise FedCalendarAcquisitionError(
                        "Compressed FOMC schedule responses are not accepted."
                    )

                content_length = _single_header(
                    headers,
                    "Content-Length",
                )

                if content_length is not None:

                    if not content_length.isdecimal():
                        raise FedCalendarAcquisitionError(
                            "Invalid FOMC Content-Length."
                        )

                    if (
                        int(content_length)
                        > MAX_FED_CALENDAR_HTML_BYTES
                    ):
                        raise FedCalendarAcquisitionError(
                            "FOMC schedule response exceeds byte limit."
                        )

                etag = _single_header(
                    headers,
                    "ETag",
                )

                last_modified = _single_header(
                    headers,
                    "Last-Modified",
                )

                body = response.read(
                    MAX_FED_CALENDAR_HTML_BYTES + 1
                )

                completed = _checked_clock(self._clock)

        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
        ) as exc:
            raise FedCalendarAcquisitionError(
                "Official FOMC schedule HTTPS acquisition failed."
            ) from exc

        if completed < started:
            raise FedCalendarAcquisitionError(
                "FOMC schedule completion precedes request start."
            )

        if completed < FED_2026_SCHEDULE_RELEASE_LABEL_UTC:
            raise FedCalendarAcquisitionError(
                "Observation precedes claimed 2024 schedule release label."
            )

        if type(body) is not bytes:
            raise FedCalendarAcquisitionError(
                "FOMC HTTP body must be bytes."
            )

        if (
            not body
            or len(body) > MAX_FED_CALENDAR_HTML_BYTES
        ):
            raise FedCalendarAcquisitionError(
                "FOMC schedule body empty or exceeds byte limit."
            )

        if (
            content_length is not None
            and len(body) != int(content_length)
        ):
            raise FedCalendarAcquisitionError(
                "FOMC schedule Content-Length mismatch."
            )

        if (
            b"<html" not in body[:8192].lower()
            or b"</html>" not in body.lower()
        ):
            raise FedCalendarAcquisitionError(
                "FOMC schedule body is not a complete HTML document."
            )

        return FedCalendarAcquisitionRecord(
            source_url=FED_2026_SCHEDULE_URL,
            response_url=final_url,
            http_status=status,
            request_started_at=started,
            response_completed_at=completed,
            content_type=content_type,
            content_etag=etag,
            content_last_modified=last_modified,
            entity_body=body,
            entity_body_sha256=sha256(body).hexdigest(),
            transport_mode=self._transport_mode,
        )
