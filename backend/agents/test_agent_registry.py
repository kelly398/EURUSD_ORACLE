from backend.agents.agent_registry import (
    AgentRegistry
)

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentStage
)


class TestTrendMaster(
    BaseAgent
):

    agent_id = "trend_master"

    agent_name = "Trend Master"

    category = (
        AgentCategory.TECHNICAL
    )

    version = "0.0.1-test"


    def analyze(
        self,
        context
    ):

        return self.build_opinion(

            context=context,

            score=0.0,

            confidence=0.0,

            rationale=(
                "Registry test only."
            ),

            evidence={}
        )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COUNCIL REGISTRY TEST"
    )

    print(
        "=" * 70
    )


    registry = (
        AgentRegistry()
    )


    specs = (
        registry.specs
    )


    print(
        f"\nCouncil seats: "
        f"{len(specs)}"
    )


    primary = (
        registry.get_specs_by_stage(
            AgentStage.PRIMARY
        )
    )


    governance = (
        registry.get_specs_by_stage(
            AgentStage.GOVERNANCE
        )
    )


    synthesis = (
        registry.get_specs_by_stage(
            AgentStage.SYNTHESIS
        )
    )


    print(
        f"Primary agents: "
        f"{len(primary)}"
    )

    print(
        f"Governance agents: "
        f"{len(governance)}"
    )

    print(
        f"Synthesis agents: "
        f"{len(synthesis)}"
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "COUNCIL MANIFEST"
    )

    print(
        "-" * 70
    )


    for spec in specs:

        print(

            f"{spec.seat_number:02d} | "

            f"{spec.agent_id:<24} | "

            f"{spec.stage.value:<10} | "

            f"{spec.category.value:<16} | "

            f"{spec.agent_name}"
        )


    # ==================================
    # MANIFEST ASSERTIONS
    # ==================================

    assert len(specs) == 31

    assert len(primary) == 28

    assert len(governance) == 2

    assert len(synthesis) == 1


    assert (

        [
            spec.seat_number
            for spec in specs
        ]

        == list(
            range(
                1,
                32
            )
        )
    )


    assert (
        len(
            {
                spec.agent_id
                for spec in specs
            }
        )
        == 31
    )


    # ==================================
    # REGISTRATION TEST
    # ==================================

    trend_agent = (
        TestTrendMaster()
    )


    registry.register(
        trend_agent
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "REGISTRATION TEST"
    )

    print(
        "-" * 70
    )


    print(
        "Registered: "
        f"{trend_agent.agent_name}"
    )


    print(
        "Implemented agents: "
        f"{registry.implemented_count}"
    )


    print(
        "Remaining agents: "
        f"{len(registry.missing_agent_ids)}"
    )


    assert (
        registry.implemented_count
        == 1
    )


    assert (
        registry.get_agent(
            "trend_master"
        )
        is trend_agent
    )


    assert (
        len(
            registry.missing_agent_ids
        )
        == 30
    )


    # ==================================
    # KEY SEAT ASSERTIONS
    # ==================================

    assert (
        registry.get_spec(
            "devils_advocate"
        ).seat_number
        == 29
    )


    assert (
        registry.get_spec(
            "risk_officer"
        ).seat_number
        == 30
    )


    assert (
        registry.get_spec(
            "chief_strategist"
        ).seat_number
        == 31
    )


    assert (
        registry.get_spec(
            "chief_strategist"
        ).stage
        == AgentStage.SYNTHESIS
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "COUNCIL REGISTRY "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    