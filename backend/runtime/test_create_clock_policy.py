"""Clock evidence admission without broker, network or database access."""
from datetime import timedelta
import json
import unittest

from backend.data_engine.test_clock_policy import NOW,policy
from backend.data_engine.clock_policy import ClockPolicyError,hfm_utc_to_wall,utc_msc
from backend.runtime.create_clock_policy import from_evidence


def evidence():
    lease=policy()
    observations=[]
    for delay in (2,1,0):
        stamp=NOW-timedelta(seconds=delay)
        ms=utc_msc(hfm_utc_to_wall(stamp))
        observations.append(dict(received_before=stamp.isoformat(),received_after=stamp.isoformat(),
                                 raw_time=ms//1000,raw_time_msc=ms))
    queries=[]
    for kind,delta in (('utc_request',timedelta()),('raw_clock_request',timedelta(hours=2))):
        queries.append(dict(kind=kind,row_count=1,requested_start=(NOW-timedelta(seconds=20)+delta).isoformat(),
            requested_end=(NOW-timedelta(seconds=10)+delta).isoformat(),
            first_raw_msc=utc_msc(NOW-timedelta(seconds=15)+delta),
            last_raw_msc=utc_msc(NOW-timedelta(seconds=15)+delta)))
    return dict(schema_version=1,initialized=True,read_only=True,hfm_company_match=True,
        company_sha256=lease.company_sha256,server_sha256=lease.server_sha256,
        observations=observations,query_evidence=queries)


class EvidenceTests(unittest.TestCase):
    def test_recent_complete_evidence_creates_a_forward_only_lease(self):
        created=from_evidence(json.dumps(evidence()).encode(),now=NOW)
        self.assertEqual(created.valid_from,NOW)
        self.assertEqual(created.valid_until,NOW+timedelta(days=30))
        self.assertFalse(created.historical_admission)

    def test_stale_incomplete_or_unknown_identity_cannot_create_policy(self):
        for changes in ({'initialized':False},{'hfm_company_match':False},{'observations':[]},
                        {'query_evidence':[]},{'error_type':'RuntimeError'}):
            with self.subTest(changes=changes),self.assertRaises(ClockPolicyError):
                from_evidence(json.dumps({**evidence(),**changes}).encode(),now=NOW)
        with self.assertRaises(ClockPolicyError):from_evidence(json.dumps(evidence()).encode(),now=NOW+timedelta(minutes=16))

    def test_query_domain_must_be_independently_demonstrated(self):
        data=evidence();data['query_evidence'][1]['last_raw_msc']-=7200000
        with self.assertRaises(ClockPolicyError):from_evidence(json.dumps(data).encode(),now=NOW)
        data=evidence();data['query_evidence'].append(data['query_evidence'][0])
        with self.assertRaises(ClockPolicyError):from_evidence(json.dumps(data).encode(),now=NOW)

    def test_disagreeing_raw_clock_or_utc_receipts_cannot_be_admitted(self):
        data=evidence();data['observations'][-1]['raw_time']+=1
        with self.assertRaises(ClockPolicyError):from_evidence(json.dumps(data).encode(),now=NOW)
        data=evidence();data['observations'][-1]['raw_time_msc']+=3600000
        data['observations'][-1]['raw_time']+=3600
        with self.assertRaises(ClockPolicyError):from_evidence(json.dumps(data).encode(),now=NOW)


if __name__=='__main__':unittest.main()
