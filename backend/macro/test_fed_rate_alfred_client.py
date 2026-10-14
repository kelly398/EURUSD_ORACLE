
"""F3.4: deterministic tests for read-only ALFRED acquisition.

Run from project root:
    python -m backend.macro.test_fed_rate_alfred_client

Uses only in-memory HTTP simulations. No network, database, broker, or HMM.
"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import unittest
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler
from unittest.mock import patch

from backend.macro.fed_rate_alfred_client import (
    ALFRED_API_ENDPOINT,
    MAX_ALFRED_RESPONSE_BYTES,
    FedAlfredClient,
    FedAlfredClientError,
    FedAlfredSourcePair,
    _NoRedirect,
)
from backend.macro.fed_rate_backfill import FedRateBackfillPlanner
from backend.macro.fed_rate_vintage_protocol import (
    ALFRED_VINTAGE_STATUS,
    corroborate_fed_effective_rate_vintage,
)
from backend.macro.test_fed_rate_backfill import FakeClient
from backend.macro.test_fed_rate_extractor import STATEMENT_URL


UTC = timezone.utc
KEY = "ab" * 16  # Synthetic test value only, never a real credential.
EFFECTIVE = date(2026, 9, 17)
FETCHED = datetime(2026, 10, 13, 18, 30, tzinfo=UTC)


def query_fields(request):
    return parse_qs(urlsplit(request.full_url).query, strict_parsing=True)


def valid_payload(request):
    q = query_fields(request)
    series = q["series_id"][0]
    vintage = q["realtime_start"][0]
    observation_date = q["observation_start"][0]
    return {
        "realtime_start": vintage,
        "realtime_end": vintage,
        "observation_start": observation_date,
        "observation_end": observation_date,
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "asc",
        "count": 1,
        "offset": 0,
        "limit": 1,
        "observations": [{
            "realtime_start": vintage,
            "realtime_end": vintage,
            "date": observation_date,
            "value": "3.75" if series == "DFEDTARL" else "4.00",
        }],
    }


class FakeResponse:
    def __init__(
        self,
        request,
        body=None,
        *,
        status=200,
        final_url=None,
        headers=None,
    ):
        self.status = status
        self._final_url = request.full_url if final_url is None else final_url
        self.body = (
            json.dumps(valid_payload(request)).encode("utf-8")
            if body is None else body
        )
        self.headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(self.body)),
        }
        if headers:
            self.headers.update(headers)
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def geturl(self):
        return self._final_url

    def read(self, size=-1):
        self.read_limits.append(size)
        return self.body[:size] if size >= 0 else self.body


class FakeTransport:
    def __init__(self, responder=None):
        self.calls = []
        self.responses = []
        self.responder = responder

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        result = (
            self.responder(request, len(self.calls))
            if self.responder is not None
            else FakeResponse(request)
        )
        self.responses.append(result)
        return result


def changed_payload(request, modify):
    payload = valid_payload(request)
    modify(payload)
    return json.dumps(payload).encode("utf-8")


class FedAlfredClientTests(unittest.TestCase):
    def client(self, *, responder=None, clock=None, key=KEY):
        transport = FakeTransport(responder)
        client = FedAlfredClient(
            api_key=key,
            opener=transport,
            clock=clock if clock is not None else lambda: FETCHED,
        )
        return client, transport

    def fetch(self, *, responder=None, clock=None):
        client, transport = self.client(
            responder=responder,
            clock=clock,
        )
        return client.fetch_pair(EFFECTIVE, EFFECTIVE), transport

    def test_01_exact_endpoint_query_and_header_contract(self):
        pair, transport = self.fetch()
        self.assertIsInstance(pair, FedAlfredSourcePair)
        self.assertEqual(len(transport.calls), 2)

        for index, (request, timeout) in enumerate(transport.calls):
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(
                urlsplit(request.full_url)
                ._replace(query="")
                .geturl(),
                ALFRED_API_ENDPOINT,
            )
            self.assertEqual(timeout, 20.0)

            fields = query_fields(request)

            self.assertEqual(fields, {
                "series_id": [
                    ["DFEDTARL", "DFEDTARU"][index]
                ],
                "realtime_start": ["2026-09-17"],
                "realtime_end": ["2026-09-17"],
                "observation_start": ["2026-09-17"],
                "observation_end": ["2026-09-17"],
                "units": ["lin"],
                "output_type": ["1"],
                "file_type": ["json"],
                "limit": ["1"],
                "offset": ["0"],
                "api_key": [KEY],
            })

            headers = dict(
                (k.lower(), v)
                for k, v in request.header_items()
            )
            self.assertEqual(
                headers["accept"], "application/json"
            )
            self.assertEqual(
                headers["accept-encoding"], "identity"
            )
            self.assertTrue(
                headers["user-agent"].startswith("EURUSD-ORACLE")
            )

    def test_02_complete_pair_and_raw_sha256_fingerprints(self):
        pair, transport = self.fetch()
        low, high = pair.observations

        self.assertEqual(
            (low.series_id, high.series_id),
            ("DFEDTARL", "DFEDTARU"),
        )
        self.assertEqual(
            (low.value_percent, high.value_percent),
            ("3.75", "4.00"),
        )
        self.assertEqual(low.observation_date, EFFECTIVE)
        self.assertEqual(high.vintage_date, EFFECTIVE)

        for record, response in zip(
            (pair.lower, pair.upper),
            transport.responses,
            strict=True,
        ):
            digest = hashlib.sha256(response.body).hexdigest()
            self.assertEqual(record.raw_response, response.body)
            self.assertEqual(record.response_sha256, digest)
            self.assertEqual(
                record.observation.response_sha256,
                digest,
            )
            self.assertEqual(
                record.endpoint, ALFRED_API_ENDPOINT
            )
            self.assertNotIn(KEY, record.endpoint)
            self.assertFalse(record.persistence_permitted)
            self.assertEqual(
                response.read_limits,
                [MAX_ALFRED_RESPONSE_BYTES + 1],
            )

        self.assertFalse(pair.persistence_permitted)

        with self.assertRaises(FrozenInstanceError):
            pair.lower = pair.upper

    def test_03_aware_fetch_clocks_normalized_to_utc(self):
        zone = timezone(timedelta(hours=3))
        clocks = iter([
            FETCHED.astimezone(zone),
            (FETCHED + timedelta(seconds=4)).astimezone(zone),
        ])

        pair, _ = self.fetch(clock=lambda: next(clocks))

        self.assertEqual(
            pair.lower.observation.fetched_at, FETCHED
        )
        self.assertEqual(
            pair.upper.observation.fetched_at,
            FETCHED + timedelta(seconds=4),
        )
        self.assertIs(
            pair.upper.observation.fetched_at.tzinfo,
            UTC,
        )

    def test_04_response_pair_integrates_with_existing_vintage_protocol(self):
        plan = FedRateBackfillPlanner(
            client=FakeClient()
        ).prepare(STATEMENT_URL)

        pair, _ = self.fetch()

        result = corroborate_fed_effective_rate_vintage(
            plan, *pair.observations
        )

        self.assertEqual(
            result.status, ALFRED_VINTAGE_STATUS
        )
        self.assertFalse(result.historical_html_certified)
        self.assertFalse(result.source_response_authenticated)
        self.assertFalse(result.persistence_permitted)

    def test_05_default_transport_uses_verified_tls_and_blocks_redirects(self):
        with (
            patch(
                "backend.macro.fed_rate_alfred_client.certifi.where",
                return_value="cert.pem",
            ),
            patch(
                "backend.macro.fed_rate_alfred_client."
                "ssl.create_default_context"
            ) as create,
            patch(
                "backend.macro.fed_rate_alfred_client.build_opener"
            ) as build,
        ):
            FedAlfredClient(api_key=KEY)

            create.assert_called_once_with(
                cafile="cert.pem"
            )

            handlers = build.call_args.args

            self.assertTrue(
                any(
                    isinstance(h, HTTPSHandler)
                    for h in handlers
                )
            )
            self.assertTrue(
                any(
                    isinstance(h, _NoRedirect)
                    for h in handlers
                )
            )

            build.return_value.open.assert_not_called()

    def test_06_redirect_handler_fails_closed(self):
        with self.assertRaisesRegex(
            FedAlfredClientError, "redirects"
        ):
            _NoRedirect().redirect_request(
                None, None, 302, "", {},
                "https://evil.example",
            )

    def test_07_env_key_supported_and_explicit_key_overrides_env(self):
        with patch.dict(os.environ, {"FRED_API_KEY": KEY}):
            transport = FakeTransport()
            client = FedAlfredClient(
                opener=transport,
                clock=lambda: FETCHED,
            )
            client.fetch_pair(EFFECTIVE, EFFECTIVE)

            self.assertEqual(
                query_fields(
                    transport.calls[0][0]
                )["api_key"],
                [KEY],
            )

        other = "cd" * 16

        with patch.dict(
            os.environ, {"FRED_API_KEY": "invalid"}
        ):
            client, transport = self.client(key=other)
            client.fetch_pair(EFFECTIVE, EFFECTIVE)

            self.assertEqual(
                query_fields(
                    transport.calls[0][0]
                )["api_key"],
                [other],
            )

    def test_08_invalid_api_keys_rejected_without_network(self):
        bad = (
            "",
            "a" * 31,
            "A" * 32,
            "_" * 32,
            "a" * 33,
            1,
            b"a" * 32,
        )

        for key in bad:
            with self.subTest(key=str(key)[:8]):
                with self.assertRaisesRegex(
                    FedAlfredClientError, "FRED_API_KEY"
                ):
                    self.client(key=key)

        with patch.dict(
            os.environ, {"FRED_API_KEY": ""}
        ):
            with self.assertRaises(FedAlfredClientError):
                FedAlfredClient(opener=FakeTransport())

    def test_09_invalid_configuration_rejected(self):
        for timeout in (
            0,
            -1,
            121,
            True,
            "20",
            float("nan"),
            float("inf"),
        ):
            with self.subTest(timeout=timeout):
                with self.assertRaises(FedAlfredClientError):
                    FedAlfredClient(
                        api_key=KEY,
                        timeout_seconds=timeout,
                        opener=FakeTransport(),
                    )

        with self.assertRaises(TypeError):
            FedAlfredClient(
                api_key=KEY,
                opener=object(),
            )

        with self.assertRaises(TypeError):
            FedAlfredClient(
                api_key=KEY,
                clock=object(),
                opener=FakeTransport(),
            )

    def test_10_invalid_dates_and_unsupported_series_before_io(self):
        client, transport = self.client()

        for invalid in (
            "2026-09-17",
            datetime(2026, 9, 17),
            None,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(FedAlfredClientError):
                    client.fetch_pair(
                        invalid, EFFECTIVE
                    )

        with self.assertRaisesRegex(
            FedAlfredClientError, "predate"
        ):
            client.fetch_pair(
                EFFECTIVE, date(2026, 9, 16)
            )

        with self.assertRaisesRegex(
            FedAlfredClientError, "Unsupported"
        ):
            client._request(
                "EFFR", EFFECTIVE, EFFECTIVE
            )

        self.assertEqual(transport.calls, [])

    def test_11_reject_non_200_status_and_changed_destination(self):
        for responder in (
            lambda req, n: FakeResponse(req, status=302),
            lambda req, n: FakeResponse(
                req, final_url="https://evil.example/"
            ),
        ):
            with self.subTest(responder=responder):
                with self.assertRaises(FedAlfredClientError):
                    self.fetch(responder=responder)

    def test_12_reject_wrong_media_type_or_character_encoding(self):
        for mime in (
            "text/html",
            "application/xml",
            "application/json; charset=utf-16",
        ):
            def respond(req, n):
                return FakeResponse(
                    req,
                    headers={"Content-Type": mime},
                )

            with self.subTest(mime=mime):
                with self.assertRaises(FedAlfredClientError):
                    self.fetch(responder=respond)

    def test_13_reject_compressed_responses(self):
        for encoding in ("gzip", "br"):
            with self.subTest(encoding=encoding):
                with self.assertRaisesRegex(
                    FedAlfredClientError, "Compressed"
                ):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req,
                            headers={
                                "Content-Encoding": encoding
                            },
                        )
                    )

    def test_14_reject_invalid_or_mismatched_content_length(self):
        for length in (
            "xyz",
            "-1",
            str(MAX_ALFRED_RESPONSE_BYTES + 1),
            "2",
        ):
            with self.subTest(length=length):
                with self.assertRaises(FedAlfredClientError):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req,
                            headers={
                                "Content-Length": length
                            },
                        )
                    )

    def test_15_reject_empty_nul_and_oversized_payloads(self):
        for body in (
            b"",
            b"\x00",
            b"x" * (MAX_ALFRED_RESPONSE_BYTES + 1),
        ):
            with self.subTest(size=len(body)):
                with self.assertRaises(FedAlfredClientError):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req, body=body
                        )
                    )

    def test_16_exact_maximum_size_json_payload_is_allowed(self):
        def respond(req, n):
            core = json.dumps(
                valid_payload(req)
            ).encode("utf-8")

            body = core + b" " * (
                MAX_ALFRED_RESPONSE_BYTES - len(core)
            )

            return FakeResponse(req, body=body)

        pair, transport = self.fetch(responder=respond)

        self.assertEqual(
            len(pair.lower.raw_response),
            MAX_ALFRED_RESPONSE_BYTES,
        )
        self.assertEqual(
            len(transport.responses[0].read_limits),
            1,
        )

    def test_17_reject_bad_unicode_json_duplicates_and_nonfinite_constants(self):
        for body in (
            b"\xff",
            b"{invalid}",
            b"[1]",
            b'{"file_type":"json","file_type":"xml"}',
            b'{"value":NaN}',
        ):
            with self.subTest(body=body[:25]):
                with self.assertRaises(FedAlfredClientError):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req, body=body
                        )
                    )

    def test_18_reject_wrong_top_level_response_metadata(self):
        edits = (
            lambda p: p.update(
                realtime_start="2026-09-16"
            ),
            lambda p: p.update(
                realtime_end="2026-09-18"
            ),
            lambda p: p.update(
                observation_end="2026-09-16"
            ),
            lambda p: p.update(units="pch"),
            lambda p: p.update(file_type="xml"),
            lambda p: p.update(output_type="1"),
            lambda p: p.update(count=0),
            lambda p: p.update(offset=True),
            lambda p: p.update(limit=10),
        )

        for edit in edits:
            with self.subTest(edit=edit):
                with self.assertRaisesRegex(
                    FedAlfredClientError, "metadata"
                ):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req,
                            body=changed_payload(req, edit),
                        )
                    )

    def test_19_optional_series_id_mismatch_rejected(self):
        with self.assertRaisesRegex(
            FedAlfredClientError, "series identity"
        ):
            self.fetch(
                responder=lambda req, n: FakeResponse(
                    req,
                    body=changed_payload(
                        req,
                        lambda p: p.update(
                            series_id="EFFR"
                        ),
                    ),
                )
            )

    def test_20_reject_missing_multiple_or_invalid_observation_rows(self):
        edits = (
            lambda p: p.update(observations=[]),
            lambda p: p.update(
                observations=[
                    p["observations"][0]
                ] * 2
            ),
            lambda p: p.update(
                observations=[None]
            ),
            lambda p: p.pop("observations"),
        )

        for edit in edits:
            with self.subTest(edit=edit):
                with self.assertRaisesRegex(
                    FedAlfredClientError, "exactly one"
                ):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req,
                            body=changed_payload(req, edit),
                        )
                    )

    def test_21_reject_wrong_or_malformed_observation_and_realtime_dates(self):
        edits = (
            lambda p: p["observations"][0].update(
                date="2026-09-16"
            ),
            lambda p: p["observations"][0].update(
                date="2026-02-30"
            ),
            lambda p: p["observations"][0].update(
                date=123
            ),
            lambda p: p["observations"][0].update(
                realtime_start="2026-09-18"
            ),
            lambda p: p["observations"][0].update(
                realtime_end="2026-09-16"
            ),
        )

        for edit in edits:
            with self.subTest(edit=edit):
                with self.assertRaises(FedAlfredClientError):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req,
                            body=changed_payload(req, edit),
                        )
                    )

    def test_22_reject_invalid_rate_values(self):
        for value in (
            ".",
            "NaN",
            "-1",
            "3.75%",
            "1e2",
            "",
            3.75,
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    FedAlfredClientError,
                    "policy-rate value",
                ):
                    self.fetch(
                        responder=lambda req, n: FakeResponse(
                            req,
                            body=changed_payload(
                                req,
                                lambda p: p["observations"][0]
                                .update(value=value),
                            ),
                        )
                    )

    def test_23_reject_naive_or_pre_vintage_fetch_clock(self):
        for clock in (
            lambda: datetime(2026, 10, 13, 18),
            lambda: datetime(
                2026, 9, 16, 23, 0, tzinfo=UTC
            ),
        ):
            with self.subTest(clock=clock):
                with self.assertRaises(FedAlfredClientError):
                    self.fetch(clock=clock)

    def test_24_network_failures_hide_api_key_and_urls(self):
        def raise_error(req, n):
            raise URLError(
                "sensitive URL " + req.full_url
            )

        client, transport = self.client(
            responder=raise_error
        )

        with self.assertRaises(FedAlfredClientError) as raised:
            client.fetch_pair(EFFECTIVE, EFFECTIVE)

        self.assertEqual(
            str(raised.exception),
            "ALFRED HTTPS request failed.",
        )
        self.assertNotIn(
            KEY, str(raised.exception)
        )
        self.assertEqual(
            len(transport.calls), 1
        )

    def test_25_second_request_failure_never_yields_a_partial_pair(self):
        def respond(req, n):
            if n == 2:
                raise TimeoutError(
                    "upper source unavailable"
                )
            return FakeResponse(req)

        client, transport = self.client(
            responder=respond
        )

        with self.assertRaisesRegex(
            FedAlfredClientError,
            "HTTPS request failed",
        ):
            client.fetch_pair(
                EFFECTIVE, EFFECTIVE
            )

        self.assertEqual(
            len(transport.calls), 2
        )

    def test_26_reject_impossible_lower_above_upper(self):
        def respond(req, n):
            body = changed_payload(
                req,
                lambda p: p["observations"][0].update(
                    value="4.25" if n == 1 else "4.00"
                ),
            )
            return FakeResponse(req, body=body)

        with self.assertRaisesRegex(
            FedAlfredClientError,
            "lower target exceeds",
        ):
            self.fetch(responder=respond)

    def test_27_reject_reversed_fetch_timestamps(self):
        clocks = iter([
            FETCHED,
            FETCHED - timedelta(seconds=1),
        ])

        with self.assertRaisesRegex(
            FedAlfredClientError,
            "out of order",
        ):
            self.fetch(
                clock=lambda: next(clocks)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
