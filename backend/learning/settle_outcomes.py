"""Score explicitly supplied mature quotes. Does not fetch data, fit or trade.

Each request is atomic and retryable. Earlier successful requests remain committed
if a later request fails. Never backdates the time an outcome entered memory.
"""
import argparse
import json
from pathlib import Path

from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from backend.agents.run_research_council import reject_duplicates, reject_nonfinite
from backend.learning.contracts import MemoryInput, OutcomeQuote
from backend.learning.database import memory_engine
from backend.learning.memory import CouncilMemory, MemoryConflict, MemoryIntegrityError


class SettlementRequest(MemoryInput):
    prediction_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote: OutcomeQuote


def load_requests(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("Quote batch exceeds 1 MiB.")
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates,
                         parse_constant=reject_nonfinite)
    if not isinstance(payload, list) or not 1 <= len(payload) <= 1000:
        raise ValueError("Expected one to 1000 settlement requests.")
    requests = tuple(SettlementRequest.model_validate(item) for item in payload)
    ids = [item.prediction_id for item in requests]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate prediction IDs in outcome batch.")
    return requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--journal", type=Path)
    destination.add_argument("--postgres", action="store_true")
    parser.add_argument("--quotes", required=True, type=Path)
    args = parser.parse_args()
    try:
        requests = load_requests(args.quotes)
    except (OSError, ValueError, UnicodeError, RecursionError):
        parser.exit(2, "Outcome quote batch is unreadable or invalid.\n")
    engine = memory_engine(journal=args.journal, postgres=args.postgres)
    results = []
    try:
        memory = CouncilMemory(engine)
        for request in requests:
            try:
                outcome = memory.resolve(request.prediction_id, request.quote)
                results.append(dict(prediction_id=request.prediction_id, status="recorded", outcome=outcome))
            except (ValueError, MemoryConflict, MemoryIntegrityError, SQLAlchemyError) as error:
                # Exceptions can contain SQL/connection details; expose only type.
                results.append(dict(prediction_id=request.prediction_id, status="blocked",
                                    error_type=type(error).__name__))
    finally:
        engine.dispose()
    print(json.dumps(dict(results=results, execution_authorized=False), indent=2, allow_nan=False))
    return 0 if all(item["status"] == "recorded" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
