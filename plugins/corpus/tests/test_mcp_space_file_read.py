from __future__ import annotations

import unittest

from corpus.errors import BudgetExceededError
from corpus.mcp_server import _mcp_space_file_read
from corpus.mcp_server_bounded import _bound_space_file_read_result
from corpus.service import CORPUS_READ_MAX_SERIALIZED_BYTES


def _project(result: dict, *, max_chars: int) -> dict:
    return _mcp_space_file_read(
        _bound_space_file_read_result(result, max_chars=max_chars)
    )


class SpaceFileReadProjectionTests(unittest.TestCase):
    def test_live_utf8_content_is_bounded_and_reports_truncation(self) -> None:
        source = {
            "source_kind": "live_file",
            "encoding": "utf8",
            "content": "가나다라마바사",
            "content_is_untrusted": True,
            "file": {"version_token": "v1:test"},
        }

        result = _project(source, max_chars=4)

        self.assertEqual(result["untrusted_content"], "가나다라")
        self.assertEqual(result["returned_chars"], 4)
        self.assertEqual(result["total_chars"], 7)
        self.assertTrue(result["truncated"])
        self.assertNotIn("content", result)
        self.assertNotIn("content_is_untrusted", result)
        self.assertEqual(source["content"], "가나다라마바사")

    def test_live_utf8_content_reports_complete_read(self) -> None:
        result = _project(
            {
                "source_kind": "live_file",
                "encoding": "utf8",
                "content": "complete",
                "content_is_untrusted": True,
            },
            max_chars=100,
        )

        self.assertEqual(result["untrusted_content"], "complete")
        self.assertEqual(result["returned_chars"], 8)
        self.assertEqual(result["total_chars"], 8)
        self.assertFalse(result["truncated"])

    def test_indexed_source_projection_is_preserved(self) -> None:
        result = _project(
            {
                "source_kind": "indexed_source",
                "count": 1,
                "units": [{"untrusted_content": "exact unit"}],
                "content_is_untrusted": True,
            },
            max_chars=1000,
        )

        self.assertEqual(result["units"], [{"untrusted_content": "exact unit"}])
        self.assertNotIn("content_is_untrusted", result)
        self.assertNotIn("truncated", result)

    def test_oversized_untruncated_payload_fails_with_budget_error(self) -> None:
        with self.assertRaises(BudgetExceededError):
            _bound_space_file_read_result(
                {
                    "source_kind": "live_file",
                    "encoding": "base64",
                    "content": "A" * CORPUS_READ_MAX_SERIALIZED_BYTES,
                    "content_is_untrusted": True,
                },
                max_chars=1000,
            )


if __name__ == "__main__":
    unittest.main()
