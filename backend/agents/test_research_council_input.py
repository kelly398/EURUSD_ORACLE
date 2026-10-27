"""Untrusted JSON transport boundary tests for the offline Council entry point."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.agents.run_research_council import load_context
from backend.agents.test_remaining_council_seats import context


class ResearchInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'context.json'

    def test_valid_context_round_trip(self):
        original=context()
        self.path.write_text(original.model_dump_json(),encoding='utf-8')
        result=load_context(self.path)
        self.assertEqual(result,original)
        self.assertFalse(result.execution_authorized)

    def test_duplicate_keys_rejected_at_any_depth(self):
        for data in ('{"as_of":1,"as_of":2}', '{"nested":{"key":1,"key":2}}'):
            with self.subTest(data=data),self.assertRaises(ValueError):
                self.path.write_text(data,encoding='utf-8')
                load_context(self.path)

    def test_nonfinite_json_constants_rejected(self):
        for value in ('NaN','Infinity','-Infinity'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.path.write_text('{"equity":'+value+'}',encoding='utf-8')
                load_context(self.path)

    def test_byte_limit_enforced_before_parsing(self):
        self.path.write_bytes(b' '*101)
        with patch('backend.agents.run_research_council.MAX_CONTEXT_BYTES',100):
            with self.assertRaises(ValueError):
                load_context(self.path)

    def test_excessive_nesting_is_a_bounded_input_error(self):
        self.path.write_bytes(b'['*2000+b']'*2000)
        with self.assertRaises(ValueError):
            load_context(self.path)

    def test_caller_cannot_override_research_mode(self):
        self.path.write_text(context().model_dump_json().replace('"research"','"live"'),encoding='utf-8')
        with self.assertRaises(ValueError):
            load_context(self.path)


if __name__=='__main__':
    unittest.main(verbosity=2)
