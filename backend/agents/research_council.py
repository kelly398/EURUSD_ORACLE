"""Compatibility research adapters around the canonical catalogue and runner.

There is no separate implementation catalogue, orchestration or eligibility
policy here. New code can use register_core_agents and CouncilRunner directly.
"""
from backend.agents.agent_registry import AgentRegistry
from backend.agents.council_inputs import ExecutionMode
from backend.agents.council_runner import CouncilRunner, CouncilRun


ResearchCouncilRun = CouncilRun


def register_research_seats(registry: AgentRegistry) -> AgentRegistry:
    """Legacy subset adapter; all factories are owned by register_core_agents."""
    from backend.agents.register_core_agents import register_core_agents
    if not isinstance(registry, AgentRegistry):
        raise TypeError("An isolated AgentRegistry is required.")
    remaining = tuple(spec.agent_id for spec in registry.specs if spec.seat_number >= 25)
    return register_core_agents(registry, agent_ids=remaining)


def build_research_registry() -> AgentRegistry:
    from backend.agents.register_core_agents import register_core_agents
    return register_core_agents(AgentRegistry())


class ResearchCouncilRunner(CouncilRunner):
    def __init__(self, registry, max_workers=4):
        super().__init__(registry, max_workers=max_workers, mode=ExecutionMode.RESEARCH)
