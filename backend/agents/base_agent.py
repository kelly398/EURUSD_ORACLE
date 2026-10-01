from abc import (
    ABC,
    abstractmethod
)

from backend.agents.contracts import (
    AgentCategory,
    AgentContext,
    AgentDirection,
    AgentOpinion
)


class BaseAgent(ABC):

    agent_id = ""

    agent_name = ""

    category = None

    version = "1.0.0"

    neutral_threshold = 0.15


    def __init__(self):

        self._validate_metadata()


    def _validate_metadata(self):

        if not self.agent_id:

            raise ValueError(
                "Agent must define "
                "agent_id."
            )


        if not self.agent_name:

            raise ValueError(
                "Agent must define "
                "agent_name."
            )


        if not isinstance(
            self.category,
            AgentCategory
        ):

            raise ValueError(
                "Agent must define a "
                "valid AgentCategory."
            )


        if not (
            0.0
            <= self.neutral_threshold
            < 1.0
        ):

            raise ValueError(
                "neutral_threshold must "
                "be between 0 and 1."
            )


    def _direction_from_score(
        self,
        score
    ):

        if (
            score
            >= self.neutral_threshold
        ):

            return (
                AgentDirection.LONG
            )


        if (
            score
            <= -self.neutral_threshold
        ):

            return (
                AgentDirection.SHORT
            )


        return (
            AgentDirection.NEUTRAL
        )


    def build_opinion(
        self,
        context,
        score,
        confidence,
        rationale,
        evidence=None
    ):

        if not (
            -1.0
            <= score
            <= 1.0
        ):

            raise ValueError(
                "Agent score must be "
                "between -1 and +1."
            )


        if not (
            0.0
            <= confidence
            <= 1.0
        ):

            raise ValueError(
                "Agent confidence must "
                "be between 0 and 1."
            )


        direction = (
            self._direction_from_score(
                score
            )
        )


        return AgentOpinion(

            agent_id=
                self.agent_id,

            agent_name=
                self.agent_name,

            category=
                self.category,

            version=
                self.version,

            as_of=
                context.as_of,

            direction=
                direction,

            score=
                score,

            confidence=
                confidence,

            rationale=
                rationale,

            evidence=(
                evidence
                if evidence is not None
                else {}
            )
        )


    def run(
        self,
        context: AgentContext
    ) -> AgentOpinion:

        if not isinstance(
            context,
            AgentContext
        ):

            raise TypeError(
                "Agent requires "
                "AgentContext."
            )


        opinion = (
            self.analyze(
                context
            )
        )


        if not isinstance(
            opinion,
            AgentOpinion
        ):

            raise TypeError(
                f"{self.agent_name} did "
                "not return AgentOpinion."
            )


        if (
            opinion.agent_id
            != self.agent_id
        ):

            raise ValueError(
                "Agent returned incorrect "
                "agent_id."
            )


        if (
            opinion.agent_name
            != self.agent_name
        ):

            raise ValueError(
                "Agent returned incorrect "
                "agent_name."
            )


        if (
            opinion.category
            != self.category
        ):

            raise ValueError(
                "Agent returned incorrect "
                "category."
            )


        if (
            opinion.as_of
            != context.as_of
        ):

            raise ValueError(
                "Agent opinion timestamp "
                "does not match context."
            )


        return opinion


    @abstractmethod
    def analyze(
        self,
        context: AgentContext
    ) -> AgentOpinion:

        raise NotImplementedError
        