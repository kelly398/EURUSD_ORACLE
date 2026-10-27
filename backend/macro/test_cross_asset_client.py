"""FRED cross-asset capture tests using in-memory responses only."""
from datetime import datetime, timezone, timedelta
import json
import unittest
from urllib.parse import urlsplit, parse_qs

from backend.macro.cross_asset_client import CrossAssetClient, CrossAssetClientError, MAX_BYTES


NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
KEY = "ab"*16


class Response:
    def __init__(self, request, *, modify=None, raw=None, status=200, final_url=None, headers=None):
        fields = parse_qs(urlsplit(request.full_url).query)
        day = fields['realtime_start'][0]
        series = fields['series_id'][0]
        payload = dict(units="lin", output_type=1, realtime_start=day, realtime_end=day,
                       observations=[dict(date=date, value=value, realtime_start=day, realtime_end=day)
                                     for date,value in (("2026-10-26","101" if series=="SP500" else "19"),
                                                        ("2026-10-23","100" if series=="SP500" else "20"))])
        if modify:
            modify(payload,series)
        self.raw = raw if raw is not None else json.dumps(payload).encode()
        self.status = status
        self.url = request.full_url if final_url is None else final_url
        self.headers = {'Content-Type':'application/json',**(headers or {})}

    def __enter__(self): return self
    def __exit__(self,*args): return False
    def geturl(self): return self.url
    def read(self,size): return self.raw[:size]


class Transport:
    def __init__(self, **options): self.options,self.calls=options,[]
    def __call__(self,request,timeout):
        self.calls.append(request)
        return Response(request,**self.options)


def client(transport=None,clock=None):
    return CrossAssetClient(KEY,opener=transport if transport is not None else Transport(),
                            clock=clock if clock is not None else lambda:NOW)


class CrossAssetClientTests(unittest.TestCase):
    def test_aligned_capture_uses_local_availability_not_historical_date(self):
        transport=Transport()
        result=client(transport).capture()
        self.assertEqual([o.series_id for o in result.observations],['SP500','VIXCLS'])
        self.assertEqual(len(transport.calls),2)
        for item in result.observations:
            self.assertEqual(item.available_at,NOW)
            self.assertEqual(item.ingested_at,NOW)
            self.assertEqual(item.observation_at,datetime(2026,10,27,4,tzinfo=timezone.utc))
        self.assertFalse(result.historical_admission_authorized)

    def test_api_key_not_in_capture(self):
        self.assertNotIn(KEY,repr(client().capture()))

    def test_credential_echo_is_rejected(self):
        with self.assertRaises(CrossAssetClientError):
            client(Transport(modify=lambda p,s:p.__setitem__('unexpected',KEY))).capture()

    def test_fixed_series_vintage_bounds_and_verified_endpoint(self):
        transport=Transport()
        client(transport).capture()
        for request in transport.calls:
            parsed=urlsplit(request.full_url)
            self.assertEqual(parsed.scheme,'https')
            self.assertEqual(parsed.netloc,'api.stlouisfed.org')
            query=parse_qs(parsed.query)
            self.assertEqual(query['realtime_start'],['2026-10-27'])
            self.assertEqual(query['realtime_end'],['2026-10-27'])
            self.assertEqual(query['sort_order'],['desc'])

    def test_missing_key_and_invalid_timeout(self):
        for key in ('', 'abc', 'A'*32):
            with self.subTest(key=key),self.assertRaises(CrossAssetClientError):
                CrossAssetClient(key)
        for value in (0,61,True,float('nan')):
            with self.subTest(timeout=value),self.assertRaises(CrossAssetClientError):
                CrossAssetClient(KEY,timeout_seconds=value)

    def test_no_redirect_html_gzip_or_http_error(self):
        for options in ({'final_url':'https://example.com'}, {'status':403},
                        {'headers':{'Content-Type':'text/html'}},
                        {'headers':{'Content-Encoding':'gzip'}}):
            with self.subTest(options=options),self.assertRaises(CrossAssetClientError):
                client(Transport(**options)).capture()

    def test_byte_bound_and_invalid_json(self):
        for raw in (b'x'*(MAX_BYTES+1),b'not json',b'[]',b'\xff',
                    b'{"units":"lin","units":"log"}',b'{"unexpected":NaN}',
                    b'['*2000+b']'*2000):
            with self.subTest(size=len(raw)),self.assertRaises(CrossAssetClientError):
                client(Transport(raw=raw)).capture()

    def test_invalid_data_fields(self):
        def modify(field,value):
            return lambda payload,series:payload['observations'][0].__setitem__(field,value)
        for field,value in (('value','nan'),('value','Infinity'),('value','0'),('value','-1'),
                            ('value',True),('date','2026-10-28'),('date','invalid'),
                            ('realtime_start','2020-01-01')):
            with self.subTest(field=field,value=value),self.assertRaises(CrossAssetClientError):
                client(Transport(modify=modify(field,value))).capture()

    def test_unsorted_duplicate_or_missing_observations(self):
        for mutate in (lambda p,s:p['observations'].reverse(),
                       lambda p,s:p['observations'].append(p['observations'][0]),
                       lambda p,s:p.__setitem__('observations',[]),
                       lambda p,s:p['observations'][0].__setitem__('value','.')):
            with self.subTest(mutate=mutate),self.assertRaises(CrossAssetClientError):
                client(Transport(modify=mutate)).capture()

    def test_current_day_date_bound_cannot_enter_early(self):
        def modify(payload,series):
            payload['observations'][0]['date']='2026-10-27'
        with self.assertRaises(CrossAssetClientError):
            client(Transport(modify=modify)).capture()

    def test_backward_or_slow_clock(self):
        for finish in (NOW-timedelta(seconds=1),NOW+timedelta(seconds=121)):
            times=iter((NOW,finish))
            with self.subTest(finish=finish),self.assertRaises(CrossAssetClientError):
                client(clock=lambda:next(times)).capture()

    def test_network_exception_does_not_expose_key(self):
        def broken(*args,**kwargs): raise RuntimeError('https://example.com?api_key='+KEY)
        with self.assertRaises(CrossAssetClientError) as error:
            client(transport=broken).capture()
        self.assertNotIn(KEY,str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)


if __name__=='__main__':
    unittest.main(verbosity=2)
