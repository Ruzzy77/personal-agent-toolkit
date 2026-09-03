"""JSON CLI for Corpus operations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import default_data_root
from .errors import (
    ContextValidationError,
    CorpusError,
    WorkspaceValidationError,
)
from .service import (
    CORPUS_INVENTORY_DEFAULT_LIMIT,
    CORPUS_INVENTORY_MAX_LIMIT,
    CORPUS_INVENTORY_MAX_OFFSET,
    CORPUS_READ_DEFAULT_CHARS,
    CorpusService,
)
from .session_sources import (
    SESSION_SOURCE_FETCH_DEFAULT_CHARS,
    SESSION_SOURCE_FETCH_MAX_CHARS,
)
from .workspaces import (
    WORKSPACE_MAX_ENCODED_CONTENT_CHARS,
    WORKSPACE_MAX_FILE_BYTES,
)

SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b)?\s*$", re.IGNORECASE)
SIZE_FACTORS = {
    None: 1,
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


def parse_size(value: str) -> int:
    match = SIZE_RE.fullmatch(value)
    if match is None:
        raise argparse.ArgumentTypeError("size must look like 5000000, 25MB, or 25MiB")
    number = float(match.group(1))
    suffix = match.group(2).lower() if match.group(2) else None
    return int(number * SIZE_FACTORS[suffix])


def load_json_object(path_value: str) -> dict:
    try:
        if path_value == "-":
            value = json.load(sys.stdin)
        else:
            with Path(path_value).open(encoding="utf-8") as stream:
                value = json.load(stream)
    except (OSError, UnicodeError) as exc:
        raise ContextValidationError(
            "context payload file could not be read",
            details={"payload_file": path_value},
        ) from exc
    except json.JSONDecodeError as exc:
        raise ContextValidationError(
            "context payload file must contain valid JSON",
            details={
                "payload_file": path_value,
                "line": exc.lineno,
                "column": exc.colno,
            },
        ) from exc
    if not isinstance(value, dict):
        raise ContextValidationError(
            "context payload file must contain a JSON object",
            details={"payload_file": path_value},
        )
    return value


def load_workspace_content(path: Path) -> str:
    """Read a bounded UTF-8 text carrier for one workspace write."""

    try:
        with path.open("rb") as stream:
            encoded = stream.read(WORKSPACE_MAX_ENCODED_CONTENT_CHARS + 1)
    except OSError as exc:
        raise WorkspaceValidationError(
            "work folder content file could not be read",
            details={"content_file": str(path)},
        ) from exc
    if len(encoded) > WORKSPACE_MAX_ENCODED_CONTENT_CHARS:
        raise WorkspaceValidationError(
            "work folder content file is too large",
            details={
                "content_file": str(path),
                "maximum_bytes": WORKSPACE_MAX_ENCODED_CONTENT_CHARS,
            },
        )
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceValidationError(
            "work folder content file must contain UTF-8 text",
            details={"content_file": str(path)},
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpus",
        description="Manage registered sources, private indexes, and reusable context.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=default_data_root(),
        help="Private mutable runtime root outside all registered source roots.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output.")
    commands = parser.add_subparsers(dest="command", required=True)

    corpus = commands.add_parser("corpus", help="Manage explicit corpus registrations.")
    corpus_commands = corpus.add_subparsers(dest="corpus_command", required=True)
    add = corpus_commands.add_parser("add", help="Register a folder Corpus may read.")
    add.add_argument("--id", required=True, dest="corpus_id")
    add.add_argument("--root", required=True, type=Path)
    add.add_argument(
        "--execution-policy",
        choices=("local_only", "external_host_allowed"),
        required=True,
    )
    add.add_argument(
        "--provider",
        default="filesystem",
        choices=("filesystem", "synology_file_provider"),
    )
    add.add_argument(
        "--exclude-directory-name",
        action="append",
        default=[],
        help="Skip any directory with this exact name; may be repeated.",
    )
    add.add_argument(
        "--exclude-path-prefix",
        action="append",
        default=[],
        help="Skip one root-relative directory prefix; may be repeated.",
    )
    scope = corpus_commands.add_parser(
        "scope",
        help="Replace the source exclusions for an existing corpus.",
    )
    scope.add_argument("--id", required=True, dest="corpus_id")
    scope.add_argument(
        "--exclude-directory-name",
        action="append",
        default=[],
    )
    scope.add_argument(
        "--exclude-path-prefix",
        action="append",
        default=[],
    )
    scope.add_argument(
        "--clear",
        action="store_true",
        help="Remove all source exclusions.",
    )
    rebind_root = corpus_commands.add_parser(
        "rebind-root",
        help="Replace a registered source root after validation and backup.",
    )
    rebind_root.add_argument("--id", required=True, dest="corpus_id")
    rebind_root.add_argument("--root", required=True, type=Path)
    rebind_root.add_argument(
        "--expected-root",
        required=True,
        type=Path,
        help="Current registered root; the command stops if it does not match.",
    )
    unregister = corpus_commands.add_parser(
        "unregister",
        help="Remove one source registration while retaining its private index.",
    )
    unregister.add_argument("--id", required=True, dest="corpus_id")
    unregister.add_argument(
        "--expected-root",
        required=True,
        type=Path,
        help="Current registered root; the command stops if it does not match.",
    )
    unregister.add_argument(
        "--confirm-unregister",
        action="store_true",
        help="Confirm removal of this source registration.",
    )
    unregister.add_argument(
        "--remove-archived-context",
        dest="archived_context_id",
        help="Remove this archived Context when it is the only saved Context reference.",
    )
    unregister.add_argument(
        "--expected-context-version",
        type=int,
        help="Current version of the archived Context selected for removal.",
    )
    unregister.add_argument(
        "--confirm-remove-linked-history",
        action="store_true",
        help="Confirm removal of the archived Context and Corpus-held linked source history.",
    )
    corpus_commands.add_parser("list", help="List registered corpora.")

    space = commands.add_parser(
        "space",
        help="View contexts and connected locations as canonical Spaces.",
    )
    space_commands = space.add_subparsers(dest="space_command", required=True)
    space_list = space_commands.add_parser("list", help="List active Spaces.")
    space_list.add_argument("--limit", type=int, default=100)
    space_list.add_argument("--offset", type=int, default=0)
    space_show = space_commands.add_parser(
        "show",
        help="Show one Space, its Context, and its Connections.",
    )
    space_show.add_argument("--id", required=True, dest="space_id")
    space_show.add_argument("--context-limit", type=int, default=100)
    space_show.add_argument("--context-offset", type=int, default=0)
    space_search = space_commands.add_parser(
        "search",
        help="Search indexed Source Connections in one Space.",
    )
    space_search.add_argument("--id", required=True, dest="space_id")
    space_search.add_argument("--connection", dest="connection_id")
    space_search.add_argument("--query", required=True)
    space_search.add_argument("--limit", type=int, default=20)
    space_files = space_commands.add_parser(
        "files",
        help="List a live directory or find filenames in one Connection.",
    )
    space_files.add_argument("--id", required=True, dest="space_id")
    space_files.add_argument("--connection", dest="connection_id")
    space_files.add_argument(
        "--mode",
        choices=("list_directory", "find"),
        default="list_directory",
    )
    space_files.add_argument("--path", dest="relative_path")
    space_files.add_argument("--query")
    space_files.add_argument("--cursor")
    space_files.add_argument("--limit", type=int, default=100)
    space_read = space_commands.add_parser(
        "read",
        help="Read a live Work file or an exact indexed read reference.",
    )
    space_read.add_argument("--id", required=True, dest="space_id")
    space_read.add_argument("--connection", dest="connection_id")
    space_read_target = space_read.add_mutually_exclusive_group()
    space_read_target.add_argument("--path", dest="relative_path")
    space_read_target.add_argument("--read-ref")
    space_read.add_argument("--encoding", choices=("utf8", "base64"), default="utf8")
    space_read.add_argument(
        "--max-bytes",
        type=parse_size,
        default=WORKSPACE_MAX_FILE_BYTES,
    )
    space_read.add_argument("--neighbor-span", type=int, default=0)
    space_read.add_argument("--include-structure-context", action="store_true")
    space_read.add_argument("--max-chars", type=int, default=CORPUS_READ_DEFAULT_CHARS)
    space_read.add_argument("--start-char", type=int, default=0)
    space_write = space_commands.add_parser(
        "write",
        help="Create or replace one file in a writable Space Connection.",
    )
    space_write.add_argument("--id", required=True, dest="space_id")
    space_write.add_argument("--connection", dest="connection_id")
    space_write.add_argument("--path", required=True, dest="relative_path")
    space_write.add_argument("--content-file", required=True, type=Path)
    space_write.add_argument(
        "--content-encoding",
        choices=("utf8", "base64"),
        default="utf8",
    )
    space_write.add_argument("--expected-version", required=True)
    space_write.add_argument("--replace-start-marker")
    space_write.add_argument("--replace-end-marker")
    space_write.add_argument("--make-current", action="store_true")
    space_select = space_commands.add_parser(
        "select-current",
        help="Select one existing file as a Connection's current file.",
    )
    space_select.add_argument("--id", required=True, dest="space_id")
    space_select.add_argument("--connection", dest="connection_id")
    space_select.add_argument("--path", required=True, dest="relative_path")
    space_restore = space_commands.add_parser(
        "restore",
        help="Restore one unchanged replacement through its recovery ID.",
    )
    space_restore.add_argument("--id", required=True, dest="space_id")
    space_restore.add_argument("--connection", dest="connection_id")
    space_restore.add_argument("--recovery-id", required=True)
    space_restore.add_argument("--expected-version", required=True)
    workspace = commands.add_parser(
        "workspace",
        help="Manage explicitly connected local work folders.",
    )
    workspace_commands = workspace.add_subparsers(
        dest="workspace_command",
        required=True,
    )
    workspace_connect = workspace_commands.add_parser(
        "connect",
        help="Connect one editable local folder to an existing context.",
    )
    workspace_connect.add_argument("--id", dest="workspace_id")
    workspace_connect.add_argument(
        "--context",
        dest="context_id",
        help="Defaults to the work-folder name.",
    )
    workspace_connect.add_argument(
        "--name",
        dest="display_name",
        help="Defaults to the work-folder name.",
    )
    workspace_connect.add_argument("--root", required=True, type=Path)
    workspace_connect.add_argument(
        "--execution-policy",
        choices=("local_only", "external_host_allowed"),
        required=True,
    )
    workspace_rebind = workspace_commands.add_parser(
        "rebind-root",
        help="Replace a copied or restored work-folder root after validation.",
    )
    workspace_rebind.add_argument("--id", required=True, dest="workspace_id")
    workspace_rebind.add_argument("--root", required=True, type=Path)
    workspace_rebind.add_argument(
        "--expected-root",
        required=True,
        type=Path,
        help="Current connected root; the command stops if it does not match.",
    )
    workspace_commands.add_parser("list", help="List connected work folders.")

    workspace_status = workspace_commands.add_parser(
        "status",
        help="Report one work folder connection and current-file state.",
    )
    workspace_status.add_argument("--id", required=True, dest="workspace_id")

    workspace_disconnect = workspace_commands.add_parser(
        "disconnect",
        help="Disconnect a work folder without changing its local files.",
    )
    workspace_disconnect.add_argument(
        "--id",
        required=True,
        dest="workspace_id",
    )

    scan = commands.add_parser("scan", help="Run metadata-only discovery.")
    scan.add_argument("--corpus", required=True)

    sync = commands.add_parser(
        "sync",
        help="Scan metadata and refresh only pending source-index documents.",
    )
    sync.add_argument("--corpus", required=True)
    sync.add_argument("--max-files", type=int, default=10)
    sync.add_argument("--max-bytes", type=parse_size, default=parse_size("50MiB"))
    sync.add_argument(
        "--max-file-bytes",
        type=parse_size,
        default=parse_size("25MiB"),
    )
    sync.add_argument(
        "--include-remote",
        action="store_true",
        help="Download remote placeholders within the file, size, and time limits.",
    )
    sync.add_argument("--timeout-seconds", type=float, default=120)

    status = commands.add_parser("status", help="Report corpus index state.")
    status.add_argument("--corpus", required=True)
    status.add_argument(
        "--max-file-bytes",
        type=parse_size,
        default=parse_size("250MiB"),
        help="Classify pending documents above this local refresh limit.",
    )
    status.add_argument("--include-remote", action="store_true")
    status.add_argument(
        "--include-warning-items",
        action="store_true",
        help="Include document-level warning identities for machine comparison.",
    )

    inventory = commands.add_parser(
        "inventory",
        help="List bounded document metadata for exact indexing selection.",
    )
    inventory.add_argument("--corpus", required=True)
    inventory.add_argument(
        "--path-contains",
        help="NFC-normalized literal substring of a relative document path.",
    )
    inventory.add_argument(
        "--eligibility-state",
        choices=("all", "supported", "unsupported", "ignored"),
        default="supported",
    )
    inventory.add_argument(
        "--residency-state",
        choices=("all", "resident", "remote_only", "unknown"),
        default="all",
    )
    inventory.add_argument(
        "--index-state",
        choices=(
            "all",
            "current",
            "refresh_required",
            "unindexed",
            "not_applicable",
        ),
        default="all",
    )
    inventory.add_argument(
        "--extension",
        help="Exact lowercase file extension, with or without a leading dot.",
    )
    inventory.add_argument(
        "--max-logical-bytes",
        type=parse_size,
        help="Only return documents at or below this logical size.",
    )
    inventory.add_argument(
        "--limit",
        type=int,
        default=CORPUS_INVENTORY_DEFAULT_LIMIT,
        help=f"Documents per page (max {CORPUS_INVENTORY_MAX_LIMIT}).",
    )
    inventory.add_argument(
        "--offset",
        type=int,
        default=0,
        help=f"Page offset (max {CORPUS_INVENTORY_MAX_OFFSET}).",
    )

    ingest = commands.add_parser(
        "ingest",
        help="Capture source bytes temporarily and extract persistent source units.",
    )
    ingest.add_argument("--corpus", required=True)
    ingest.add_argument("--max-files", type=int, default=10)
    ingest.add_argument("--max-bytes", type=parse_size, default=parse_size("50MiB"))
    ingest.add_argument(
        "--max-file-bytes",
        type=parse_size,
        default=parse_size("25MiB"),
        help=(
            "Per-file capture limit. Values above 250 MiB are accepted only for "
            "local files selected by exact --document-id values (maximum 1 GiB)."
        ),
    )
    ingest.add_argument(
        "--include-remote",
        action="store_true",
        help="Download selected remote placeholders within the file, size, and time limits.",
    )
    ingest.add_argument(
        "--remote-only",
        action="store_true",
        help="Select only remote placeholders; requires --include-remote.",
    )
    ingest.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help=(
            "Restrict ingest to an exact document id; repeat for multiple documents. "
            "Exact local selection permits an explicit request total up to 1 GiB."
        ),
    )
    ingest.add_argument("--timeout-seconds", type=float, default=120)

    large_document = commands.add_parser(
        "large-document",
        help="Approve and refresh selected resident documents above the standard limit.",
    )
    large_document_commands = large_document.add_subparsers(
        dest="large_document_command",
        required=True,
    )
    large_approve = large_document_commands.add_parser(
        "approve",
        help="Approve the currently observed source identity for one document.",
    )
    large_approve.add_argument("--corpus", required=True)
    large_approve.add_argument("--document-id", required=True)
    large_approve.add_argument("--max-bytes", type=parse_size)
    large_list = large_document_commands.add_parser(
        "list",
        help="List large-document approvals and whether they still match the source.",
    )
    large_list.add_argument("--corpus", required=True)
    large_revoke = large_document_commands.add_parser(
        "revoke",
        help="Revoke one large-document approval.",
    )
    large_revoke.add_argument("--corpus", required=True)
    large_revoke.add_argument("--document-id", required=True)
    large_sync = large_document_commands.add_parser(
        "sync",
        help="Refresh matching approved large documents one at a time.",
    )
    large_sync.add_argument("--corpus", required=True)
    large_sync.add_argument("--max-files", type=int, default=10)
    large_sync.add_argument("--max-bytes", type=parse_size, default=parse_size("1GiB"))
    large_sync.add_argument("--timeout-seconds", type=float, default=600)

    cleanup_source_copies = commands.add_parser(
        "cleanup-source-copies",
        help="Plan or delete retained source-byte copies from one private corpus runtime.",
    )
    cleanup_source_copies.add_argument("--corpus", required=True)
    cleanup_source_copies.add_argument(
        "--confirm-delete-source-copies",
        action="store_true",
        help=(
            "Delete canonical blob and abandoned staging files. "
            "Without this flag the command only reports a plan."
        ),
    )

    retention = commands.add_parser(
        "retention",
        help="Inspect or apply bounded lifecycle cleanup for durable records.",
    )
    retention_commands = retention.add_subparsers(
        dest="retention_command",
        required=True,
    )
    retention_status = retention_commands.add_parser(
        "status",
        help="Preview deterministic retention actions without changing records.",
    )
    retention_status.add_argument("--corpus", required=True)
    retention_run = retention_commands.add_parser(
        "run",
        help="Apply deterministic archive, trash, and purge transitions.",
    )
    retention_run.add_argument("--corpus", required=True)
    retention_set = retention_commands.add_parser(
        "set",
        help="Set one record to managed, protected, or transient retention.",
    )
    retention_set.add_argument("--corpus", required=True)
    retention_set.add_argument("--document-id", required=True)
    retention_set.add_argument(
        "--class",
        dest="retention_class",
        choices=("managed", "protected", "transient"),
        required=True,
    )
    retention_restore = retention_commands.add_parser(
        "restore",
        help="Restore one archived or trashed record to active retrieval.",
    )
    retention_restore.add_argument("--corpus", required=True)
    retention_restore.add_argument("--document-id", required=True)

    source = commands.add_parser(
        "source",
        help="Connect and inspect non-file source records inside an existing corpus.",
    )
    source_commands = source.add_subparsers(
        dest="source_command",
        required=True,
    )
    source_list = source_commands.add_parser(
        "list",
        help="List linked source bindings and observed metadata records.",
    )
    source_list.add_argument("--corpus", required=True)
    source_list.add_argument("--binding", dest="binding_id")
    source_list.add_argument(
        "--record-state",
        choices=("active", "removed"),
        default="active",
    )
    source_list.add_argument(
        "--occurred-after",
        help="Return records strictly later than this timezone-aware ISO 8601 timestamp.",
    )
    source_list.add_argument("--limit", type=int, default=100)
    source_list.add_argument("--offset", type=int, default=0)

    source_bind = source_commands.add_parser(
        "bind",
        help="Bind a provider selector to an existing corpus.",
    )
    source_bind.add_argument("--corpus", required=True)
    source_bind.add_argument("--id", required=True, dest="binding_id")
    source_bind.add_argument(
        "--payload-file",
        required=True,
        metavar="FILE|-",
        help="Read provider_kind and selector from a JSON object.",
    )

    source_observe = source_commands.add_parser(
        "observe",
        help="Upsert a bounded page of provider metadata observations.",
    )
    source_observe.add_argument("--corpus", required=True)
    source_observe.add_argument("--id", required=True, dest="binding_id")
    source_observe.add_argument(
        "--payload-file",
        required=True,
        metavar="FILE|-",
        help="Read run_id, records, and complete from a JSON object.",
    )
    source_refresh = source_commands.add_parser(
        "refresh",
        help="Discover completed Codex or Claude turns for one binding.",
    )
    source_refresh.add_argument("--corpus", required=True)
    source_refresh.add_argument("--id", required=True, dest="binding_id")

    source_fetch = source_commands.add_parser(
        "fetch",
        help="Read one exact Codex or Claude turn from its provider record.",
    )
    source_fetch.add_argument("--corpus", required=True)
    source_fetch.add_argument("--id", required=True, dest="binding_id")
    source_fetch.add_argument("--external-id", required=True)
    source_fetch.add_argument(
        "--max-chars",
        type=int,
        default=SESSION_SOURCE_FETCH_DEFAULT_CHARS,
        help=(
            "Aggregate visible provider-message character budget "
            f"(max {SESSION_SOURCE_FETCH_MAX_CHARS})."
        ),
    )

    context = commands.add_parser(
        "context",
        help="Manage private named contexts for repeated corpus use.",
    )
    context_commands = context.add_subparsers(
        dest="context_command",
        required=True,
    )

    context_list = context_commands.add_parser(
        "list",
        help="List named contexts visible to the local CLI.",
    )
    context_list.add_argument(
        "--state",
        choices=("active", "archived"),
        default="active",
    )
    context_list.add_argument("--limit", type=int, default=100)
    context_list.add_argument("--offset", type=int, default=0)

    context_show = context_commands.add_parser(
        "show",
        help="Read one named context and its source freshness.",
    )
    context_show.add_argument(
        "--id",
        required=True,
        dest="context_id",
        help="Private context id.",
    )
    context_show.add_argument(
        "--state",
        choices=("active", "archived"),
        default="active",
    )
    context_show.add_argument("--limit", type=int, default=100)
    context_show.add_argument("--offset", type=int, default=0)

    context_create = context_commands.add_parser(
        "create",
        help="Create a named context from a JSON object.",
    )
    context_create.add_argument("--id", required=True, dest="context_id")
    context_create.add_argument(
        "--payload-file",
        required=True,
        metavar="FILE|-",
        help="Read the create payload as a JSON object from FILE or standard input (-).",
    )

    context_update = context_commands.add_parser(
        "update",
        help="Update a named context from a JSON object.",
    )
    context_update.add_argument("--id", required=True, dest="context_id")
    context_update.add_argument(
        "--action",
        required=True,
        choices=("append", "supersede"),
    )
    context_update.add_argument("--expected-version", required=True, type=int)
    context_update.add_argument(
        "--payload-file",
        required=True,
        metavar="FILE|-",
        help="Read the update payload as a JSON object from FILE or standard input (-).",
    )

    context_archive = context_commands.add_parser(
        "archive",
        help="Archive a named context without changing connected files.",
    )
    context_archive.add_argument("--id", required=True, dest="context_id")
    context_archive.add_argument("--expected-version", required=True, type=int)

    context_skill = context_commands.add_parser(
        "skill",
        help="Read or change the approved workflow guidance attached to one Context.",
    )
    context_skill_commands = context_skill.add_subparsers(
        dest="context_skill_command",
        required=True,
    )
    context_skill_show = context_skill_commands.add_parser(
        "show",
        help="Read the Context Skill and its current version token.",
    )
    context_skill_show.add_argument("--id", required=True, dest="context_id")

    context_skill_set = context_skill_commands.add_parser(
        "set",
        help="Copy one reviewed SKILL.md into the private Context skill folder.",
    )
    context_skill_set.add_argument("--id", required=True, dest="context_id")
    context_skill_set.add_argument(
        "--skill-file",
        required=True,
        type=Path,
        help="Reviewed, remote-safe SKILL.md to copy into the Context.",
    )
    context_skill_set.add_argument(
        "--expected-version",
        required=True,
        help="Current Context Skill version, or 'absent' when creating it.",
    )
    context_skill_set.add_argument(
        "--confirm-context-skill-write",
        action="store_true",
        help="Confirm that this guidance may be returned to Chat for the selected Context.",
    )

    context_skill_remove = context_skill_commands.add_parser(
        "remove",
        help="Remove the approved Context Skill without changing source files.",
    )
    context_skill_remove.add_argument("--id", required=True, dest="context_id")
    context_skill_remove.add_argument("--expected-version", required=True)
    context_skill_remove.add_argument(
        "--confirm-context-skill-remove",
        action="store_true",
    )

    return parser


def execute(args: argparse.Namespace) -> dict | list:
    service = CorpusService(data_root=args.data_root)
    if args.command == "corpus":
        if args.corpus_command == "add":
            source_scope = None
            if args.exclude_directory_name or args.exclude_path_prefix:
                source_scope = {
                    "exclude_directory_names": args.exclude_directory_name,
                    "exclude_path_prefixes": args.exclude_path_prefix,
                }
            return service.register(
                corpus_id=args.corpus_id,
                source_root=args.root,
                execution_policy=args.execution_policy,
                provider_kind=args.provider,
                source_scope=source_scope,
            )
        if args.corpus_command == "scope":
            if args.clear and (args.exclude_directory_name or args.exclude_path_prefix):
                raise ContextValidationError(
                    "--clear cannot be combined with exclusion values"
                )
            if not args.clear and not (
                args.exclude_directory_name or args.exclude_path_prefix
            ):
                raise ContextValidationError(
                    "source scope requires an exclusion value or --clear"
                )
            return service.configure_source_scope(
                corpus_id=args.corpus_id,
                exclude_directory_names=(
                    [] if args.clear else args.exclude_directory_name
                ),
                exclude_path_prefixes=([] if args.clear else args.exclude_path_prefix),
            )
        if args.corpus_command == "rebind-root":
            return service.rebind_source_root(
                corpus_id=args.corpus_id,
                source_root=args.root,
                expected_source_root=args.expected_root,
            )
        if args.corpus_command == "unregister":
            return service.unregister(
                corpus_id=args.corpus_id,
                expected_source_root=args.expected_root,
                confirm_unregister=args.confirm_unregister,
                archived_context_id=args.archived_context_id,
                expected_context_version=args.expected_context_version,
                confirm_remove_linked_history=args.confirm_remove_linked_history,
            )
        return {"corpora": service.corpora()}
    if args.command == "space":
        if args.space_command == "list":
            return service.space_list(
                audience="local_cli",
                limit=args.limit,
                offset=args.offset,
            )
        if args.space_command == "show":
            return service.space_get(
                space_id=args.space_id,
                audience="local_cli",
                context_limit=args.context_limit,
                context_offset=args.context_offset,
            )
        if args.space_command == "search":
            return service.space_search(
                space_id=args.space_id,
                connection_id=args.connection_id,
                query=args.query,
                limit=args.limit,
                audience="local_cli",
            )
        if args.space_command == "files":
            return service.space_file_list(
                space_id=args.space_id,
                connection_id=args.connection_id,
                mode=args.mode,
                relative_path=args.relative_path,
                query=args.query,
                cursor=args.cursor,
                limit=args.limit,
                audience="local_cli",
            )
        if args.space_command == "read":
            return service.space_file_read(
                space_id=args.space_id,
                connection_id=args.connection_id,
                relative_path=args.relative_path,
                read_ref=args.read_ref,
                encoding=args.encoding,
                max_bytes=args.max_bytes,
                neighbor_span=args.neighbor_span,
                include_structure_context=args.include_structure_context,
                max_chars=args.max_chars,
                start_char=args.start_char,
                audience="local_cli",
            )
        if args.space_command == "write":
            return service.space_file_write(
                space_id=args.space_id,
                connection_id=args.connection_id,
                relative_path=args.relative_path,
                content=load_workspace_content(args.content_file),
                content_encoding=args.content_encoding,
                expected_version=args.expected_version,
                replace_start_marker=args.replace_start_marker,
                replace_end_marker=args.replace_end_marker,
                make_current=args.make_current,
                audience="local_cli",
            )
        if args.space_command == "select-current":
            return service.space_file_select_current(
                space_id=args.space_id,
                connection_id=args.connection_id,
                relative_path=args.relative_path,
                audience="local_cli",
            )
        if args.space_command == "restore":
            return service.space_file_restore(
                space_id=args.space_id,
                connection_id=args.connection_id,
                recovery_id=args.recovery_id,
                expected_version=args.expected_version,
                audience="local_cli",
            )
        raise AssertionError(f"unhandled space command: {args.space_command}")
    if args.command == "workspace":
        if args.workspace_command == "connect":
            return service.workspace_connect(
                workspace_id=args.workspace_id,
                context_id=args.context_id,
                display_name=args.display_name,
                root=args.root,
                execution_policy=args.execution_policy,
            )
        if args.workspace_command == "rebind-root":
            return service.workspace_rebind_root(
                workspace_id=args.workspace_id,
                root=args.root,
                expected_root=args.expected_root,
            )
        if args.workspace_command == "list":
            return service.workspace_list(audience="local_cli")
        if args.workspace_command == "status":
            return service.workspace_status(
                workspace_id=args.workspace_id,
                audience="local_cli",
            )
        if args.workspace_command == "disconnect":
            return service.workspace_disconnect(
                workspace_id=args.workspace_id,
            )
        raise AssertionError(f"unhandled workspace command: {args.workspace_command}")
    if args.command == "scan":
        return service.scan(args.corpus)
    if args.command == "sync":
        return service.sync(
            args.corpus,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
            max_file_bytes=args.max_file_bytes,
            include_remote=args.include_remote,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "status":
        return service.status(
            args.corpus,
            max_file_bytes=args.max_file_bytes,
            include_remote=args.include_remote,
            include_warning_items=args.include_warning_items,
        )
    if args.command == "inventory":
        return service.inventory(
            args.corpus,
            path_contains=args.path_contains,
            eligibility_state=args.eligibility_state,
            residency_state=args.residency_state,
            index_state=args.index_state,
            extension=args.extension,
            max_logical_bytes=args.max_logical_bytes,
            limit=args.limit,
            offset=args.offset,
        )
    if args.command == "ingest":
        return service.ingest(
            args.corpus,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
            max_file_bytes=args.max_file_bytes,
            include_remote=args.include_remote,
            remote_only=args.remote_only,
            document_ids=args.document_ids,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "large-document":
        if args.large_document_command == "approve":
            return service.approve_large_document(
                args.corpus,
                document_id=args.document_id,
                max_bytes=args.max_bytes,
            )
        if args.large_document_command == "list":
            return service.list_large_document_approvals(args.corpus)
        if args.large_document_command == "revoke":
            return service.revoke_large_document_approval(
                args.corpus,
                document_id=args.document_id,
            )
        if args.large_document_command == "sync":
            return service.sync_approved_large_documents(
                args.corpus,
                max_files=args.max_files,
                max_bytes=args.max_bytes,
                timeout_seconds=args.timeout_seconds,
            )
        raise AssertionError(
            f"unhandled large-document command: {args.large_document_command}"
        )
    if args.command == "cleanup-source-copies":
        return service.cleanup_source_copies(
            args.corpus,
            confirm_delete=args.confirm_delete_source_copies,
        )
    if args.command == "retention":
        if args.retention_command == "status":
            return service.maintain_retention(args.corpus, dry_run=True)
        if args.retention_command == "run":
            return service.maintain_retention(args.corpus)
        if args.retention_command == "set":
            return service.set_document_retention(
                args.corpus,
                document_id=args.document_id,
                retention_class=args.retention_class,
            )
        if args.retention_command == "restore":
            return service.restore_document(
                args.corpus,
                document_id=args.document_id,
            )
        raise AssertionError(f"unhandled retention command: {args.retention_command}")
    if args.command == "source":
        if args.source_command == "list":
            return service.corpus_source_read(
                corpus_id=args.corpus,
                binding_id=args.binding_id,
                record_state=args.record_state,
                occurred_after=args.occurred_after,
                limit=args.limit,
                offset=args.offset,
                audience="local_cli",
            )
        if args.source_command in {"bind", "observe"}:
            return service.corpus_source_update(
                action=args.source_command,
                corpus_id=args.corpus,
                binding_id=args.binding_id,
                payload=load_json_object(args.payload_file),
                audience="local_cli",
            )
        if args.source_command == "refresh":
            return service.corpus_source_update(
                action="refresh",
                corpus_id=args.corpus,
                binding_id=args.binding_id,
                payload={},
                audience="local_cli",
            )
        if args.source_command == "fetch":
            return service.corpus_source_fetch(
                corpus_id=args.corpus,
                binding_id=args.binding_id,
                external_id=args.external_id,
                max_chars=args.max_chars,
                audience="local_cli",
            )
        raise AssertionError(f"unhandled source command: {args.source_command}")
    if args.command == "context":
        if args.context_command == "list":
            return service.context_read(
                context_id=None,
                state=args.state,
                limit=args.limit,
                offset=args.offset,
                audience="local_cli",
            )
        if args.context_command == "show":
            return service.context_read(
                context_id=args.context_id,
                state=args.state,
                limit=args.limit,
                offset=args.offset,
                audience="local_cli",
            )
        if args.context_command == "create":
            return service.context_update(
                action="create",
                context_id=args.context_id,
                expected_version=0,
                payload=load_json_object(args.payload_file),
                audience="local_cli",
            )
        if args.context_command == "update":
            return service.context_update(
                action=args.action,
                context_id=args.context_id,
                expected_version=args.expected_version,
                payload=load_json_object(args.payload_file),
                audience="local_cli",
            )
        if args.context_command == "archive":
            return service.context_update(
                action="archive",
                context_id=args.context_id,
                expected_version=args.expected_version,
                payload={},
                audience="local_cli",
            )
        if args.context_command == "skill":
            if args.context_skill_command == "show":
                return service.context_skill_read(
                    context_id=args.context_id,
                    audience="local_cli",
                )
            if args.context_skill_command == "set":
                return service.context_skill_set(
                    context_id=args.context_id,
                    skill_file=args.skill_file,
                    expected_version=args.expected_version,
                    confirm_context_skill_write=args.confirm_context_skill_write,
                )
            if args.context_skill_command == "remove":
                return service.context_skill_remove(
                    context_id=args.context_id,
                    expected_version=args.expected_version,
                    confirm_context_skill_remove=args.confirm_context_skill_remove,
                )
            raise AssertionError(
                f"unhandled context skill command: {args.context_skill_command}"
            )
        raise AssertionError(f"unhandled context command: {args.context_command}")
    raise AssertionError(f"unhandled command: {args.command}")


def _emit(payload: object, *, pretty: bool, stream) -> None:
    json.dump(
        payload,
        stream,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=pretty,
    )
    stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = execute(args)
    except CorpusError as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
            },
            pretty=args.pretty,
            stream=sys.stderr,
        )
        return 2
    # Keep the command-line boundary machine-readable for unexpected faults.
    except Exception as exc:  # noqa: BLE001
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "unexpected_error",
                    "message": str(exc),
                },
            },
            pretty=args.pretty,
            stream=sys.stderr,
        )
        return 1
    _emit({"ok": True, "result": result}, pretty=args.pretty, stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
