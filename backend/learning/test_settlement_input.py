"""Bounded, unambiguous data-only settlement input parsing."""
import json
from pathlib import Path
import tempfile
import unittest

from backend.learning.settle_outcomes import load_requests
from backend.learning.test_memory import quote


class SettlementInputTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/"quotes.json"

    def load(self, raw):
        self.path.write_text(raw, encoding="utf-8")
        return load_requests(self.path)

    def test_valid_request_preserves_quote_origin_and_identity(self):
        raw = json.dumps([dict(prediction_id="a"*64, quote=quote().model_dump(mode="json"))])
        self.assertEqual(self.load(raw)[0].quote.origin, "synthetic")

    def test_duplicate_keys_nonfinite_oversize_and_untyped_batches_fail(self):
        request = dict(prediction_id="a"*64, quote=quote().model_dump(mode="json"))
        cases = ('[{"prediction_id":"a","prediction_id":"b"}]', '[NaN]', '{}', '[]',
                 json.dumps([request, request]), ' '*1048577)
        for raw in cases:
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                self.load(raw)


if __name__ == "__main__":
    unittest.main()
