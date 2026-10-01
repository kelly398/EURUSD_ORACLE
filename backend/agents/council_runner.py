from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from time import perf_counter

from backend.agents.agent_registry import (
    AgentRegistry
)

from backend.agents.contracts import (
    AgentContext,
    AgentOpinion,
    AgentStage
)


class AgentRunStatus(str, Enum):

    SUCCESS = "success"

    ERROR = "error"


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


@dataclass(frozen=True)
class PrimaryCouncilRun:

    as_of: datetime

    executions: tuple[
        AgentExecutionResult,
        ...
    ]

    missing_agent_ids: tuple[
        str,
        ...
    ]

    expected_agent_count: int = 28


    @property
    def successful_executions(self):

        return tuple(

            execution

            for execution
            in self.executions

            if (
                execution.status
                == AgentRunStatus.SUCCESS
            )
        )


    @property
    def failed_executions(self):

        return tuple(

            execution

            for execution
            in self.executions

            if (
                execution.status
                == AgentRunStatus.ERROR
            )
        )


    @property
    def opinions(self):

        return tuple(

            execution.opinion

            for execution
            in self.successful_executions

            if (
                execution.opinion
                is not None
            )
        )


    @property
    def succeeded_count(self):

        return len(
            self.successful_executions
        )


    @property
    def failed_count(self):

        return len(
            self.failed_executions
        )


    @property
    def is_complete(self):

        return (

            self.succeeded_count
            == self.expected_agent_count

            and

            self.failed_count == 0

            and

            len(
                self.missing_agent_ids
            )
            == 0
        )


class CouncilRunner:

    def __init__(
        self,
        registry: AgentRegistry,
        max_workers=8
    ):

        if not isinstance(
            registry,
            AgentRegistry
        ):

            raise TypeError(
                "CouncilRunner requires "
                "AgentRegistry."
            )


        if max_workers <= 0:

            raise ValueError(
                "max_workers must be "
                "greater than zero."
            )


        self.registry = registry

        self.max_workers = (
            max_workers
        )


    @staticmethod
    def _execute_agent(
        spec,
        agent,
        context
    ):

        started = (
            perf_counter()
        )


        try:

            opinion = (
                agent.run(
                    context
                )
            )


            duration_ms = (

                perf_counter()
                - started

            ) * 1000


            return AgentExecutionResult(

                seat_number=
                    spec.seat_number,

                agent_id=
                    spec.agent_id,

                agent_name=
                    spec.agent_name,

                stage=
                    spec.stage,

                status=
                    AgentRunStatus.SUCCESS,

                duration_ms=
                    duration_ms,

                opinion=
                    opinion,

                error=None
            )


        except Exception as error:

            duration_ms = (

                perf_counter()
                - started

            ) * 1000


            return AgentExecutionResult(

                seat_number=
                    spec.seat_number,

                agent_id=
                    spec.agent_id,

                agent_name=
                    spec.agent_name,

                stage=
                    spec.stage,

                status=
                    AgentRunStatus.ERROR,

                duration_ms=
                    duration_ms,

                opinion=None,

                error=(
                    f"{type(error).__name__}: "
                    f"{error}"
                )
            )


    def run_primary(
        self,
        context: AgentContext,
        require_all_registered=False
    ) -> PrimaryCouncilRun:

        if not isinstance(
            context,
            AgentContext
        ):

            raise TypeError(
                "run_primary requires "
                "AgentContext."
            )


        primary_specs = (
            self.registry
            .get_specs_by_stage(
                AgentStage.PRIMARY
            )
        )


        registered_pairs = (
            self.registry
            .get_registered_by_stage(
                AgentStage.PRIMARY
            )
        )


        registered_ids = {

            spec.agent_id

            for spec, agent
            in registered_pairs
        }


        missing_agent_ids = tuple(

            spec.agent_id

            for spec in primary_specs

            if (
                spec.agent_id
                not in registered_ids
            )
        )


        if (
            require_all_registered
            and missing_agent_ids
        ):

            raise RuntimeError(
                "Primary Council is "
                "incomplete. Missing agents: "
                + ", ".join(
                    missing_agent_ids
                )
            )


        if not registered_pairs:

            return PrimaryCouncilRun(

                as_of=
                    context.as_of,

                executions=tuple(),

                missing_agent_ids=
                    missing_agent_ids
            )


        worker_count = min(

            self.max_workers,

            len(
                registered_pairs
            )
        )


        results = []


        with ThreadPoolExecutor(
            max_workers=
                worker_count,

            thread_name_prefix=
                "oracle-agent"
        ) as executor:


            futures = {}


            for spec, agent in registered_pairs:

                future = (
                    executor.submit(

                        self._execute_agent,

                        spec,

                        agent,

                        context
                    )
                )


                futures[
                    future
                ] = spec


            for future in as_completed(
                futures
            ):

                result = (
                    future.result()
                )


                results.append(
                    result
                )


        # Parallel agents finish in an
        # unpredictable order.
        #
        # Consensus must always receive
        # opinions in deterministic
        # Council-seat order.
        results.sort(
            key=lambda item:
                item.seat_number
        )


        return PrimaryCouncilRun(

            as_of=
                context.as_of,

            executions=
                tuple(results),

            missing_agent_ids=
                missing_agent_ids
        )
        