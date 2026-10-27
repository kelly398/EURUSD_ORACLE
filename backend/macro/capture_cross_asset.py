"""Capture current FRED closes to stdout; no historical/production admission.

Usage: python -m backend.macro.capture_cross_asset
Configure FRED_API_KEY in the local environment or .env; never pass it on the
command line. Source values are for the account holder's local analysis.
"""
import json
import sys

from dotenv import load_dotenv

from backend.macro.cross_asset_client import CrossAssetClient, CrossAssetClientError


def main():
    load_dotenv()
    try:
        result=CrossAssetClient().capture()
    except CrossAssetClientError as error:
        print(str(error),file=sys.stderr)
        return 1
    print(json.dumps(dict(observed_at=result.observed_at.isoformat(),
        historical_admission_authorized=False,
        response_sha256=result.response_sha256,
        observations=[item.model_dump(mode='json') for item in result.observations]),indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
