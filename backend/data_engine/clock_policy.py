"""Explicit HFM broker-wall-clock conversion, bound to one observed server.

No current-offset inference and no historical admission. Ambiguous/nonexistent
labels and undocumented transition-hour windows are refused, rather than guessed.
"""
from datetime import datetime, timedelta, timezone
from calendar import monthrange
from hashlib import sha256
import json
from math import isfinite
from operator import index
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
HFM_HOURS_URL = "https://www.hfm.com/int/en/trading-instruments/trading-hours"


def implementation_sha256():
    return sha256(Path(__file__).read_bytes()).hexdigest()


class ClockPolicyError(RuntimeError):
    pass


def aware_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ClockPolicyError("Clock policy requires aware UTC instants.")
    return value.astimezone(UTC)


def integer(value):
    try:
        if isinstance(value, bool):
            raise TypeError
        return index(value)
    except TypeError:
        raise ClockPolicyError("Source time/count fields must be integers.") from None


def utc_msc(value):
    delta = aware_utc(value) - EPOCH
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def instant(ms):
    try:
        ms = integer(ms)
        if ms <= 0:
            raise ValueError
        return EPOCH + timedelta(milliseconds=ms)
    except (OverflowError, ValueError):
        raise ClockPolicyError("Source timestamp is outside supported bounds.") from None


def _supported_label(wall):
    # HFM documents seasonal dates, not an intraday cutover time. FX is closed
    # around the cutover; do not make a historical claim about those labels.
    if wall.month in (3, 10) and wall.weekday() == 6 and wall.day >= 25 and wall.hour < 6:
        raise ClockPolicyError("Seasonal transition-window labels are not admitted.")


def _offset(value):
    # Implement the broker's published dates explicitly; do not depend on the
    # host's local timezone, current offset, or a changing IANA installation.
    def last_sunday(month):
        last = value.replace(month=month, day=monthrange(value.year, month)[1])
        return last - timedelta(days=(last.weekday()+1) % 7)
    spring, autumn = last_sunday(3), last_sunday(10)
    return timedelta(hours=3 if spring.date() <= value.date() < autumn.date() else 2)


def hfm_wall_to_utc_msc(raw_msc):
    """Pure candidate mapping, not a certificate that old data was available."""
    wall = instant(raw_msc).replace(tzinfo=None)
    _supported_label(wall)
    return utc_msc((wall - _offset(wall)).replace(tzinfo=UTC))


def hfm_utc_to_wall(value):
    value = aware_utc(value)
    wall = (value + _offset(value)).replace(tzinfo=None)
    _supported_label(wall)
    # The API treats these fields as broker wall labels encoded like Unix time.
    return wall.replace(tzinfo=UTC)


def finite(value, *, minimum=0):
    result = float(value)
    if not isfinite(result) or result < minimum:
        raise ClockPolicyError("Nonfinite or invalid market measurement.")
    return result


def decode_json(raw, *, limit):
    if len(raw) > limit:
        raise ClockPolicyError('Clock input exceeds its byte limit.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ClockPolicyError('Duplicate clock input field.')
            result[key] = value
        return result
    def nonfinite(value):
        raise ClockPolicyError('Nonfinite clock input.')
    try:
        value = json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=nonfinite)
    except RecursionError:
        raise ClockPolicyError('Clock input nesting exceeds parser limits.') from None
    if not isinstance(value,dict):
        raise ClockPolicyError('Clock input must be an object.')
    return value


class ClockPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    convention: Literal["hfm_eu_wall_v1"] = "hfm_eu_wall_v1"
    symbol: Literal["EURUSD"] = "EURUSD"
    company_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_policy_url: Literal[HFM_HOURS_URL] = HFM_HOURS_URL
    valid_from: datetime
    valid_until: datetime
    historical_admission: Literal[False] = False

    @field_validator("valid_from", "valid_until")
    @classmethod
    def normalize_time(cls, value):
        return aware_utc(value)

    @model_validator(mode="after")
    def check_lease(self):
        if not timedelta(0) < self.valid_until - self.valid_from <= timedelta(days=30):
            raise ValueError("Clock policy requires a positive lease of at most 30 days.")
        return self

    @property
    def epoch_id(self):
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode()).hexdigest()

    @classmethod
    def load(cls, path):
        with Path(path).open('rb') as stream:
            raw = stream.read(16385)
        return cls.model_validate(decode_json(raw,limit=16384))

    def check_active(self, now):
        if self.implementation_sha256 != implementation_sha256():
            raise ClockPolicyError("Clock implementation differs from the bound epoch.")
        if not self.valid_from <= aware_utc(now) < self.valid_until:
            raise ClockPolicyError("Forward clock policy is not currently active.")

    def check_identity(self, account):
        if account is None:
            raise ClockPolicyError("Broker identity is unavailable.")
        company = str(getattr(account, 'company', ''))
        server = str(getattr(account, 'server', ''))
        if not company or not server or (sha256(company.encode()).hexdigest(), sha256(server.encode()).hexdigest()) != (self.company_sha256, self.server_sha256):
            raise ClockPolicyError("Broker/server identity differs from the clock policy.")

    def check_time(self, ms):
        timestamp = instant(ms)
        if not self.valid_from <= timestamp < self.valid_until:
            raise ClockPolicyError("Timestamp is outside this forward feed epoch.")

    def query_range(self, start, end):
        start, end = aware_utc(start), aware_utc(end)
        if not self.valid_from <= start < end <= self.valid_until:
            raise ClockPolicyError("History request is outside the forward epoch.")
        if end - start > timedelta(days=1):
            raise ClockPolicyError("Clock-aware requests are bounded to one day.")
        first, last = hfm_utc_to_wall(start), hfm_utc_to_wall(end-timedelta(microseconds=1))
        if (first - start) != (last - (end-timedelta(microseconds=1))):
            raise ClockPolicyError("Split the request at seasonal clock transitions.")
        return first, hfm_utc_to_wall(end)

    def tick(self, raw):
        if isinstance(raw, dict) and ('clock_epoch_id' in raw or 'source_record' in raw):
            raise ClockPolicyError("A normalized tick cannot be normalized twice.")
        seconds, ms = integer(raw['time']), integer(raw['time_msc'])
        if not 0 <= ms - seconds * 1000 < 1000:
            raise ClockPolicyError("Raw tick seconds and milliseconds disagree.")
        normalized = hfm_wall_to_utc_msc(ms)
        bid, ask = finite(raw['bid']), finite(raw['ask'])
        if not 0 < bid <= ask:
            raise ClockPolicyError("Invalid bid/ask quote.")
        source = dict(time=seconds, time_msc=ms, bid=bid, ask=ask,
            last=finite(raw['last']), volume=integer(raw['volume']),
            flags=integer(raw['flags']), volume_real=finite(raw['volume_real']))
        if source['volume'] < 0 or source['flags'] < 0:
            raise ClockPolicyError("Invalid raw tick counts/flags.")
        return dict(**{**source, 'time': normalized // 1000, 'time_msc': normalized},
                    clock_epoch_id=self.epoch_id, source_record=source)

    def rate(self, raw):
        if isinstance(raw, dict) and ('clock_epoch_id' in raw or 'source_record' in raw):
            raise ClockPolicyError("A normalized bar cannot be normalized twice.")
        seconds = integer(raw['time'])
        normalized = hfm_wall_to_utc_msc(seconds * 1000)
        source = dict(time=seconds, **{key: finite(raw[key]) for key in ('open','high','low','close')},
            tick_volume=integer(raw['tick_volume']), spread=integer(raw['spread']),
            real_volume=integer(raw['real_volume']))
        if source['low'] <= 0 or source['high'] < max(source[k] for k in ('open','low','close')) or source['low'] > min(source[k] for k in ('open','high','close')):
            raise ClockPolicyError("Invalid bar prices.")
        if any(source[key] < 0 for key in ('tick_volume','spread','real_volume')):
            raise ClockPolicyError("Invalid bar counts/spread.")
        return {**source, 'time': normalized // 1000, 'clock_epoch_id': self.epoch_id, 'source_record': source}
