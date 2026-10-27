"""Create a server-bound forward lease from recent probe evidence. No DB writes."""
import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from backend.data_engine.clock_policy import (ClockPolicy, ClockPolicyError, aware_utc,
    hfm_wall_to_utc_msc, instant, implementation_sha256, decode_json)


def from_evidence(raw, *, now):
    now = aware_utc(now)
    if len(raw) > 65536:
        raise ClockPolicyError('Clock evidence exceeds 64 KiB.')
    data = decode_json(raw,limit=65536)
    if data.get('schema_version')!=1 or data.get('initialized') is not True or data.get('read_only') is not True or data.get('error_type') or data.get('blocker'):
        raise ClockPolicyError('Clock probe did not complete its read-only evidence checks.')
    if data.get('hfm_company_match') is not True:
        raise ClockPolicyError('Probe does not establish an HFM broker identity.')
    observations = data.get('observations', [])
    if len(observations) < 3:
        raise ClockPolicyError('Clock evidence needs repeated observations.')
    receipts = []
    for item in observations:
        before = aware_utc(datetime.fromisoformat(item['received_before']))
        after = aware_utc(datetime.fromisoformat(item['received_after']))
        if not before <= after <= now:
            raise ClockPolicyError('Clock probe receipt interval is invalid.')
        if item['raw_time_msc']//1000 != item['raw_time']:
            raise ClockPolicyError('Clock probe raw time fields disagree.')
        converted = instant(hfm_wall_to_utc_msc(item['raw_time_msc']))
        if not -180 <= (converted-after).total_seconds() <= 5:
            raise ClockPolicyError('Documented mapping does not match the observed UTC clock.')
        receipts.append(after)
    if receipts != sorted(receipts) or receipts[-1]-receipts[0] < timedelta(seconds=1):
        raise ClockPolicyError('Probe observations must span increasing receipt times.')
    if now-receipts[-1] > timedelta(minutes=15):
        raise ClockPolicyError('Clock evidence is stale; collect a fresh probe.')
    requests=data.get('query_evidence', [])
    query = {item['kind']: item for item in requests}
    if set(query)!={'utc_request','raw_clock_request'} or len(query)!=len(requests):
        raise ClockPolicyError('Probe must independently verify the history query convention.')
    for kind, item in query.items():
        if isinstance(item.get('row_count'),bool) or not isinstance(item.get('row_count'),int) or item['row_count']<=0:
            raise ClockPolicyError('Query convention is inconclusive without returned rows.')
        start = aware_utc(datetime.fromisoformat(item['requested_start']))
        end = aware_utc(datetime.fromisoformat(item['requested_end']))
        if not start <= instant(item['first_raw_msc']) <= instant(item['last_raw_msc']) < end+timedelta(seconds=1):
            raise ClockPolicyError('Raw history does not follow the asserted query convention.')
    recent = instant(hfm_wall_to_utc_msc(query['raw_clock_request']['last_raw_msc']))
    if not -90 <= (recent-receipts[-1]).total_seconds() <= 5:
        raise ClockPolicyError('Broker-clock query did not return contemporaneous quotes.')
    return ClockPolicy(company_sha256=data['company_sha256'], server_sha256=data['server_sha256'],
        evidence_sha256=sha256(raw).hexdigest(), implementation_sha256=implementation_sha256(),
        valid_from=receipts[-1], valid_until=receipts[-1]+timedelta(days=30))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        with args.evidence.open('rb') as stream:
            raw=stream.read(65537)
        policy=from_evidence(raw,now=datetime.now(timezone.utc))
        # Explicit exclusive creation: a bound epoch must never be overwritten.
        with args.output.open('x',encoding='utf-8') as stream:
            stream.write(policy.model_dump_json(indent=2))
    except (OSError,ValueError,KeyError,TypeError,ClockPolicyError):
        parser.exit(2,'Clock policy creation failed; verify fresh evidence and a new output path.\n')
    print(json.dumps(dict(epoch_id=policy.epoch_id,valid_from=policy.valid_from.isoformat(),
                          valid_until=policy.valid_until.isoformat(),historical_admission=False),indent=2))


if __name__=='__main__':
    raise SystemExit(main())
