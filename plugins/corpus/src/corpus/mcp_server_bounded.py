"""Bound live Work-file MCP responses before they leave the personal host."""

from __future__ import annotations

from functools import wraps
from typing import Any

from .database import encode_json
from .errors import BudgetExceededError
from .service import (
    CORPUS_READ_DEFAULT_CHARS,
    CORPUS_READ_MAX_SERIALIZED_BYTES,
    CorpusService,
)
from .workspaces import WORKSPACE_MAX_FILE_BYTES


def _bound_space_file_read_result(
    result: dict[str, Any],
    *,
    max_chars: int,
) -> dict[str, Any]:
    """Apply the live UTF-8 character budget and aggregate MCP response budget."""

    bounded = dict(result)
    if bounded.get("source_kind") == "live_file" and bounded.get("encoding") == "utf8":
        content = bounded.get("content")
        if not isinstance(content, str):
            raise TypeError("live UTF-8 Space reads must return string content")
        total_chars = len(content)
        returned_content = content[:max_chars]
        bounded["content"] = returned_content
        bounded["returned_chars"] = len(returned_content)
        bounded["total_chars"] = total_chars
        bounded["truncated"] = len(returned_content) < total_chars

    serialized_bytes = len(encode_json(bounded).encode())
    if serialized_bytes > CORPUS_READ_MAX_SERIALIZED_BYTES:
        encoding = bounded.get("encoding")
        retry_fields = ["max_chars"] if encoding == "utf8" else ["max_bytes"]
        raise BudgetExceededError(
            "Space file read response exceeds the serialized response budget",
            details={
                "source_kind": bounded.get("source_kind"),
                "encoding": encoding,
                "serialized_bytes": serialized_bytes,
                "maximum_serialized_bytes": CORPUS_READ_MAX_SERIALIZED_BYTES,
                "retry_with_lower": retry_fields,
            },
        )
    return bounded


def _install_live_read_bound() -> None:
    current = CorpusService.space_file_read
    if getattr(current, "__corpus_live_read_bounded__", False):
        return

    @wraps(current)
    def bounded_space_file_read(
        self: CorpusService,
        *,
        space_id: str,
        connection_id: str | None = None,
        relative_path: str | None = None,
        read_ref: str | None = None,
        encoding: str = "utf8",
        max_bytes: int = WORKSPACE_MAX_FILE_BYTES,
        neighbor_span: int = 0,
        max_chars: int = CORPUS_READ_DEFAULT_CHARS,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        result = current(
            self,
            space_id=space_id,
            connection_id=connection_id,
            relative_path=relative_path,
            read_ref=read_ref,
            encoding=encoding,
            max_bytes=max_bytes,
            neighbor_span=neighbor_span,
            max_chars=max_chars,
            audience=audience,
        )
        return _bound_space_file_read_result(result, max_chars=max_chars)

    bounded_space_file_read.__corpus_live_read_bounded__ = True  # type: ignore[attr-defined]
    CorpusService.space_file_read = bounded_space_file_read


_install_live_read_bound()

from . import mcp_server as _base  # noqa: E402

create_server = _base.create_server
mcp = _base.mcp
main = _base.main

__all__ = ["create_server", "main", "mcp"]


if __name__ == "__main__":
    main()
