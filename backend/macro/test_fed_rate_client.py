
"""F2.2: deterministic, offline tests for the official Fed HTML collector.

Run from the activated project virtual environment and project root:
    python -m backend.macro.test_fed_rate_client

All HTTP responses are simulated. No database, broker, live network, or
reserved HMM evaluation is accessed.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import ssl
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request

from backend.macro.fed_rate_client import (
    FED_SOURCE_MAX_BYTES,
    FED_SOURCE_ROLES,
    FedRateClient,
    FedSourceError,
    _RejectRedirect,
)
from backend.macro.fed_rate_protocol import FedRateProtocolError


STATEMENT_URL = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20260916a.htm"
)
IMPLEMENTATION_URL = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20260916a1.htm"
)
MEETING_URL = (
    "https://www.federalreserve.gov/monetarypolicy/"
    "fomcpresconf20260916.htm"
)
URLS = (STATEMENT_URL, IMPLEMENTATION_URL, MEETING_URL)

DOCUMENTS = {
    STATEMENT_URL: b"<!doctype html><html><body>FOMC statement</body></html>",
    IMPLEMENTATION_URL: b"<html><body>Implementation note</body></html>",
    MEETING_URL: b"<html><body>FOMC meeting index</body></html>",
}

FIXED_CLOCK = datetime(
    2026, 10, 13, 22, 30, tzinfo=timezone(timedelta(hours=3))
)
EXPECTED_UTC = datetime(2026, 10, 13, 19, 30, tzinfo=timezone.utc)


class FakeResponse(BytesIO):
    """Minimal context-managed HTTPResponse substitute."""

    def __init__(
        self,
        url,
        data,
        *,
        status=200,
        content_type="text/html; charset=utf-8",
        content_encoding="identity",
        final_url=None,
    ):
        super().__init__(data)
        self.status = status
        self.headers = {
            "Content-Type": content_type,
            "Content-Encoding": content_encoding,
        }
        self._final_url = url if final_url is None else final_url

    def geturl(self):
        return self._final_url


class FakeTransport:
    """Inject responses and collect all requests; never uses the network."""

    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def __call__(self, request, *, timeout, context):
        self.calls.append((request, timeout, context))
        url = request.full_url
        result = self.overrides.get(url)
        if isinstance(result, Exception):
            raise result
        if result is not None:
            return result
        return FakeResponse(url, DOCUMENTS[url])


class FedRateClientTests(unittest.TestCase):
    def make_client(self, overrides=None, utc_now=None, timeout=20):
        transport = FakeTransport(overrides)
        client = FedRateClient(
            opener=transport,
            utc_now=utc_now or (lambda: FIXED_CLOCK),
            timeout_seconds=timeout,
        )
        return client, transport

    def test_01_official_url_construction(self):
        self.assertEqual(FedRateClient.build_urls(STATEMENT_URL), URLS)

        invalid = (
            STATEMENT_URL.replace("https:", "http:"),
            STATEMENT_URL.replace("www.federalreserve.gov", "other.example"),
            STATEMENT_URL + "?revision=1",
            STATEMENT_URL.replace("a.htm", "b.htm"),
            STATEMENT_URL.replace("20260916", "20260230"),
        )
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises((FedRateProtocolError, FedSourceError)):
                    FedRateClient.build_urls(url)

    def test_02_no_network_at_construction(self):
        client, transport = self.make_client()
        self.assertEqual(transport.calls, [])
        self.assertEqual(client.timeout_seconds, 20.0)

    def test_03_verified_tls_and_request_headers(self):
        client, transport = self.make_client(timeout=7)
        self.assertTrue(client.ssl_context.check_hostname)
        self.assertEqual(client.ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(client.timeout_seconds, 7.0)
        client.fetch_original_documents(STATEMENT_URL)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(
            tuple(request.full_url for request, _, _ in transport.calls),
            URLS,
        )
        for request, timeout, context in transport.calls:
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Accept"), "text/html")
            self.assertEqual(request.get_header("Accept-encoding"), "identity")
            self.assertEqual(timeout, 7.0)
            self.assertIs(context, client.ssl_context)

    def test_04_complete_bundle_and_raw_fingerprints(self):
        client, _ = self.make_client()
        bundle = client.fetch_original_documents(STATEMENT_URL)
        docs = (bundle.statement, bundle.implementation, bundle.meeting)
        self.assertEqual(tuple(doc.role for doc in docs), FED_SOURCE_ROLES)
        self.assertEqual(tuple(doc.url for doc in docs), URLS)
        for doc in docs:
            self.assertEqual(doc.raw_bytes, DOCUMENTS[doc.url])
            self.assertEqual(doc.html, doc.raw_bytes.decode("utf-8"))
            self.assertEqual(
                doc.content_sha256,
                hashlib.sha256(doc.raw_bytes).hexdigest(),
            )
            self.assertEqual(doc.fetched_at, EXPECTED_UTC)
            self.assertIs(doc.fetched_at.tzinfo, timezone.utc)
        with self.assertRaises(FrozenInstanceError):
            bundle.statement.role = "changed"
        with self.assertRaises(FrozenInstanceError):
            bundle.meeting = None

    def test_05_http_status_redirect_and_url_integrity(self):
        examples = (
            FakeResponse(STATEMENT_URL, b"<html>x</html>", status=301),
            FakeResponse(STATEMENT_URL, b"<html>x</html>", status=503),
            FakeResponse(
                STATEMENT_URL,
                b"<html>x</html>",
                final_url="https://untrusted.example/page.htm",
            ),
        )
        for response in examples:
            with self.subTest(status=response.status, final=response.geturl()):
                client, transport = self.make_client({STATEMENT_URL: response})
                with self.assertRaises(FedSourceError):
                    client.fetch_original_documents(STATEMENT_URL)
                self.assertEqual(len(transport.calls), 1)

    def test_06_http_redirect_handler_is_fail_closed(self):
        handler = _RejectRedirect()
        with self.assertRaises(FedSourceError):
            handler.redirect_request(
                Request(STATEMENT_URL), None, 302, "Found", {},
                "https://other.example/redirect",
            )

    def test_07_content_type_and_encoding_rejection(self):
        examples = (
            FakeResponse(STATEMENT_URL, b"{}", content_type="application/json"),
            FakeResponse(STATEMENT_URL, b"<html>x</html>", content_type=""),
            FakeResponse(
                STATEMENT_URL, b"gzip-bytes", content_encoding="gzip"
            ),
        )
        for response in examples:
            with self.subTest(headers=response.headers):
                client, _ = self.make_client({STATEMENT_URL: response})
                with self.assertRaises(FedSourceError):
                    client.fetch_original_documents(STATEMENT_URL)

    def test_08_empty_oversized_nul_and_non_utf8_payloads(self):
        bad = (b"", b"X" * (FED_SOURCE_MAX_BYTES + 1), b"a\x00b", b"\xff")
        for data in bad:
            with self.subTest(length=len(data)):
                client, _ = self.make_client({
                    STATEMENT_URL: FakeResponse(STATEMENT_URL, data)
                })
                with self.assertRaises(FedSourceError):
                    client.fetch_original_documents(STATEMENT_URL)

    def test_09_exact_size_limit_and_utf8_bom(self):
        exact = b"<html>" + b"a" * (FED_SOURCE_MAX_BYTES - 13) + b"</html>"
        self.assertEqual(len(exact), FED_SOURCE_MAX_BYTES)
        for data in (exact, b"\xef\xbb\xbf<html>ok</html>"):
            with self.subTest(length=len(data)):
                client, _ = self.make_client({
                    STATEMENT_URL: FakeResponse(STATEMENT_URL, data)
                })
                bundle = client.fetch_original_documents(STATEMENT_URL)
                self.assertEqual(bundle.statement.raw_bytes, data)
                self.assertTrue(bundle.statement.html.startswith("<html>"))
                self.assertEqual(
                    bundle.statement.content_sha256,
                    hashlib.sha256(data).hexdigest(),
                )

    def test_10_network_exceptions_are_wrapped(self):
        exceptions = (
            HTTPError(STATEMENT_URL, 503, "Unavailable", {}, None),
            URLError("Network down"),
            TimeoutError("Timed out"),
            ssl.SSLError("TLS certificate failure"),
            OSError("Socket failure"),
        )
        for error in exceptions:
            with self.subTest(error=type(error).__name__):
                client, transport = self.make_client({STATEMENT_URL: error})
                with self.assertRaises(FedSourceError) as caught:
                    client.fetch_original_documents(STATEMENT_URL)
                self.assertIs(caught.exception.__cause__, error)
                self.assertEqual(len(transport.calls), 1)

    def test_11_partial_bundles_are_never_returned(self):
        client, transport = self.make_client({
            IMPLEMENTATION_URL: URLError("Missing implementation note")
        })
        with self.assertRaises(FedSourceError):
            client.fetch_original_documents(STATEMENT_URL)
        self.assertEqual(
            tuple(call[0].full_url for call in transport.calls),
            URLS[:2],
        )

    def test_12_clock_provenance_must_be_aware(self):
        for bad_clock in (
            lambda: datetime(2026, 10, 13, 19, 30),
            lambda: "2026-10-13T19:30:00Z",
        ):
            with self.subTest(clock=bad_clock()):
                client, _ = self.make_client(utc_now=bad_clock)
                with self.assertRaises(FedSourceError):
                    client.fetch_original_documents(STATEMENT_URL)

    def test_13_constructor_configuration_rejection(self):
        for invalid in (0, -1, 121, True, "20", float("nan"), float("inf")):
            with self.subTest(timeout=invalid):
                with self.assertRaises(ValueError):
                    FedRateClient(timeout_seconds=invalid)
        with self.assertRaises(TypeError):
            FedRateClient(opener=object())
        with self.assertRaises(TypeError):
            FedRateClient(utc_now=object())

    def test_14_invalid_role_rejected_before_network(self):
        client, transport = self.make_client()
        with self.assertRaises(FedSourceError):
            client._fetch("unverified_source", STATEMENT_URL)
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
