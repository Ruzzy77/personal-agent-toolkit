from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "refresh-corpus-sources"
    / "scripts"
    / "refresh_sources.py"
)
SPEC = importlib.util.spec_from_file_location("refresh_sources", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
refresh_sources = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh_sources)


def warning(document_id: str) -> dict:
    return {
        "kind": "extraction_issue",
        "document_id": document_id,
        "revision_id": f"revision-{document_id}",
        "adapter_id": "document-files",
        "adapter_version": "2",
        "config_hash": "config",
        "issue_code": "content_gap",
        "impact": "content_gap",
        "severity": "warning",
        "occurrence_count": 1,
    }


def result(*items: dict) -> list[dict]:
    return [{"corpus_id": "research", "warning_items": list(items)}]


class RefreshWarningStateTest(unittest.TestCase):
    def test_same_total_with_a_different_document_is_a_change(self) -> None:
        baseline, _ = refresh_sources._warning_delta(
            {"schema_version": 1, "corpora": {}},
            result(warning("a")),
            reset=False,
        )
        _, delta = refresh_sources._warning_delta(
            baseline,
            result(warning("b")),
            reset=False,
        )

        self.assertEqual(delta["summary"]["new"], 1)
        self.assertEqual(delta["summary"]["resolved"], 1)

    def test_resolved_warning_is_marked_when_it_reappears(self) -> None:
        baseline, _ = refresh_sources._warning_delta(
            {"schema_version": 1, "corpora": {}},
            result(warning("a")),
            reset=False,
        )
        resolved, _ = refresh_sources._warning_delta(
            baseline,
            result(),
            reset=False,
        )
        _, delta = refresh_sources._warning_delta(
            resolved,
            result(warning("a")),
            reset=False,
        )

        self.assertEqual(delta["summary"]["reappeared"], 1)
        self.assertEqual(delta["summary"]["new"], 0)

    def test_failed_refresh_does_not_replace_the_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "warning-state.json"
            refresh_sources._update_warning_state(
                path,
                result(warning("a")),
                reset=False,
                refresh_ok=True,
            )
            before = path.read_bytes()

            update = refresh_sources._update_warning_state(
                path,
                result(warning("b")),
                reset=False,
                refresh_ok=False,
            )

            self.assertFalse(update["updated"])
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
