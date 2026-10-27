"""Atomic, idempotent Council records and delayed, append-only outcomes.

Only these tables are created explicitly. No existing market tables are modified.
The API exposes inserts and reads, never updates, deletes, fitting or trading.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

from sqlalchemy import select, insert, inspect
from sqlalchemy.exc import IntegrityError

from backend.agents.contracts import AgentContext, AgentSignalType
from backend.agents.council_inputs import ExecutionMode
from backend.agents.council_manifest import COUNCIL_MANIFEST
from backend.agents.council_runner import CouncilRun, AgentRunStatus
from backend.learning.contracts import (RunProvenance, ForecastSpecification, OutcomeQuote,
                                        canonical, digest, encoded, utc)
from backend.learning.schema import metadata, schema_version, cycles, agents, forecasts, outcomes


class MemoryConflict(RuntimeError):
    """An existing identity has different content; nothing was overwritten."""


class MemoryIntegrityError(RuntimeError):
    """Stored content no longer matches its recorded hash."""


@dataclass(frozen=True)
class MemoryReceipt:
    cycle_id: str
    content_sha256: str
    prediction_ids: tuple[str, ...]
    inserted: bool


class CouncilMemory:
    def __init__(self, engine, *, clock=None):
        if engine.dialect.name not in {"sqlite", "postgresql"}:
            raise ValueError("Council memory supports SQLite and PostgreSQL.")
        self.engine = engine
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def initialize(self):
        """Explicit additive installation, not an implicit side effect of run()."""
        expected = set(metadata.tables)
        with self.engine.begin() as connection:
            existing = set(inspect(connection).get_table_names()) & expected
            if existing and existing != expected:
                raise MemoryIntegrityError("Partial memory schema; explicit migration required.")
            if existing:
                versions = tuple(connection.execute(select(schema_version.c.version)).scalars())
                if versions != (1,):
                    raise MemoryIntegrityError("Unsupported memory schema version.")
                for table in metadata.sorted_tables:
                    columns = {c["name"] for c in inspect(connection).get_columns(table.name)}
                    if columns != set(table.c.keys()):
                        raise MemoryIntegrityError("Incompatible memory schema; migration required.")
                    primary = set(inspect(connection).get_pk_constraint(table.name)["constrained_columns"])
                    if primary != {c.name for c in table.primary_key}:
                        raise MemoryIntegrityError("Memory schema primary key mismatch.")
                return
            metadata.create_all(connection)
            connection.execute(insert(schema_version).values(version=1))

    @staticmethod
    def _run_payload(context, result):
        if not isinstance(result, CouncilRun) or result.mode != context.execution_mode:
            raise ValueError("Memory requires the canonical Council run in the context mode.")
        if result.primary.as_of != context.as_of:
            raise ValueError("Run and context analysis timestamps differ.")
        expected_hash = sha256(context.model_dump_json().encode()).hexdigest()
        if result.context_sha256 != expected_hash:
            raise ValueError("Run does not belong to this exact input context.")
        specs = {s.agent_id: s for s in COUNCIL_MANIFEST}
        seen = set()
        for item in result.executions:
            spec = specs.get(item.agent_id)
            if spec is None or item.agent_id in seen or (item.seat_number, item.stage) != (spec.seat_number, spec.stage):
                raise ValueError("Run observation identity does not match the Council manifest.")
            seen.add(item.agent_id)
            if item.status == AgentRunStatus.SUCCESS:
                if item.opinion is None or item.opinion.agent_id != item.agent_id or item.opinion.as_of != context.as_of:
                    raise ValueError("Successful observation requires its matching opinion.")
            elif item.opinion is not None:
                raise ValueError("Blocked/failed observations cannot carry opinions.")
        payload = asdict(result)
        # Durations vary between identical replays and are not prediction identity.
        payload.pop("context_sha256")
        for item in payload["primary"]["executions"] + payload["governance"] + payload["synthesis"]:
            item.pop("duration_ms")
        payload.update(decision=result.decision, is_complete=result.is_complete,
                       execution_authorized=False)
        return canonical(payload)

    def record_cycle(self, context, result, provenance, *, forecast_specs=None):
        context = AgentContext.model_validate(context.model_dump())
        provenance = RunProvenance.model_validate(provenance.model_dump())
        now = utc(self._clock())
        if utc(context.as_of) > now:
            raise ValueError("Cannot record a future analysis.")
        if context.primary_agent_opinions or context.governance_opinions:
            raise ValueError("Record the original context, before stage handoffs.")
        if provenance.origin == "live" and context.execution_mode != ExecutionMode.PRODUCTION:
            raise ValueError("Research runs cannot be labeled live.")
        run = self._run_payload(context, result)
        target_specs = dict(forecast_specs or {})
        primary_ids = {s.agent_id for s in COUNCIL_MANIFEST if s.stage.value == "primary"}
        if not set(target_specs) <= primary_ids:
            raise ValueError("Forecast specifications require canonical primary agent IDs.")
        target_specs = {key: ForecastSpecification.model_validate(value.model_dump())
                        for key, value in target_specs.items()}
        identity = dict(scope=provenance.scope, symbol=context.symbol,
                        timeframe=context.timeframe, as_of=context.as_of,
                        mode=context.execution_mode)
        cycle_id = digest(identity)
        payload = dict(schema_version=1, context=context.model_dump(), run=run,
                       provenance=provenance.model_dump(), forecast_specs=target_specs)
        content_hash = digest(payload)
        # A restart after the horizon may retry a previously committed live cycle.
        # First-writer time is retained; this never creates a late live forecast.
        with self.engine.connect() as connection:
            previous = connection.execute(select(cycles).where(cycles.c.cycle_id == cycle_id)).mappings().first()
            if previous is not None:
                self._checked_cycle(previous)
                if previous["content_sha256"] != content_hash:
                    raise MemoryConflict("Cycle already exists with different inputs, outputs or provenance.")
                prediction_ids = tuple(connection.execute(select(forecasts.c.prediction_id)
                    .where(forecasts.c.cycle_id == cycle_id).order_by(forecasts.c.agent_id)).scalars())
                return MemoryReceipt(cycle_id, content_hash, prediction_ids, False)
        prediction_rows = []
        for execution in result.primary.executions:
            specification = target_specs.get(execution.agent_id)
            if specification is None or execution.status != AgentRunStatus.SUCCESS:
                continue
            opinion = execution.opinion
            if opinion.signal_type != AgentSignalType.DIRECTIONAL:
                raise ValueError("Contextual evidence must not be scored as a directional forecast.")
            if not context.ticks:
                raise ValueError("Quote-based forecasts require a causal entry quote.")
            entry = context.ticks[-1]
            if not 0 <= (context.as_of - entry.timestamp).total_seconds() <= specification.max_quote_age_seconds:
                raise ValueError("Entry quote is too old for this forecast target.")
            if entry.ask < entry.bid or entry.bid <= 0:
                raise ValueError("Invalid entry quote.")
            target_at = utc(context.as_of) + timedelta(seconds=specification.horizon_seconds)
            prospective = now < target_at
            if provenance.origin == "live" and not prospective:
                raise ValueError("A live forecast must be committed before its outcome matures.")
            prediction_id = digest(dict(cycle_id=cycle_id, agent_id=execution.agent_id))
            forecast = dict(specification=specification.model_dump(),
                            entry=dict(timestamp=entry.timestamp, bid=entry.bid, ask=entry.ask),
                            opinion=opinion.model_dump())
            prediction_rows.append(dict(prediction_id=prediction_id, cycle_id=cycle_id,
                agent_id=execution.agent_id, target_at=target_at, recorded_at=now,
                prospective=prospective, direction=opinion.direction.value,
                forecast_json=encoded(forecast)))
        receipt = MemoryReceipt(cycle_id, content_hash,
                                tuple(r["prediction_id"] for r in sorted(prediction_rows, key=lambda r: r["agent_id"])), True)
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(cycles).values(
                    cycle_id=cycle_id, scope=provenance.scope, origin=provenance.origin,
                    symbol=context.symbol, timeframe=context.timeframe, as_of=utc(context.as_of),
                    recorded_at=now, mode=context.execution_mode.value,
                    decision=result.decision, is_complete=result.is_complete,
                    context_sha256=digest(context), content_sha256=content_hash,
                    payload_json=encoded(payload), timings_json=encoded({
                        e.agent_id: e.duration_ms for e in result.executions})))
                if result.executions:
                    connection.execute(insert(agents), [dict(cycle_id=cycle_id,
                        agent_id=e.agent_id, seat_number=e.seat_number, stage=e.stage.value,
                        status=e.status.value, observation_json=encoded({
                            k: v for k, v in asdict(e).items() if k != "duration_ms"}))
                        for e in result.executions])
                if prediction_rows:
                    connection.execute(insert(forecasts), prediction_rows)
            return receipt
        except IntegrityError:
            with self.engine.connect() as connection:
                previous = connection.execute(select(cycles).where(cycles.c.cycle_id == cycle_id)).mappings().first()
                if previous is None:
                    raise
                self._checked_cycle(previous)
                if previous["content_sha256"] != content_hash:
                    raise MemoryConflict("Cycle already exists with different inputs, outputs or provenance.") from None
            return MemoryReceipt(cycle_id, content_hash, receipt.prediction_ids, False)

    @staticmethod
    def _checked_cycle(row):
        payload = json.loads(row["payload_json"])
        if digest(payload) != row["content_sha256"]:
            raise MemoryIntegrityError("Decision snapshot hash mismatch.")
        return payload

    def load_cycle(self, cycle_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(cycles).where(cycles.c.cycle_id == cycle_id)).mappings().one()
            return self._checked_cycle(row)

    def pending(self, *, as_of, scope, origin, limit=100):
        as_of = utc(as_of)
        if as_of > utc(self._clock()):
            raise ValueError("Cannot request future outcomes.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Outcome batch size must be between 1 and 1000.")
        statement = select(forecasts.c.prediction_id).join(cycles).outerjoin(outcomes).where(
            cycles.c.scope == scope, cycles.c.origin == origin,
            forecasts.c.recorded_at <= as_of, forecasts.c.target_at <= as_of,
            outcomes.c.prediction_id.is_(None)).order_by(forecasts.c.target_at, forecasts.c.prediction_id).limit(limit)
        with self.engine.connect() as connection:
            return tuple(connection.execute(statement).scalars())

    def resolve(self, prediction_id, quote):
        quote = OutcomeQuote.model_validate(quote.model_dump())
        now = utc(self._clock())
        with self.engine.connect() as connection:
            row = connection.execute(select(forecasts, cycles.c.payload_json, cycles.c.content_sha256)
                .join(cycles).where(forecasts.c.prediction_id == prediction_id)).mappings().one()
        cycle = self._checked_cycle(row)
        if quote.origin != cycle["provenance"]["origin"]:
            raise ValueError("Outcome quote origin does not match its decision scope.")
        forecast = json.loads(row["forecast_json"])
        expected = dict(specification=cycle["forecast_specs"][row["agent_id"]],
            entry=forecast["entry"], opinion=forecast["opinion"])
        # Validate the child prediction against the hashed parent snapshot.
        original = next(e["opinion"] for e in cycle["run"]["primary"]["executions"]
                        if e["agent_id"] == row["agent_id"])
        context = cycle["context"]
        entry = context["ticks"][-1]
        expected["entry"] = {k: entry[k] for k in ("timestamp", "bid", "ask")}
        expected["opinion"] = original
        if encoded(expected) != encoded(forecast) or row["direction"] != original["direction"]:
            raise MemoryIntegrityError("Forecast differs from its hashed decision snapshot.")
        specification = ForecastSpecification.model_validate(forecast["specification"])
        target_at = utc(datetime.fromisoformat(context["as_of"])) + timedelta(seconds=specification.horizon_seconds)
        if target_at != row["target_at"]:
            raise MemoryIntegrityError("Forecast horizon changed after commitment.")
        if now < target_at or now < row["recorded_at"] or utc(quote.available_at) > now:
            raise ValueError("Outcome has not matured or its source is not yet available.")
        age = (target_at - utc(quote.timestamp)).total_seconds()
        if not 0 <= age <= specification.max_quote_age_seconds:
            raise ValueError("Outcome requires a quote at or just before its target time.")
        entry = forecast["entry"]
        bid0, ask0 = Decimal(str(entry["bid"])), Decimal(str(entry["ask"]))
        bid1, ask1 = Decimal(str(quote.bid)), Decimal(str(quote.ask))
        pip = Decimal(str(specification.pip_size))
        mid_move = (bid1 + ask1 - bid0 - ask0) / (2 * pip)
        band = Decimal(str(specification.neutral_band_pips))
        label = "long" if mid_move > band else "short" if mid_move < -band else "neutral"
        direction = row["direction"]
        gross = (bid1 - ask0) / pip if direction == "long" else (bid0 - ask1) / pip if direction == "short" else None
        net = gross - Decimal(str(specification.round_trip_commission_pips)) if gross is not None else None
        payload = dict(schema_version=1, prediction_id=prediction_id, target_at=target_at,
            quote=quote.model_dump(), target_label=label, mid_move_pips=float(mid_move),
            correct=label == direction, hypothetical_net_pips=float(net) if net is not None else None,
            cost_model=specification.cost_model, execution_authorized=False,
            actual_fill=False)
        content_hash = digest(payload)
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(outcomes).values(prediction_id=prediction_id,
                    recorded_at=now, quote_available_at=utc(quote.available_at),
                    content_sha256=content_hash, outcome_json=encoded(payload)))
        except IntegrityError:
            with self.engine.connect() as connection:
                previous = connection.execute(select(outcomes).where(outcomes.c.prediction_id == prediction_id)).mappings().one()
                if digest(json.loads(previous["outcome_json"])) != previous["content_sha256"]:
                    raise MemoryIntegrityError("Stored outcome hash mismatch.") from None
                if previous["content_sha256"] != content_hash:
                    raise MemoryConflict("An outcome already exists; corrections require a new version.") from None
        return canonical(payload)

    def available_outcomes(self, *, as_of, scope, origin, limit=100):
        as_of = utc(as_of)
        if as_of > utc(self._clock()):
            raise ValueError("Cannot read future performance.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Outcome batch size must be between 1 and 1000.")
        statement = select(outcomes).select_from(outcomes.join(forecasts,
            outcomes.c.prediction_id == forecasts.c.prediction_id).join(cycles,
            forecasts.c.cycle_id == cycles.c.cycle_id)).where(
            cycles.c.scope == scope, cycles.c.origin == origin,
            cycles.c.recorded_at <= as_of, forecasts.c.target_at <= as_of,
            outcomes.c.recorded_at <= as_of, outcomes.c.quote_available_at <= as_of
        ).order_by(outcomes.c.recorded_at, outcomes.c.prediction_id).limit(limit)
        with self.engine.connect() as connection:
            result = []
            for row in connection.execute(statement).mappings():
                payload = json.loads(row["outcome_json"])
                if digest(payload) != row["content_sha256"]:
                    raise MemoryIntegrityError("Stored outcome hash mismatch.")
                result.append(payload)
            return tuple(result)
