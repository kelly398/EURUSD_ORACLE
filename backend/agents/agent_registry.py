from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentStage
)

from backend.agents.council_manifest import (
    COUNCIL_MANIFEST
)


class AgentRegistry:

    EXPECTED_COUNCIL_SIZE = 31


    def __init__(
        self,
        manifest=COUNCIL_MANIFEST
    ):

        self._manifest = tuple(
            manifest
        )

        self._agents = {}

        self._validate_manifest()

        self._spec_by_id = {

            spec.agent_id: spec

            for spec in self._manifest
        }


    def _validate_manifest(self):

        if (
            len(self._manifest)
            != self.EXPECTED_COUNCIL_SIZE
        ):

            raise ValueError(
                "Council manifest must "
                "contain exactly 31 seats."
            )


        seat_numbers = [

            spec.seat_number

            for spec in self._manifest
        ]


        expected_seats = list(
            range(
                1,
                self.EXPECTED_COUNCIL_SIZE
                + 1
            )
        )


        if (
            sorted(seat_numbers)
            != expected_seats
        ):

            raise ValueError(
                "Council seats must be "
                "exactly numbered 1 through 31."
            )


        agent_ids = [

            spec.agent_id

            for spec in self._manifest
        ]


        if (
            len(agent_ids)
            != len(set(agent_ids))
        ):

            raise ValueError(
                "Council manifest contains "
                "duplicate agent IDs."
            )


        names = [

            spec.agent_name

            for spec in self._manifest
        ]


        if (
            len(names)
            != len(set(names))
        ):

            raise ValueError(
                "Council manifest contains "
                "duplicate agent names."
            )


        primary_count = sum(

            1

            for spec in self._manifest

            if (
                spec.stage
                == AgentStage.PRIMARY
            )
        )


        governance_count = sum(

            1

            for spec in self._manifest

            if (
                spec.stage
                == AgentStage.GOVERNANCE
            )
        )


        synthesis_count = sum(

            1

            for spec in self._manifest

            if (
                spec.stage
                == AgentStage.SYNTHESIS
            )
        )


        if primary_count != 28:

            raise ValueError(
                "Council must contain "
                "28 primary agents."
            )


        if governance_count != 2:

            raise ValueError(
                "Council must contain "
                "2 governance agents."
            )


        if synthesis_count != 1:

            raise ValueError(
                "Council must contain "
                "1 synthesis agent."
            )


    @property
    def specs(self):

        return self._manifest


    @property
    def implemented_count(self):
        """Compatibility name for registered implementation count, not eligibility."""
        return self.registered_count

    @property
    def registered_count(self):
        return len(self._agents)


    @property
    def registered_agent_ids(self):

        return tuple(spec.agent_id for spec in sorted(self._manifest, key=lambda s: s.seat_number)
                     if spec.agent_id in self._agents)


    @property
    def missing_agent_ids(self):

        return tuple(

            spec.agent_id

            for spec in self._manifest

            if (
                spec.agent_id
                not in self._agents
            )
        )


    def is_registered(
        self,
        agent_id
    ):

        return (
            agent_id
            in self._agents
        )


    def get_spec(
        self,
        agent_id
    ):

        try:

            return self._spec_by_id[
                agent_id
            ]

        except KeyError:

            raise KeyError(
                "Unknown Council agent: "
                f"{agent_id}"
            )


    def get_specs_by_stage(
        self,
        stage
    ):

        return tuple(

            spec

            for spec in self._manifest

            if spec.stage == stage
        )


    def get_registered_by_stage(
        self,
        stage
    ):

        pairs = []


        for spec in self._manifest:

            if (
                spec.stage
                != stage
            ):

                continue


            if (
                spec.agent_id
                not in self._agents
            ):

                continue


            pairs.append(
                (
                    spec,
                    self._agents[
                        spec.agent_id
                    ]
                )
            )


        return tuple(sorted(pairs, key=lambda pair: pair[0].seat_number))


    def _validate_candidate(
        self,
        agent: BaseAgent
    ):

        if not isinstance(
            agent,
            BaseAgent
        ):

            raise TypeError(
                "Registry accepts only "
                "BaseAgent instances."
            )


        if (
            agent.agent_id
            not in self._spec_by_id
        ):

            raise ValueError(
                "Agent is not defined in "
                "the Council manifest: "
                f"{agent.agent_id}"
            )


        if (
            agent.agent_id
            in self._agents
        ):

            raise ValueError(
                "Agent already registered: "
                f"{agent.agent_id}"
            )


        spec = self._spec_by_id[
            agent.agent_id
        ]


        if (
            agent.agent_name
            != spec.agent_name
        ):

            raise ValueError(
                "Agent name does not match "
                "Council manifest."
            )


        if (
            agent.category
            != spec.category
        ):

            raise ValueError(
                "Agent category does not match "
                "Council manifest."
            )


        return agent

    def register(self, agent: BaseAgent):
        self.register_many((agent,))

    def register_many(self, agents):
        """Validate the entire batch before adding any implementation."""
        candidates = tuple(agents)
        ids = []
        for agent in candidates:
            self._validate_candidate(agent)
            ids.append(agent.agent_id)
        if len(ids) != len(set(ids)):
            raise ValueError("Registration batch contains duplicate agent IDs.")
        self._agents.update((agent.agent_id, agent) for agent in candidates)

    def eligibility(self, agent_id, context, *, mode="production"):
        """Dispatch readiness is separate from registered implementation availability."""
        from backend.agents.execution_policy import assess_eligibility
        spec = self.get_spec(agent_id)
        if not self.is_registered(agent_id):
            from backend.agents.execution_policy import AgentEligibility
            from backend.agents.council_inputs import ExecutionMode
            return AgentEligibility(agent_id, ExecutionMode(mode), ("IMPLEMENTATION_NOT_REGISTERED",))
        return assess_eligibility(spec, context, mode=mode)

    def get_eligible_by_stage(self, stage, context, *, mode="production"):
        return tuple((spec, agent) for spec, agent in self.get_registered_by_stage(stage)
                     if self.eligibility(spec.agent_id, context, mode=mode).allowed)


    def get_agent(
        self,
        agent_id
    ):

        try:

            return self._agents[
                agent_id
            ]

        except KeyError:

            raise KeyError(
                "Council agent is not "
                "implemented or registered: "
                f"{agent_id}"
            )
