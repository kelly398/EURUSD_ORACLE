"""Bounded FRED daily-close capture for research Seat 28.

Official series contracts: https://fred.stlouisfed.org/series/SP500 and
https://fred.stlouisfed.org/series/VIXCLS . API vintage dates are not intraday
publication timestamps: https://fred.stlouisfed.org/docs/api/fred/realtime_period.html

Availability is local capture completion, NEVER a historical observation date.
Date-only close observations use the next New York midnight as a conservative
observation-time bound. This intentionally delays same-day usability. No DB,
Council registration, historical admission or order execution occurs here.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
import os
import re
import ssl
from urllib.parse import urlencode
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, build_opener
from zoneinfo import ZoneInfo

import certifi

from backend.agents.council_inputs import CrossAssetObservation


ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
SERIES = ("SP500", "VIXCLS")
MAX_BYTES = 262144
MAX_ROWS = 32


class CrossAssetClientError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CrossAssetClientError("Cross-asset redirects are forbidden.")


@dataclass(frozen=True)
class CrossAssetCapture:
    observed_at: datetime
    observations: tuple[CrossAssetObservation, ...]
    response_sha256: tuple[str, ...]
    # Raw bytes permit independent rehashing; request credentials are excluded.
    raw_responses: tuple[bytes, ...]

    @property
    def historical_admission_authorized(self):
        return False


def _now():
    return datetime.now(timezone.utc)


def _aware_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CrossAssetClientError("Capture requires a timezone-aware clock.")
    return value.astimezone(timezone.utc)


def _date_bound(value: date):
    return datetime.combine(value+timedelta(days=1), time(), ZoneInfo("America/New_York")).astimezone(timezone.utc)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate source field.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite source constant.")


class CrossAssetClient:
    def __init__(self, api_key=None, *, opener=None, clock=None, timeout_seconds=20):
        key = os.environ.get("FRED_API_KEY") if api_key is None else api_key
        if not isinstance(key, str) or re.fullmatch(r"[a-z0-9]{32}", key, re.ASCII) is None:
            raise CrossAssetClientError("A registered FRED_API_KEY is required; never put credentials in logs.")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int,float)) or not 0 < timeout_seconds <= 60:
            raise CrossAssetClientError("Invalid cross-asset request timeout.")
        if opener is not None and not callable(opener):
            raise TypeError("opener must be callable.")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable.")
        self._key, self._timeout = key, timeout_seconds
        self._clock = clock if clock is not None else _now
        self._open = opener if opener is not None else build_opener(
            HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())), NoRedirect()).open

    def _fetch(self, series, start, end):
        params = dict(series_id=series, api_key=self._key, file_type="json", units="lin",
                      observation_start=start.isoformat(), observation_end=end.isoformat(),
                      sort_order="desc", order_by="observation_date", limit=str(MAX_ROWS),
                      offset="0", output_type="1", realtime_start=end.isoformat(), realtime_end=end.isoformat())
        request = Request(ENDPOINT+"?"+urlencode(params), headers={"Accept":"application/json", "Accept-Encoding":"identity"})
        try:
            with self._open(request, timeout=self._timeout) as response:
                if response.status != 200 or response.geturl() != request.full_url:
                    raise CrossAssetClientError("Cross-asset HTTP status or final origin is invalid.")
                if response.headers.get("Content-Type", "").split(';')[0].strip().lower() != "application/json":
                    raise CrossAssetClientError("Cross-asset response is not JSON.")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise CrossAssetClientError("Encoded cross-asset responses are forbidden.")
                raw = response.read(MAX_BYTES+1)
        except CrossAssetClientError:
            raise
        except Exception:
            # Network exceptions can contain request URLs with query credentials.
            raise CrossAssetClientError("Cross-asset source acquisition failed.") from None
        if not isinstance(raw, bytes) or len(raw)>MAX_BYTES:
            raise CrossAssetClientError("Cross-asset response exceeds byte bound.")
        if self._key.encode('ascii') in raw:
            raise CrossAssetClientError("Cross-asset response unexpectedly contains a credential.")
        try:
            payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_json_object,
                                 parse_constant=_invalid_constant)
            if not isinstance(payload,dict) or payload.get('units') != 'lin' or payload.get('output_type') != 1:
                raise ValueError
            if payload.get('realtime_start') != end.isoformat() or payload.get('realtime_end') != end.isoformat():
                raise ValueError
            rows = payload['observations']
            if not isinstance(rows,list) or not 2<=len(rows)<=MAX_ROWS:
                raise ValueError
            values, previous = {}, None
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('date'),str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['date'], re.ASCII):
                    raise ValueError
                day = date.fromisoformat(row['date'])
                if not start <= day <= end or (previous is not None and day >= previous):
                    raise ValueError
                if row.get('realtime_start') != end.isoformat() or row.get('realtime_end') != end.isoformat():
                    raise ValueError
                previous = day
                value = row['value']
                if value == '.':
                    continue
                if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value, re.ASCII):
                    raise ValueError
                number = float(value)
                if not isfinite(number) or number <= 0:
                    raise ValueError
                values[day] = number
        except (ValueError, KeyError, TypeError, UnicodeError, OverflowError, RecursionError):
            raise CrossAssetClientError("Cross-asset source response violates the daily-close contract.") from None
        return raw, values

    def capture(self):
        started = _aware_utc(self._clock())
        end = started.date()
        start = end-timedelta(days=14)
        responses, observations = [], []
        for series in SERIES:
            raw, values = self._fetch(series, start, end)
            responses.append(raw)
            observations.append(values)
        finished = _aware_utc(self._clock())
        if finished < started or finished-started > timedelta(seconds=120):
            raise CrossAssetClientError("Cross-asset acquisition clock/window is invalid.")
        common = sorted((day for day in observations[0].keys() & observations[1].keys()
                         if _date_bound(day) <= finished), reverse=True)
        if len(common)<2:
            raise CrossAssetClientError("Two aligned, already-closed daily observations are required.")
        latest, previous = common[:2]
        if finished-_date_bound(latest)>timedelta(days=4):
            raise CrossAssetClientError("Latest aligned cross-asset close is stale.")
        hashes = tuple(sha256(raw).hexdigest() for raw in responses)
        snapshots = []
        for series, values, response_hash in zip(SERIES, observations, hashes, strict=True):
            canonical = json.dumps(dict(series=series, date=latest.isoformat(), previous=previous.isoformat(),
                                       value=values[latest], previous_value=values[previous], response_sha256=response_hash),
                                   sort_keys=True, separators=(',',':'))
            snapshots.append(CrossAssetObservation(series_id=series, observation_at=_date_bound(latest),
                previous_observation_at=_date_bound(previous), available_at=finished, ingested_at=finished,
                value=values[latest], previous_value=values[previous], content_hash=sha256(canonical.encode()).hexdigest()))
        return CrossAssetCapture(finished, tuple(snapshots), hashes, tuple(responses))
