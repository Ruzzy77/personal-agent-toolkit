from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from corpus.mcp_server import create_server
from corpus.service import CorpusService


async def call_tool(server, name: str, arguments: dict) -> tuple[list, dict]:
    result = await server.call_tool(name, arguments)
    return result.content, result.structured_content


class MCPServerTest(unittest.TestCase):
    def test_default_server_exposes_only_the_space_file_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            server = create_server(data)
            tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
            content, response = asyncio.run(call_tool(server, "corpus_space_list", {}))

        self.assertEqual(
            set(tools),
            {
                "corpus_space_list",
                "corpus_space_get",
                "corpus_space_search",
                "corpus_file_list",
                "corpus_file_read",
                "corpus_file_write",
                "corpus_file_delete",
                "corpus_file_select_current",
                "corpus_file_restore",
            },
        )
        self.assertTrue(all(tool.output_schema.get("type") == "object" for tool in tools.values()))
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["spaces"], [])
        self.assertEqual(json.loads(content[0].text), response)
        self.assertFalse(data.exists())

    def test_remote_work_file_round_trip_keeps_local_paths_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            data = base / "private-data"
            source = base / "local-source"
            work = base / "shared-work"
            source.mkdir()
            work.mkdir()
            (source / "source.md").write_text("local source", encoding="utf-8")
            original = "Draft with /Users/example/private text."
            (work / "draft.md").write_text(original, encoding="utf-8")

            service = CorpusService(data)
            service.register(
                corpus_id="source",
                source_root=source,
                execution_policy="local_only",
            )
            service.context_update(
                action="create",
                context_id="thesis",
                expected_version=0,
                payload={
                    "title": "Thesis",
                    "purpose": "Revise the thesis.",
                    "scope": {},
                    "corpus_ids": ["source"],
                },
                )
            service.workspace_connect(
                workspace_id="thesis",
                context_id="thesis",
                display_name="Thesis",
                root=work,
                execution_policy="external_host_allowed",
            )
            server = create_server(data)

            _, listing = asyncio.run(call_tool(server, "corpus_space_list", {}))
            self.assertEqual(
                [space["space_id"] for space in listing["result"]["spaces"]],
                ["thesis"],
            )

            _, read = asyncio.run(
                call_tool(
                    server,
                    "corpus_file_read",
                    {"space_id": "thesis", "relative_path": "draft.md"},
                )
            )
            self.assertTrue(read["ok"])
            self.assertEqual(read["result"]["untrusted_content"], original)

            _, written = asyncio.run(
                call_tool(
                    server,
                    "corpus_file_write",
                    {
                        "space_id": "thesis",
                        "relative_path": "draft.md",
                        "content": "Revised",
                        "content_encoding": "utf8",
                        "expected_version": read["result"]["file"]["version_token"],
                    },
                )
            )
            self.assertTrue(written["ok"])
            self.assertEqual((work / "draft.md").read_text(encoding="utf-8"), "Revised")

            serialized = json.dumps([listing, read, written], ensure_ascii=False)
            self.assertNotIn(str(data), serialized)
            self.assertNotIn(str(source), serialized)
            self.assertNotIn(str(work), serialized)
            self.assertIn("/Users/example/private text.", serialized)


if __name__ == "__main__":
    unittest.main()
