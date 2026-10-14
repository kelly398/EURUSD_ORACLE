
"""F4.5.1: Offline, non-activating Council Seat 25 readiness audit.

Run: python -m backend.agents.test_fed_council_readiness

The only Fed registration occurs in a fresh in-memory AgentRegistry
created by the tests. This does not change register_core_agents.
No broker, network, database, or HMM evaluation is invoked.
"""

import ast
from pathlib import Path
import unittest

from backend.agents.agent_registry import AgentRegistry
from backend.agents.contracts import (
    AgentCategory,
    AgentContext,
    AgentDirection,
    AgentSignalType,
    AgentStage,
)
from backend.agents.council_runner import AgentRunStatus, CouncilRunner
from backend.agents.implementations.fed_agent import FedAgent
from backend.agents.test_fed_agent import fed_pair, make_context


class FedCouncilReadinessTests(unittest.TestCase):

    def test_01_manifest_has_31_unique_ordered_seats(self):
        specs = AgentRegistry().specs
        self.assertEqual(len(specs), 31)
        self.assertEqual(
            [s.seat_number for s in specs],
            list(range(1, 32)),
        )
        self.assertEqual(len({s.agent_id for s in specs}), 31)

    def test_02_fed_seat_manifest_contract(self):
        spec = AgentRegistry().get_spec("fed")
        self.assertEqual(spec.seat_number, 25)
        self.assertEqual(spec.agent_name, "Federal Reserve")
        self.assertEqual(spec.category, AgentCategory.MACRO)
        self.assertEqual(spec.stage, AgentStage.PRIMARY)
        self.assertEqual(
            spec.required_inputs,
            ("fed_data", "economic_calendar"),
        )

    def test_03_manifest_fields_exist_in_context_contract(self):
        spec = AgentRegistry().get_spec("fed")
        self.assertTrue(
            set(spec.required_inputs).issubset(
                AgentContext.model_fields
            )
        )

    def test_04_fed_agent_matches_contextual_seat(self):
        agent = FedAgent()
        spec = AgentRegistry().get_spec("fed")

        self.assertEqual(agent.agent_id, spec.agent_id)
        self.assertEqual(agent.agent_name, spec.agent_name)
        self.assertEqual(agent.category, spec.category)
        self.assertEqual(
            agent.signal_type,
            AgentSignalType.CONTEXTUAL,
        )

    def test_05_new_registry_does_not_activate_fed(self):
        registry = AgentRegistry()

        self.assertFalse(registry.is_registered("fed"))
        self.assertIn("fed", registry.missing_agent_ids)
        self.assertEqual(registry.implemented_count, 0)

    def test_06_isolated_registry_accepts_matching_fed_agent(self):
        registry = AgentRegistry()
        agent = FedAgent()

        registry.register(agent)

        self.assertTrue(registry.is_registered("fed"))
        self.assertIs(registry.get_agent("fed"), agent)
        self.assertEqual(registry.implemented_count, 1)

        # Registration is local to this disposable registry.
        self.assertFalse(
            AgentRegistry().is_registered("fed")
        )

    def test_07_duplicate_registration_rejected(self):
        registry = AgentRegistry()
        registry.register(FedAgent())

        with self.assertRaisesRegex(
            ValueError,
            "already registered",
        ):
            registry.register(FedAgent())

    def test_08_manifest_name_mismatch_rejected(self):

        class WrongNameFed(FedAgent):
            agent_name = "Wrong Federal Reserve"

        registry = AgentRegistry()

        with self.assertRaisesRegex(
            ValueError,
            "name does not match",
        ):
            registry.register(WrongNameFed())

        self.assertFalse(registry.is_registered("fed"))

    def test_09_incomplete_primary_council_rejected_when_required(self):
        registry = AgentRegistry()
        registry.register(FedAgent())

        runner = CouncilRunner(
            registry,
            max_workers=1,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Council is incomplete",
        ):
            runner.run_primary(
                make_context(),
                require_all_registered=True,
            )

    def test_10_missing_fed_input_is_a_runner_error_not_an_opinion(self):
        registry = AgentRegistry()
        registry.register(FedAgent())

        result = CouncilRunner(
            registry,
            max_workers=1,
        ).run_primary(
            make_context()
        )

        self.assertEqual(len(result.executions), 1)

        execution = result.executions[0]

        self.assertEqual(execution.seat_number, 25)
        self.assertEqual(
            execution.status,
            AgentRunStatus.ERROR,
        )
        self.assertIsNone(execution.opinion)
        self.assertIn(
            "requires causal Fed",
            execution.error,
        )
        self.assertFalse(result.is_complete)

    def test_11_synthetic_fed_opinion_remains_neutral_and_incomplete(self):
        registry = AgentRegistry()
        registry.register(FedAgent())

        result = CouncilRunner(
            registry,
            max_workers=1,
        ).run_primary(
            make_context(fed_data=fed_pair())
        )

        self.assertEqual(len(result.executions), 1)

        execution = result.executions[0]

        self.assertEqual(
            execution.status,
            AgentRunStatus.SUCCESS,
        )
        self.assertEqual(
            execution.opinion.direction,
            AgentDirection.NEUTRAL,
        )
        self.assertEqual(
            execution.opinion.signal_type,
            AgentSignalType.CONTEXTUAL,
        )
        self.assertEqual(execution.opinion.score, 0.0)
        self.assertEqual(execution.opinion.confidence, 0.0)
        self.assertFalse(result.is_complete)

    def test_12_empty_calendar_is_not_misreported_as_fed_calendar(self):
        context = make_context(
            fed_data=fed_pair()
        )

        self.assertEqual(
            context.economic_calendar,
            (),
        )

        opinion = FedAgent().run(context)

        self.assertFalse(
            opinion.evidence[
                "fed_calendar_interpretation_available"
            ]
        )
        self.assertFalse(
            opinion.evidence[
                "historical_replay_admission_authorized"
            ]
        )

    def test_13_core_registration_source_does_not_activate_fed(self):
        # Parse rather than import the core registration module.
        # Importing it would load other completed agent implementations.
        source = Path(__file__).with_name(
            "register_core_agents.py"
        )

        tree = ast.parse(
            source.read_text(encoding="utf-8")
        )

        constructed = []

        for node in ast.walk(tree):

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Call)
                and isinstance(node.args[0].func, ast.Name)
            ):
                constructed.append(
                    node.args[0].func.id
                )

        self.assertEqual(len(constructed), 24)
        self.assertIn("ECBAgent", constructed)
        self.assertNotIn("FedAgent", constructed)

    def test_14_live_factory_does_not_silently_inject_fed_data(self):
        source = Path(__file__).with_name(
            "context_factory.py"
        )

        tree = ast.parse(
            source.read_text(encoding="utf-8")
        )

        constructor_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "AgentContext"
        ]

        self.assertTrue(constructor_calls)

        self.assertTrue(
            all(
                "fed_data" not in {
                    kw.arg for kw in call.keywords
                }
                for call in constructor_calls
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
