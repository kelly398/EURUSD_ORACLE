
"""F4.5.5a: Fully offline source-acquisition tests (no HTTP calls)."""

from datetime import datetime, timedelta, timezone
from email.message import Message
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request
import ssl
import unittest

from backend.macro.fed_calendar_client import (
    FedCalendarClient,
    FedCalendarAcquisitionError,
    MAX_FED_CALENDAR_HTML_BYTES,
    _RejectRedirects,
)
from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULE_URL,
)


START = datetime(
    2026, 10, 14, 12, 0,
    tzinfo=timezone.utc,
)

END = START + timedelta(seconds=2)

BODY = (
    b'<!doctype html><html lang="en">'
    b'<head><title>Fed</title></head>'
    b'<body>FOMC tentative 2026 schedule</body></html>'
)


class FakeResponse:

    def __init__(
        self,
        *,
        body=BODY,
        code=200,
        url=FED_2026_SCHEDULE_URL,
        extra_headers=None,
    ):
        self.body = body
        self.code = code
        self.url = url
        self.headers = Message()

        self.headers.add_header(
            "Content-Type",
            "text/html; charset=utf-8",
        )

        if extra_headers:
            for key, value in extra_headers.items():

                if key == "Content-Type":
                    del self.headers[key]

                self.headers.add_header(key, value)

        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.code

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def read(self, limit):
        self.read_limit = limit
        return self.body[:limit]


class FakeOpener:

    def __init__(self, response=None, error=None):
        self.response = (
            response
            if response is not None
            else FakeResponse()
        )
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout

        if self.error:
            raise self.error

        return self.response


def make_client(
    *,
    response=None,
    error=None,
    clock=None,
):
    opener = FakeOpener(
        response=response,
        error=error,
    )

    times = iter((START, END))

    client = FedCalendarClient(
        opener=opener,
        clock=(
            clock
            if clock is not None
            else lambda: next(times)
        ),
    )

    return client, opener


