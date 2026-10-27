"""Explicit, bounded JSON entry point for isolated full-Council research.

Usage: python -m backend.agents.run_research_council --context INPUT.json
No network acquisition, training, reserved evaluation or broker calls. Optional
--journal writes a dedicated SQLite decision journal; default remains read-only.
The input is data only, never pickle, executable configuration or instructions.
"""
import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from backend.agents.contracts import AgentContext
from backend.agents.council_inputs import ExecutionMode
from backend.agents.agent_registry import AgentRegistry
from backend.agents.register_core_agents import register_core_agents
from backend.agents.council_runner import CouncilRunner


MAX_CONTEXT_BYTES = 16*1024*1024


def reject_duplicates(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field in research input.")
        result[key]=value
    return result


def reject_nonfinite(value):
    raise ValueError("Nonfinite JSON value.")


def load_context(path):
    with Path(path).open('rb') as stream:
        raw=stream.read(MAX_CONTEXT_BYTES+1)
    if len(raw)>MAX_CONTEXT_BYTES:
        raise ValueError("Research context exceeds 16 MiB limit.")
    try:
        payload=json.loads(raw.decode('utf-8'), object_pairs_hook=reject_duplicates,
                           parse_constant=reject_nonfinite)
    except RecursionError:
        raise ValueError("Research context nesting exceeds parser limits.") from None
    if not isinstance(payload,dict):
        raise ValueError("Research context must be an object.")
    legacy_mode=payload.pop('analysis_mode','research')
    if legacy_mode!='research' or payload.get('execution_mode','research')!='research':
        raise ValueError("Research entry point requires explicit research mode.")
    payload['execution_mode']=ExecutionMode.RESEARCH
    return AgentContext.model_validate(payload)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True,type=Path)
    parser.add_argument('--workers',type=int,default=4,choices=range(1,17))
    parser.add_argument('--journal',type=Path,help='Opt-in SQLite decision journal.')
    parser.add_argument('--journal-postgres',action='store_true',help='Opt-in journal in configured PostgreSQL.')
    parser.add_argument('--scope',help='Stable experiment scope; required with --journal.')
    parser.add_argument('--origin',choices=('synthetic','historical_research'))
    parser.add_argument('--policy-version',default='council_v1')
    parser.add_argument('--forecast-specs',type=Path,help='Explicit agent ID -> forecast specification JSON.')
    args=parser.parse_args()
    try:
        context=load_context(args.context)
    except (OSError,ValueError,ValidationError,UnicodeError):
        # Validation errors can include complete input portfolio records. Keep
        # the CLI error bounded and avoid printing financial/account data.
        parser.exit(2,'Research context is unreadable or violates its typed causal contract.\n')
    registry=register_core_agents(AgentRegistry())
    runner=CouncilRunner(registry,max_workers=args.workers,mode=ExecutionMode.RESEARCH)
    receipt=None
    if args.journal or args.journal_postgres:
        if args.journal and args.journal_postgres:
            parser.error('Choose one journal destination.')
        if not args.scope or not args.origin:
            parser.error('--journal requires --scope and --origin.')
        from backend.learning.database import memory_engine
        from backend.learning.memory import CouncilMemory
        from backend.learning.service import CouncilMemoryService
        from backend.learning.contracts import ForecastSpecification
        specs={}
        if args.forecast_specs:
            try:
                with args.forecast_specs.open('rb') as stream:
                    raw=stream.read(65537)
                if len(raw)>65536:
                    raise ValueError('Forecast specification file too large.')
                data=json.loads(raw.decode('utf-8'),object_pairs_hook=reject_duplicates,parse_constant=reject_nonfinite)
                if not isinstance(data,dict):
                    raise ValueError('Expected an agent specification mapping.')
                specs={key:ForecastSpecification.model_validate(value) for key,value in data.items()}
            except (OSError,ValueError,UnicodeError,RecursionError):
                parser.exit(2,'Forecast specifications are unreadable or invalid.\n')
        from sqlalchemy.exc import SQLAlchemyError
        engine=None
        try:
            engine=memory_engine(journal=args.journal,postgres=args.journal_postgres)
            memory=CouncilMemory(engine)
            memory.initialize()
            recorded=CouncilMemoryService(runner,memory,scope=args.scope,origin=args.origin,
                policy_version=args.policy_version,forecast_specs=specs).run(context)
            result,receipt=recorded.run,recorded.receipt
        except (SQLAlchemyError,ValueError,RuntimeError,OSError):
            parser.exit(2,'Journaled Council run failed; no decision was authorized. Check journal integrity and configuration.\n')
        finally:
            if engine is not None:
                engine.dispose()
    else:
        if args.scope or args.origin or args.forecast_specs:
            parser.error('--scope, --origin and --forecast-specs require --journal.')
        result=runner.run(context)
    record=dict(analysis_mode='research',as_of=context.as_of.isoformat(),
        context_sha256=result.context_sha256,all_seats_completed=result.is_complete,
        execution_authorized=False,decision=result.decision,executions=[dict(seat=e.seat_number,agent_id=e.agent_id,
            stage=e.stage.value,status=e.status.value,error=e.error,blockers=e.blockers,
            opinion=e.opinion.model_dump(mode='json') if e.opinion else None) for e in result.executions])
    if receipt:
        record['memory']=dict(cycle_id=receipt.cycle_id,content_sha256=receipt.content_sha256,
                              inserted=receipt.inserted,prediction_ids=receipt.prediction_ids)
    print(json.dumps(record,indent=2,allow_nan=False))
    return 0 if result.is_complete else 1


if __name__=='__main__':
    raise SystemExit(main())
