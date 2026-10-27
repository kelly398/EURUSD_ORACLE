"""Canonical Council dispatch: availability, eligibility, then staged execution."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from math import isfinite
from time import perf_counter

from backend.agents.agent_registry import AgentRegistry
from backend.agents.contracts import AgentContext, AgentOpinion, AgentStage
from backend.agents.council_inputs import ExecutionMode
from backend.agents.council_manifest import COUNCIL_MANIFEST


class AgentRunStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AgentExecutionResult:
    seat_number: int
    agent_id: str
    agent_name: str
    stage: AgentStage
    status: AgentRunStatus
    duration_ms: float
    opinion: AgentOpinion | None
    error: str | None
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PrimaryCouncilRun:
    as_of: datetime
    executions: tuple[AgentExecutionResult, ...]
    missing_agent_ids: tuple[str, ...]
    expected_agent_count: int = 28

    @property
    def successful_executions(self):
        return tuple(e for e in self.executions if e.status == AgentRunStatus.SUCCESS)

    @property
    def failed_executions(self):
        return tuple(e for e in self.executions if e.status == AgentRunStatus.ERROR)

    @property
    def blocked_executions(self):
        return tuple(e for e in self.executions if e.status == AgentRunStatus.BLOCKED)

    @property
    def opinions(self):
        return tuple(e.opinion for e in self.successful_executions if e.opinion is not None)

    @property
    def succeeded_count(self):
        return len(self.successful_executions)

    @property
    def failed_count(self):
        return len(self.failed_executions)

    @property
    def blocked_count(self):
        return len(self.blocked_executions)

    @property
    def is_complete(self):
        return (self.succeeded_count == self.expected_agent_count
                and not self.failed_count and not self.blocked_count
                and not self.missing_agent_ids)


@dataclass(frozen=True)
class CouncilRun:
    primary: PrimaryCouncilRun
    governance: tuple[AgentExecutionResult, ...]
    synthesis: tuple[AgentExecutionResult, ...]
    context_sha256: str
    mode: ExecutionMode = ExecutionMode.PRODUCTION
    missing_agent_ids: tuple[str, ...] = ()

    @property
    def executions(self):
        return self.primary.executions + self.governance + self.synthesis

    @property
    def blocked_executions(self):
        return tuple(e for e in self.executions if e.status == AgentRunStatus.BLOCKED)

    @property
    def failed_executions(self):
        return tuple(e for e in self.executions if e.status == AgentRunStatus.ERROR)

    @property
    def is_complete(self):
        return (self.primary.is_complete and len(self.governance) == 2
                and len(self.synthesis) == 1 and len(self.executions) == 31
                and not self.missing_agent_ids
                and all(e.status == AgentRunStatus.SUCCESS for e in self.executions))

    @property
    def final_opinion(self):
        return self.synthesis[0].opinion if len(self.synthesis) == 1 else None

    @property
    def decision(self):
        if not self.is_complete or self.final_opinion is None:
            return "hold"
        return self.final_opinion.evidence.get("decision", "hold")

    @property
    def execution_authorized(self):
        # This Council has no broker execution/admission capability.
        return False


class CouncilRunner:
    def __init__(self, registry: AgentRegistry, max_workers=8, *, mode=ExecutionMode.PRODUCTION):
        if not isinstance(registry, AgentRegistry):
            raise TypeError("CouncilRunner requires AgentRegistry.")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer.")
        self.registry = registry
        self.max_workers = max_workers
        self.mode = ExecutionMode(mode)
        def identity(spec):
            return spec.seat_number, spec.agent_id, spec.agent_name, spec.category, spec.stage
        actual = sorted((identity(spec) for spec in registry.specs), key=lambda value: value[0])
        if actual != [identity(spec) for spec in COUNCIL_MANIFEST]:
            raise ValueError("Council execution requires canonical seat identities and stages.")

    @staticmethod
    def _fresh(context, **updates):
        if not isinstance(context, AgentContext):
            raise TypeError("Council execution requires AgentContext.")
        return type(context).model_validate({**context.model_dump(), **updates})

    def _validate_context(self, context):
        context = self._fresh(context)
        if context.symbol != "EURUSD" or context.timeframe != "M5":
            raise ValueError("This Council and its frozen model contracts require EURUSD M5.")
        if context.execution_mode != self.mode:
            raise ValueError("Context and runner execution modes do not match.")
        return context

    @staticmethod
    def _execute_agent(spec, agent, context):
        started = perf_counter()
        try:
            opinion = agent.run(context)
            opinion = AgentOpinion.model_validate(opinion.model_dump())
            if ((opinion.agent_id, opinion.agent_name, opinion.category, opinion.as_of, opinion.signal_type)
                    != (spec.agent_id, spec.agent_name, spec.category, context.as_of, agent.signal_type)):
                raise ValueError("Agent opinion identity, role or timestamp does not match dispatch.")
            if spec.stage != AgentStage.PRIMARY and opinion.signal_type.value != "governance":
                raise ValueError("Governance/synthesis dispatch requires a governance opinion.")
            if spec.stage == AgentStage.PRIMARY and opinion.signal_type.value == "governance":
                raise ValueError("Primary dispatch cannot produce a governance opinion.")
            if any(isinstance(value, float) and not isfinite(value) for value in opinion.evidence.values()):
                raise ValueError("Agent returned nonfinite evidence.")
            if ((opinion.direction.value == "long" and opinion.score <= 0)
                    or (opinion.direction.value == "short" and opinion.score >= 0)):
                raise ValueError("Agent direction and score disagree.")
            return AgentExecutionResult(spec.seat_number, spec.agent_id, spec.agent_name,
                spec.stage, AgentRunStatus.SUCCESS, (perf_counter()-started)*1000, opinion, None)
        except Exception as error:
            return AgentExecutionResult(spec.seat_number, spec.agent_id, spec.agent_name,
                spec.stage, AgentRunStatus.ERROR, (perf_counter()-started)*1000,
                None, f"{type(error).__name__}: {error}")

    def _dispatch(self, spec, agent, context):
        eligibility = self.registry.eligibility(spec.agent_id, context, mode=self.mode)
        if not eligibility.allowed:
            return AgentExecutionResult(spec.seat_number, spec.agent_id, spec.agent_name,
                spec.stage, AgentRunStatus.BLOCKED, 0.0, None, None, eligibility.blockers)
        return self._execute_agent(spec, agent, context)

    def _run_stage(self, stage, context, *, parallel=False):
        pairs = self.registry.get_registered_by_stage(stage)
        before = context.model_dump_json()
        if parallel and pairs:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(pairs)),
                                    thread_name_prefix="oracle-agent") as executor:
                futures = [executor.submit(self._dispatch, spec, agent, context) for spec, agent in pairs]
                results = tuple(future.result() for future in as_completed(futures))
        else:
            results = tuple(self._dispatch(spec, agent, context) for spec, agent in pairs)
        if context.model_dump_json() != before:
            raise RuntimeError("An agent mutated shared Council input.")
        return tuple(sorted(results, key=lambda result: result.seat_number))

    def run_primary(self, context: AgentContext, require_all_registered=False):
        context = self._validate_context(context)
        specs = self.registry.get_specs_by_stage(AgentStage.PRIMARY)
        missing = tuple(spec.agent_id for spec in specs if not self.registry.is_registered(spec.agent_id))
        if require_all_registered and missing:
            raise RuntimeError("Primary Council is incomplete. Missing agents: " + ", ".join(missing))
        return PrimaryCouncilRun(context.as_of, self._run_stage(AgentStage.PRIMARY, context, parallel=True),
                                 missing, expected_agent_count=len(specs))

    def run(self, context: AgentContext, *, require_all_registered=True):
        """Run primary -> governance -> synthesis through the canonical registry.

        Only successful opinions are handed forward. Missing/blocked/error seats
        remain explicit, and the overall decision is HOLD unless all seats finish.
        """
        context = self._validate_context(context)
        if context.primary_agent_opinions or context.governance_opinions:
            raise ValueError("Staged Council inputs must not contain precomputed opinions.")
        missing = self.registry.missing_agent_ids
        if require_all_registered and missing:
            raise RuntimeError("Full Council registration is incomplete: " + ", ".join(missing))
        initial = context.model_dump_json()
        primary = self.run_primary(context, require_all_registered=require_all_registered)
        governance_context = self._fresh(context, primary_agent_opinions=primary.opinions)
        governance = self._run_stage(AgentStage.GOVERNANCE, governance_context)
        opinions = tuple(e.opinion for e in governance
                         if e.status == AgentRunStatus.SUCCESS and e.opinion is not None)
        synthesis_context = self._fresh(governance_context, governance_opinions=opinions)
        synthesis = self._run_stage(AgentStage.SYNTHESIS, synthesis_context)
        return CouncilRun(primary, governance, synthesis, sha256(initial.encode()).hexdigest(), self.mode, missing)