class FedCalendarClientTests(unittest.TestCase):

    def test_01_capture_raw_bytes_hash_and_observation(self):
        client, _ = make_client()
        record = client.fetch()

        self.assertEqual(record.entity_body, BODY)

        self.assertEqual(
            record.entity_body_sha256,
            sha256(BODY).hexdigest(),
        )

        self.assertEqual(
            record.first_observed_at,
            END,
        )

        self.assertEqual(
            record.request_started_at,
            START,
        )

        self.assertEqual(
            record.response_completed_at,
            END,
        )

    def test_02_request_pins_exact_official_url_and_headers(self):
        client, opener = make_client()
        record = client.fetch()

        self.assertEqual(
            opener.request.full_url,
            FED_2026_SCHEDULE_URL,
        )

        self.assertEqual(
            opener.request.get_method(),
            "GET",
        )

        self.assertEqual(
            opener.request.get_header("Accept-encoding"),
            "identity",
        )

        self.assertEqual(opener.timeout, 20)

        self.assertEqual(
            record.response_url,
            FED_2026_SCHEDULE_URL,
        )

    def test_03_transport_is_explicitly_unverified_when_injected(self):
        record = make_client()[0].fetch()

        self.assertEqual(
            record.transport_mode,
            "INJECTED_TRANSPORT_UNVERIFIED",
        )

        self.assertFalse(
            record.independently_historical_attested
        )
        self.assertFalse(
            record.council_calendar_admission_authorized
        )
        self.assertFalse(
            record.historical_replay_authorized
        )
        self.assertFalse(
            record.persistence_authorized
        )

    def test_04_headers_etag_and_last_modified_are_only_metadata(self):
        response = FakeResponse(
            extra_headers={
                "ETag": '"example-etag"',
                "Last-Modified": (
                    "Fri, 09 Aug 2024 17:30:00 GMT"
                ),
            }
        )

        record = make_client(
            response=response
        )[0].fetch()

        self.assertEqual(
            record.content_etag,
            '"example-etag"',
        )

        self.assertIn(
            "2024",
            record.content_last_modified,
        )

        self.assertFalse(
            record.independently_historical_attested
        )

    def test_05_byte_changes_change_digest_without_admission(self):
        altered = BODY.replace(
            b"tentative",
            b"revised",
        )

        one = make_client()[0].fetch()

        two = make_client(
            response=FakeResponse(body=altered)
        )[0].fetch()

        self.assertNotEqual(
            one.entity_body_sha256,
            two.entity_body_sha256,
        )

        self.assertFalse(
            two.historical_replay_authorized
        )

    def test_06_wrong_url_rejected(self):
        response = FakeResponse(
            url="https://www.federalreserve.gov/other"
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "final URL",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_07_redirect_status_rejected(self):
        response = FakeResponse(code=302)

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "HTTP 200",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_08_301_handler_raises_without_following(self):
        handler = _RejectRedirects()
        req = Request(FED_2026_SCHEDULE_URL)

        with self.assertRaises(HTTPError):
            handler.redirect_request(
                req,
                None,
                301,
                "Moved",
                Message(),
                "https://evil.invalid/",
            )

    def test_09_http_error_is_wrapped(self):
        error = HTTPError(
            FED_2026_SCHEDULE_URL,
            429,
            "rate limited",
            Message(),
            None,
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "HTTPS acquisition",
        ):
            make_client(
                error=error
            )[0].fetch()

    def test_10_network_error_is_wrapped(self):
        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "HTTPS acquisition",
        ):
            make_client(
                error=URLError("offline")
            )[0].fetch()

    def test_11_non_html_content_type_rejected(self):
        response = FakeResponse(
            extra_headers={
                "Content-Type": "application/json"
            }
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "text/html",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_12_missing_content_type_rejected(self):
        response = FakeResponse()
        del response.headers["Content-Type"]

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "text/html",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_13_compressed_entity_rejected(self):
        response = FakeResponse(
            extra_headers={
                "Content-Encoding": "gzip"
            }
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "Compressed",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_14_body_larger_than_limit_rejected(self):
        body = (
            BODY
            + b"X" * MAX_FED_CALENDAR_HTML_BYTES
        )

        response = FakeResponse(body=body)

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "byte limit",
        ):
            make_client(
                response=response
            )[0].fetch()

        self.assertEqual(
            response.read_limit,
            MAX_FED_CALENDAR_HTML_BYTES + 1,
        )

    def test_15_declared_oversized_length_rejected_before_read(self):
        response = FakeResponse(
            extra_headers={
                "Content-Length": str(
                    MAX_FED_CALENDAR_HTML_BYTES + 1
                )
            }
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "byte limit",
        ):
            make_client(
                response=response
            )[0].fetch()

        self.assertIsNone(
            response.read_limit
        )

    def test_16_declared_length_mismatch_rejected(self):
        response = FakeResponse(
            extra_headers={
                "Content-Length": str(len(BODY) + 1)
            }
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "mismatch",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_17_invalid_length_rejected(self):
        response = FakeResponse(
            extra_headers={
                "Content-Length": "broken"
            }
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "Content-Length",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_18_empty_or_not_html_rejected(self):
        for body in (
            b"",
            b"no html",
            b"<html><body>unfinished",
        ):
            with self.subTest(body=body):
                with self.assertRaisesRegex(
                    FedCalendarAcquisitionError,
                    "HTML|empty",
                ):
                    make_client(
                        response=FakeResponse(body=body)
                    )[0].fetch()

    def test_19_clock_must_be_timezone_aware(self):

        def bad_clock():
            return START.replace(tzinfo=None)

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "aware datetime",
        ):
            make_client(
                clock=bad_clock
            )[0].fetch()

    def test_20_reversed_clock_rejected(self):
        times = iter((END, START))

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "precedes request start",
        ):
            make_client(
                clock=lambda: next(times)
            )[0].fetch()

    def test_21_bad_timeout_rejected(self):
        for timeout in (
            0,
            -1,
            float("inf"),
            float("nan"),
            True,
        ):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    FedCalendarClient(
                        timeout_seconds=timeout,
                        opener=FakeOpener(),
                    )

    def test_22_duplicate_headers_rejected(self):
        response = FakeResponse()

        response.headers.add_header(
            "Content-Type",
            "text/html",
        )

        with self.assertRaisesRegex(
            FedCalendarAcquisitionError,
            "Ambiguous",
        ):
            make_client(
                response=response
            )[0].fetch()

    def test_23_no_network_during_default_construction(self):
        client = FedCalendarClient()

        self.assertEqual(
            client._transport_mode,
            "DEFAULT_VERIFIED_TLS_CONFIGURATION",
        )

        self.assertTrue(
            client._ssl_context.check_hostname
        )

        self.assertEqual(
            client._ssl_context.verify_mode,
            ssl.CERT_REQUIRED,
        )

        self.assertTrue(
            any(
                isinstance(h, HTTPSHandler)
                for h in client._opener.handlers
            )
        )

        self.assertTrue(
            any(
                isinstance(h, _RejectRedirects)
                for h in client._opener.handlers
            )
        )

    def test_24_source_release_clock_not_blindly_attested(self):
        record = make_client()[0].fetch()

        self.assertGreater(
            record.first_observed_at.year,
            2024,
        )

        self.assertFalse(
            record.independently_historical_attested
        )

    def test_25_missing_identity_cannot_be_parsed_as_schedule_candidate(self):
        record = make_client()[0].fetch()

        self.assertFalse(
            hasattr(record, "scheduled_meetings")
        )

        self.assertFalse(
            hasattr(record, "calendar_snapshots")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
