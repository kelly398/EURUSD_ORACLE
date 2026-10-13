
"""F2.1: strict official FOMC HTML acquisition, without policy extraction.

Retrieval time is NOT the historical public-release timestamp. The F1 protocol
must validate independent public-release evidence before snapshots are built.
No database/network action occurs at module import or class construction.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, Request, build_opener,
)

import certifi

from backend.macro.fed_rate_protocol import (
    FED_RATE_MEETING_PATH,
    FED_RATE_SOURCE_PATH,
    build_fed_statement_id,
)


FED_SOURCE_MAX_BYTES = 1_048_576
FED_SOURCE_ROLES = ("statement", "implementation", "meeting")


class FedSourceError(ValueError):
    """Source identity, HTTP integrity, or transport cannot be validated."""


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FedSourceError("HTTP redirects are prohibited for official documents.")


@dataclass(frozen=True)
class FedSourceDocument:
    role: str
    url: str
    html: str
    raw_bytes: bytes
    content_sha256: str
    fetched_at: datetime


@dataclass(frozen=True)
class FedSourceBundle:
    statement: FedSourceDocument
    implementation: FedSourceDocument
    meeting: FedSourceDocument


class FedRateClient:
    """Download three official documents for a single FOMC decision.

    All URL identities come from a validated statement URL. No alternate hosts,
    redirects, fallback sources, implicit retries, or unchecked TLS are allowed.
    An injectable opener/clock lets deterministic tests run without networking.
    """

    def __init__(self, timeout_seconds=20, opener=None, utc_now=None):
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not (0 < timeout_seconds <= 120)
        ):
            raise ValueError("timeout_seconds must be in (0, 120].")
        if opener is not None and not callable(opener):
            raise TypeError("opener must be callable.")
        if utc_now is not None and not callable(utc_now):
            raise TypeError("utc_now must be callable.")

        self.timeout_seconds = float(timeout_seconds)
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

        if (
            not self.ssl_context.check_hostname
            or self.ssl_context.verify_mode != ssl.CERT_REQUIRED
        ):
            raise RuntimeError("Verified TLS configuration is mandatory.")

        # Unlike urlopen(), this transport blocks redirects before following them.
        self._http_opener = build_opener(
            HTTPSHandler(context=self.ssl_context), _RejectRedirect()
        )
        self.opener = opener if opener is not None else self._verified_open

    def _verified_open(self, request, *, timeout, context):
        if context is not self.ssl_context:
            raise FedSourceError("Request must use this client verified TLS context.")
        return self._http_opener.open(request, timeout=timeout)

    @staticmethod
    def build_urls(statement_url: str) -> tuple[str, str, str]:
        statement_id = build_fed_statement_id(statement_url)
        # The statement ID embeds its release date: monetaryYYYYMMDDa.
        date_token = statement_id[8:16]
        try:
            datetime.strptime(date_token, "%Y%m%d")
        except ValueError as exc:
            raise FedSourceError("Invalid statement release date.") from exc
        return (
            statement_url,
            FED_RATE_SOURCE_PATH + statement_id + "1.htm",
            FED_RATE_MEETING_PATH + "fomcpresconf" + date_token + ".htm",
        )

    def _utc_fetch_time(self) -> datetime:
        stamp = self.utc_now()
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise FedSourceError("Fetch clock must return an aware datetime.")
        if stamp.utcoffset() is None:
            raise FedSourceError("Fetch clock has no UTC offset.")
        return stamp.astimezone(timezone.utc)

    def _fetch(self, role: str, url: str) -> FedSourceDocument:
        if role not in FED_SOURCE_ROLES:
            raise FedSourceError("Unsupported source document role.")
        request = Request(
            url,
            headers={
                "Accept": "text/html",
                "Accept-Encoding": "identity",
                "User-Agent": "EURUSD_ORACLE/1.0",
            },
            method="GET",
        )
        try:
            with self.opener(
                request,
                timeout=self.timeout_seconds,
                context=self.ssl_context,
            ) as response:
                if response.geturl() != url:
                    raise FedSourceError("HTTP redirect or URL mismatch rejected.")
                if getattr(response, "status", None) != 200:
                    raise FedSourceError("Source did not return HTTP 200.")
                content_type = response.headers.get("Content-Type", "")
                mime_type = content_type.split(";", 1)[0].strip().lower()
                if mime_type != "text/html":
                    raise FedSourceError("Source did not return HTML.")
                content_encoding = response.headers.get(
                    "Content-Encoding", "identity"
                ).strip().lower()
                if content_encoding != "identity":
                    raise FedSourceError("Unexpected compressed response rejected.")
                raw = response.read(FED_SOURCE_MAX_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            raise FedSourceError("Official Fed source request failed.") from exc

        if not isinstance(raw, bytes) or not raw or len(raw) > FED_SOURCE_MAX_BYTES:
            raise FedSourceError("Source payload absent, invalid, or oversized.")
        if b"\x00" in raw:
            raise FedSourceError("NUL bytes in HTML payload.")
        try:
            html = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise FedSourceError("Source payload is not valid UTF-8.") from exc

        return FedSourceDocument(
            role=role,
            url=url,
            html=html,
            raw_bytes=raw,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            fetched_at=self._utc_fetch_time(),
        )

    def fetch_original_documents(self, statement_url: str) -> FedSourceBundle:
        """Fetch all three documents or raise; never return a partial bundle.

        The caller may not treat fetched_at/HTTP metadata as available_at.
        Historical publication times still require F1 document validation.
        """
        statement_url, implementation_url, meeting_url = self.build_urls(
            statement_url
        )
        return FedSourceBundle(
            statement=self._fetch("statement", statement_url),
            implementation=self._fetch("implementation", implementation_url),
            meeting=self._fetch("meeting", meeting_url),
        )
