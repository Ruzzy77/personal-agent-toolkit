from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from corpus.errors import ContextValidationError
from corpus.service import CorpusService


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _iso_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _codex_records(
    *,
    completed_at: datetime,
    final_text: str = "완료된 답변입니다.",
) -> list[dict]:
    return [
        {
            "type": "session_meta",
            "payload": {
                "id": "codex-session-1",
                "cwd": "/workspace/project",
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": "codex-turn-1",
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "CODEx-visible-message-canary",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "SECRET_TOOL_NAME_CANARY",
                "arguments": "SECRET_TOOL_ARGUMENT_CANARY",
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "phase": "final_answer",
                "message": final_text,
            },
        },
        {
            "type": "event_msg",
            "timestamp": _iso_timestamp(completed_at),
            "payload": {
                "type": "task_complete",
                "turn_id": "codex-turn-1",
                "completed_at": int(completed_at.timestamp()),
            },
        },
    ]


def _claude_records(*, completed_at: datetime) -> list[dict]:
    return [
        {
            "type": "user",
            "uuid": "claude-turn-1",
            "sessionId": "claude-session-1",
            "cwd": "/workspace/project",
            "timestamp": _iso_timestamp(completed_at - timedelta(minutes=1)),
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "Claude-visible-message-canary",
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "sessionId": "claude-session-1",
            "cwd": "/workspace/project",
            "timestamp": _iso_timestamp(completed_at),
            "message": {
                "stop_reason": "end_turn",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "CLAUDE_REASONING_CANARY",
                    },
                    {
                        "type": "tool_use",
                        "name": "CLAUDE_TOOL_CANARY",
                        "input": {"secret": "CLAUDE_TOOL_ARGUMENT_CANARY"},
                    },
                    {
                        "type": "text",
                        "text": "Claude 완료 답변입니다.",
                    },
                ],
            },
        },
    ]


class SessionLinkedSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.completed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
        self.data = base / "private-data"
        self.source = base / "source"
        self.codex_root = base / "codex-sessions"
        self.claude_root = base / "claude-projects"
        self.source.mkdir()
        (self.source / "README.md").write_text("# Project\n", encoding="utf-8")
        self.codex_file = (
            self.codex_root
            / self.completed_at.strftime("%Y")
            / self.completed_at.strftime("%m")
            / "rollout-codex-session-1.jsonl"
        )
        self.claude_file = self.claude_root / "project" / "claude-session-1.jsonl"
        _write_jsonl(
            self.codex_file,
            _codex_records(completed_at=self.completed_at),
        )
        _write_jsonl(
            self.claude_file,
            _claude_records(completed_at=self.completed_at),
        )
        self.environment = mock.patch.dict(
            "os.environ",
            {
                "CORPUS_CODEX_SESSIONS_ROOT": str(self.codex_root),
                "CORPUS_CODEX_ARCHIVED_SESSIONS_ROOT": str(
                    base / "absent-codex-archive"
                ),
                "CORPUS_CLAUDE_PROJECTS_ROOT": str(self.claude_root),
            },
            clear=False,
        )
        self.environment.start()
        self.service = CorpusService(self.data)
        self.service.register(
            corpus_id="project",
            source_root=self.source,
            execution_policy="external_host_allowed",
        )

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def _bind_and_refresh(self, provider: str) -> dict:
        binding_id = f"project-{provider}"
        selector = {
            "cwd_prefix": "/workspace/project",
            "actor": "all",
            "lookback_days": 30,
        }
        if provider == "codex":
            selector["include_archived"] = False
        self.service.corpus_source_update(
            action="bind",
            corpus_id="project",
            binding_id=binding_id,
            payload={
                "provider_kind": provider,
                "selector": selector,
            },
        )
        refreshed = self.service.corpus_source_update(
            action="refresh",
            corpus_id="project",
            binding_id=binding_id,
            payload={},
        )
        self.assertEqual(refreshed["status"], "complete")
        self.assertEqual(refreshed["discovered_record_count"], 1)
        listed = self.service.corpus_source_read(
            corpus_id="project",
            binding_id=binding_id,
        )
        self.assertEqual(listed["returned_count"], 1)
        return listed["records"][0]

    def test_session_observe_rejects_path_shaped_identity_and_locator_values(
        self,
    ) -> None:
        self.service.corpus_source_update(
            action="bind",
            corpus_id="project",
            binding_id="project-codex",
            payload={
                "provider_kind": "codex",
                "selector": {
                    "cwd_prefix": "/workspace/project",
                    "actor": "all",
                    "lookback_days": 30,
                    "include_archived": False,
                },
            },
        )

        def record() -> dict:
            return {
                "external_id": "turn_safe",
                "parent_external_id": "session-safe",
                "occurred_at": "2026-08-03T00:00:00Z",
                "provider_metadata": {
                    "session_id": "session-safe",
                    "turn_id": "turn-safe",
                    "cwd": "/workspace/project",
                    "workspace": "/workspace/project",
                    "actor": "user_task",
                    "task_kind": "codex_turn",
                },
                "locator": {
                    "root_ref": "active",
                    "relative_path": "2026/08/session.jsonl",
                    "session_id": "session-safe",
                    "turn_id": "turn-safe",
                },
                "freshness_identity": "sha256:" + ("a" * 64),
            }

        cases = (
            ("identity", "/Users/private-user/session"),
            ("identity", "C:private-session"),
            ("identity", "file:relative-session"),
            ("locator", "C:/Users/private-user/session.jsonl"),
            ("locator", "C:\\Users\\private-user\\session.jsonl"),
            ("locator", "C:private\\session.jsonl"),
            ("locator", "file:relative-session.jsonl"),
            ("locator", "\\\\server\\share\\session.jsonl"),
            ("locator", "\\rooted\\session.jsonl"),
        )
        for index, (field, canary) in enumerate(cases):
            unsafe = record()
            if field == "identity":
                unsafe["parent_external_id"] = canary
                unsafe["provider_metadata"]["session_id"] = canary
                unsafe["locator"]["session_id"] = canary
            else:
                unsafe["locator"]["relative_path"] = canary
            with (
                self.subTest(field=field, canary=canary),
                self.assertRaises(ContextValidationError),
            ):
                self.service.corpus_source_update(
                    action="observe",
                    corpus_id="project",
                    binding_id="project-codex",
                    payload={
                        "run_id": f"unsafe-path-run-{index}",
                        "records": [unsafe],
                        "complete": True,
                    },
                )

        with self.assertRaises(ContextValidationError):
            self.service.corpus_source_update(
                action="observe",
                corpus_id="project",
                binding_id="project-codex",
                payload={
                    "run_id": "/Users/private-user/run",
                    "records": [],
                    "complete": False,
                },
            )

        database_bytes = (self.data / "contexts.sqlite3").read_bytes()
        self.assertNotIn(b"private-user", database_bytes)

    def test_codex_and_claude_observations_store_no_provider_content(self) -> None:
        codex = self._bind_and_refresh("codex")
        claude = self._bind_and_refresh("claude")

        self.assertEqual(codex["provider_kind"], "codex")
        self.assertEqual(codex["provider_metadata"]["turn_id"], "codex-turn-1")
        self.assertEqual(claude["provider_kind"], "claude")
        self.assertEqual(claude["provider_metadata"]["turn_id"], "claude-turn-1")
        self.assertTrue(codex["freshness_identity"].startswith("sha256:"))
        self.assertTrue(claude["freshness_identity"].startswith("sha256:"))

        database_bytes = (self.data / "contexts.sqlite3").read_bytes()
        for canary in (
            b"CODEx-visible-message-canary",
            "완료된 답변입니다.".encode(),
            b"SECRET_TOOL_NAME_CANARY",
            b"SECRET_TOOL_ARGUMENT_CANARY",
            b"Claude-visible-message-canary",
            "Claude 완료 답변입니다.".encode(),
            b"CLAUDE_REASONING_CANARY",
            b"CLAUDE_TOOL_CANARY",
            b"CLAUDE_TOOL_ARGUMENT_CANARY",
        ):
            self.assertNotIn(canary, database_bytes)

    def test_exact_fetch_returns_visible_messages_only_and_detects_change(self) -> None:
        record = self._bind_and_refresh("codex")
        fetched = self.service.corpus_source_fetch(
            corpus_id="project",
            binding_id="project-codex",
            external_id=record["external_id"],
        )

        rendered = json.dumps(fetched, ensure_ascii=False)
        self.assertEqual(fetched["freshness_state"], "valid")
        self.assertIn("CODEx-visible-message-canary", rendered)
        self.assertIn("완료된 답변입니다.", rendered)
        self.assertNotIn("SECRET_TOOL", rendered)
        self.assertFalse(fetched["tool_records_included"])
        self.assertFalse(fetched["reasoning_records_included"])

        _write_jsonl(
            self.codex_file,
            _codex_records(
                completed_at=self.completed_at,
                final_text="변경된 완료 답변입니다.",
            ),
        )
        changed = self.service.corpus_source_fetch(
            corpus_id="project",
            binding_id="project-codex",
            external_id=record["external_id"],
        )
        self.assertEqual(changed["freshness_state"], "source_changed")
        self.assertNotEqual(
            changed["current_freshness_identity"],
            changed["expected_freshness_identity"],
        )

    def test_refresh_run_id_is_generated_internally(self) -> None:
        self.service.corpus_source_update(
            action="bind",
            corpus_id="project",
            binding_id="project-codex",
            payload={
                "provider_kind": "codex",
                "selector": {
                    "cwd_prefix": "/workspace/project",
                    "actor": "all",
                    "lookback_days": 30,
                    "include_archived": False,
                },
            },
        )
        first = self.service.corpus_source_update(
            action="refresh",
            corpus_id="project",
            binding_id="project-codex",
            payload={},
        )
        second = self.service.corpus_source_update(
            action="refresh",
            corpus_id="project",
            binding_id="project-codex",
            payload={},
        )
        self.assertTrue(first["run_id"].startswith("run_"))
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_paged_observation_preserves_base_run_until_completion(self) -> None:
        self.service.corpus_source_update(
            action="bind",
            corpus_id="project",
            binding_id="project-codex",
            payload={
                "provider_kind": "codex",
                "selector": {
                    "cwd_prefix": "/workspace/project",
                    "actor": "all",
                    "lookback_days": 30,
                    "include_archived": False,
                },
            },
        )

        def record(index: int) -> dict:
            return {
                "external_id": f"turn_{index}",
                "parent_external_id": "session-safe",
                "occurred_at": "2026-08-03T00:00:00Z",
                "provider_metadata": {
                    "session_id": "session-safe",
                    "turn_id": f"turn-{index}",
                    "cwd": "/workspace/project",
                    "workspace": "/workspace/project",
                    "actor": "user_task",
                    "task_kind": "codex_turn",
                },
                "locator": {
                    "root_ref": "active",
                    "relative_path": "2026/08/session.jsonl",
                    "session_id": "session-safe",
                    "turn_id": f"turn-{index}",
                },
                "freshness_identity": "sha256:" + f"{index:064x}",
            }

        self.service.corpus_source_update(
            action="observe",
            corpus_id="project",
            binding_id="project-codex",
            payload={
                "run_id": "initial-run",
                "records": [record(0)],
                "complete": True,
            },
        )
        self.service.corpus_source_update(
            action="observe",
            corpus_id="project",
            binding_id="project-codex",
            payload={
                "run_id": "paged-run",
                "records": [record(1)],
                "complete": False,
            },
        )
        completed = self.service.corpus_source_update(
            action="observe",
            corpus_id="project",
            binding_id="project-codex",
            payload={
                "run_id": "paged-run",
                "records": [record(2)],
                "complete": True,
            },
        )

        self.assertEqual(completed["status"], "complete")
        self.assertEqual(completed["observed_in_run"], 2)

    def test_restricted_context_tracks_changed_and_missing_provider_record(
        self,
    ) -> None:
        record = self._bind_and_refresh("codex")
        self.service.context_update(
            action="create",
            context_id="project-experience",
            expected_version=0,
            payload={
                "title": "Project experience",
                "purpose": "Reuse completed task evidence.",
                "scope": {"type": "semantic_collection"},
                "corpus_ids": ["project"],
            },
        )
        self.service.context_update(
            action="append",
            context_id="project-experience",
            expected_version=1,
            payload={
                "items": [
                    {
                        "client_ref": "completed-turn-finding",
                        "kind": "finding",
                        "body_text": "A completed task established the current result.",
                        "attributes": {},
                        "sources": [],
                        "external_sources": [
                            {
                                "corpus_id": "project",
                                "binding_id": "project-codex",
                                "external_id": record["external_id"],
                                "link_role": "direct",
                            }
                        ],
                    }
                ]
            },
        )

        current = self.service.context_read(context_id="project-experience")
        linked = current["items"][0]["external_sources"][0]
        self.assertEqual(linked["dependency_state"], "valid")
        self.assertEqual(linked["provider_kind"], "codex")
        self.assertEqual(
            linked["provider_metadata"]["turn_id"],
            "codex-turn-1",
        )
        self.assertEqual(linked["locator"]["turn_id"], "codex-turn-1")

        _write_jsonl(
            self.codex_file,
            _codex_records(
                completed_at=self.completed_at,
                final_text="변경된 완료 답변입니다.",
            ),
        )
        changed = self.service.context_read(context_id="project-experience")
        self.assertEqual(
            changed["items"][0]["external_sources"][0]["dependency_state"],
            "source_changed",
        )

        self.service.corpus_source_update(
            action="refresh",
            corpus_id="project",
            binding_id="project-codex",
            payload={},
        )
        refreshed = self.service.context_read(context_id="project-experience")
        self.assertEqual(
            refreshed["items"][0]["external_sources"][0]["dependency_state"],
            "source_changed",
        )

        self.codex_file.unlink()
        missing = self.service.corpus_source_fetch(
            corpus_id="project",
            binding_id="project-codex",
            external_id=record["external_id"],
        )
        self.assertEqual(missing["freshness_state"], "source_unavailable")
        self.service.corpus_source_update(
            action="refresh",
            corpus_id="project",
            binding_id="project-codex",
            payload={},
        )
        removed = self.service.context_read(context_id="project-experience")
        self.assertEqual(
            removed["items"][0]["external_sources"][0]["dependency_state"],
            "source_removed",
        )


if __name__ == "__main__":
    unittest.main()
