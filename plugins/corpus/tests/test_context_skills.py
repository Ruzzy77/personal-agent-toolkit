from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from corpus.errors import (
    ContextConflictError,
    ContextNotFoundError,
    ContextValidationError,
)
from corpus.service import CorpusService


class ContextSkillServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.data = self.base / "private"
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "rule.md").write_text("official rule", encoding="utf-8")
        self.service = CorpusService(self.data)
        self.service.register(
            corpus_id="rules",
            source_root=self.source,
            execution_policy="external_host_allowed",
        )
        self.service.context_update(
            action="create",
            context_id="research-administration",
            expected_version=0,
            payload={
                "title": "Research administration",
                "purpose": "Keep rule interpretation tied to exact sources.",
                "scope": {"topic": "research_administration"},
                "corpus_ids": ["rules"],
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _skill_file(self, body: str | None = None) -> Path:
        path = self.base / "SKILL.md"
        instructions = body or (
            "Start from the selected Context. Read exact current source text when needed. "
            "Do not simulate a local-only currentness check."
        )
        path.write_text(
            "---\n"
            "name: research-administration\n"
            "description: Apply source-aware research administration guidance.\n"
            "---\n\n"
            f"{instructions}\n",
            encoding="utf-8",
        )
        return path

    def test_context_skill_is_stored_privately_and_returned_to_chat_projection(
        self,
    ) -> None:
        installed = self.service.context_skill_set(
            context_id="research-administration",
            skill_file=self._skill_file(),
            expected_version="absent",
            confirm_context_skill_write=True,
        )

        self.assertTrue(installed["changed"])
        self.assertTrue(installed["skill"]["version"].startswith("context-skill-v1:"))
        local_path = Path(installed["skill"]["storage_path"])
        self.assertEqual(
            local_path,
            self.data / "contexts" / "research-administration" / "skill",
        )
        self.assertEqual(stat.S_IMODE(os.stat(local_path).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(local_path / "SKILL.md").st_mode), 0o600)

        listing = self.service.space_list(audience="external_mcp")["spaces"][0]
        listed_skill = listing["context"]["skill"]
        self.assertEqual(listed_skill["provenance"], "user_approved_context_skill")
        self.assertNotIn("instructions", listed_skill)
        self.assertNotIn("storage_path", listed_skill)

        opened = self.service.space_get(
            space_id="research-administration",
            audience="external_mcp",
        )["space"]
        remote_skill = opened["context"]["skill"]
        self.assertIn("Read exact current source text", remote_skill["instructions"])
        self.assertFalse(remote_skill["source_evidence"])
        self.assertNotIn(str(self.data), repr(opened))
        self.assertNotIn(str(self.base), repr(opened))

    def test_context_skill_write_is_version_checked_and_idempotent(self) -> None:
        source = self._skill_file()
        first = self.service.context_skill_set(
            context_id="research-administration",
            skill_file=source,
            expected_version="absent",
            confirm_context_skill_write=True,
        )
        version = first["skill"]["version"]

        replay = self.service.context_skill_set(
            context_id="research-administration",
            skill_file=source,
            expected_version=version,
            confirm_context_skill_write=True,
        )
        self.assertFalse(replay["changed"])

        with self.assertRaises(ContextConflictError):
            self.service.context_skill_set(
                context_id="research-administration",
                skill_file=self._skill_file("Use a conflicting workflow."),
                expected_version="absent",
                confirm_context_skill_write=True,
            )

        removed = self.service.context_skill_remove(
            context_id="research-administration",
            expected_version=version,
            confirm_context_skill_remove=True,
        )
        self.assertTrue(removed["changed"])
        self.assertIsNone(
            self.service.context_skill_read(context_id="research-administration")[
                "skill"
            ]
        )

    def test_context_skill_can_be_replaced_from_complete_chat_content(self) -> None:
        first = self.service.context_skill_revise(
            context_id="research-administration",
            name="research-administration",
            description="Apply source-aware research administration guidance.",
            instructions="Read the current Context, then use exact Source text only when needed.",
            expected_version="absent",
            audience="external_mcp",
        )
        self.assertTrue(first["changed"])
        self.assertNotIn("storage_path", first["skill"])

        replay = self.service.context_skill_revise(
            context_id="research-administration",
            name="research-administration",
            description="Apply source-aware research administration guidance.",
            instructions="Read the current Context, then use exact Source text only when needed.",
            expected_version="absent",
            audience="external_mcp",
        )
        self.assertFalse(replay["changed"])

        with self.assertRaises(ContextValidationError) as caught:
            self.service.context_skill_revise(
                context_id="research-administration",
                name="research-administration",
                description="Apply source-aware research administration guidance.",
                instructions="Use /Users/private/secret.md.",
                expected_version=first["skill"]["version"],
                audience="external_mcp",
            )
        self.assertEqual(caught.exception.details["reason"], "private_content_detected")

    def test_context_skill_requires_confirmation_and_rejects_private_paths(
        self,
    ) -> None:
        source = self._skill_file()
        with self.assertRaises(ContextValidationError):
            self.service.context_skill_set(
                context_id="research-administration",
                skill_file=source,
                expected_version="absent",
                confirm_context_skill_write=False,
            )

        unsafe = self._skill_file(
            "Run the helper from /Users/private/Agent-Workspace/private-context-skill."
        )
        with self.assertRaises(ContextValidationError) as caught:
            self.service.context_skill_set(
                context_id="research-administration",
                skill_file=unsafe,
                expected_version="absent",
                confirm_context_skill_write=True,
            )
        self.assertEqual(caught.exception.details["reason"], "private_content_detected")

    def test_archived_context_skill_remains_readable_but_cannot_be_changed(
        self,
    ) -> None:
        installed = self.service.context_skill_set(
            context_id="research-administration",
            skill_file=self._skill_file(),
            expected_version="absent",
            confirm_context_skill_write=True,
        )
        self.service.context_update(
            action="archive",
            context_id="research-administration",
            expected_version=1,
            payload={},
        )

        skill = self.service.context_skill_read(context_id="research-administration")[
            "skill"
        ]
        self.assertEqual(skill["version"], installed["skill"]["version"])
        with self.assertRaises(ContextNotFoundError):
            self.service.context_skill_remove(
                context_id="research-administration",
                expected_version=skill["version"],
                confirm_context_skill_remove=True,
            )
