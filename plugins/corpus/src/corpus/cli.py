"""JSON CLI for Corpus operations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import default_data_root
from .errors import ContextValidationError, CorpusError
from .golden import load_golden_annotation
from .service import (
    CORPUS_INVENTORY_DEFAULT_LIMIT,
    CORPUS_INVENTORY_MAX_LIMIT,
    CORPUS_INVENTORY_MAX_OFFSET,
    CORPUS_OVERVIEW_DEFAULT_ITEMS_PER_CONTEXT,
    CORPUS_OVERVIEW_MAX_ITEMS_PER_CONTEXT,
    CORPUS_READ_DEFAULT_CHARS,
    CORPUS_READ_MAX_CHARS,
    CorpusService,
)
from .session_sources import (
    SESSION_SOURCE_FETCH_DEFAULT_CHARS,
    SESSION_SOURCE_FETCH_MAX_CHARS,
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
    corpus_commands.add_parser("list", help="List registered corpora.")

    overview = commands.add_parser(
        "overview",
        help="Show a readable, read-only overview of corpora and reusable context.",
    )
    overview.add_argument(
        "--max-items-per-context",
        type=int,
        default=CORPUS_OVERVIEW_DEFAULT_ITEMS_PER_CONTEXT,
        help=(
            "Current context items to show per context "
            f"(max {CORPUS_OVERVIEW_MAX_ITEMS_PER_CONTEXT})."
        ),
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
        "--include-derived",
        action="store_true",
        help="Include optional semantic queue and claim counts.",
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

    migrate = commands.add_parser(
        "migrate",
        help="Explicitly migrate a corpus database after creating a private backup.",
    )
    migrate.add_argument("--corpus", required=True)
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Report schema state without changing the database.",
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
        help="Restrict ingest to an exact document id; repeat for multiple documents.",
    )
    ingest.add_argument("--timeout-seconds", type=float, default=120)

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

    search = commands.add_parser(
        "search",
        help="Find possible passages by exact terms; inspect the source before relying on them.",
    )
    search.add_argument("--corpus", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)

    read = commands.add_parser("read", help="Read exact source units by stable id.")
    read.add_argument("--corpus", required=True)
    read.add_argument("--unit-id", action="append", required=True, dest="unit_ids")
    read.add_argument("--neighbor-span", type=int, default=0)
    read.add_argument(
        "--max-chars",
        type=int,
        default=CORPUS_READ_DEFAULT_CHARS,
        help=f"Aggregate source-content character budget (max {CORPUS_READ_MAX_CHARS}).",
    )

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
    source_refresh.add_argument("--run-id", required=True)

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
    context_list.add_argument(
        "--view",
        choices=("restricted", "general"),
        default="restricted",
        help=(
            "Read the complete private context or only its selected items, without private "
            "source links or internal identifiers."
        ),
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
        help=(
            "Private context id, or the public collection id returned by "
            "`context list --view general`."
        ),
    )
    context_show.add_argument(
        "--state",
        choices=("active", "archived"),
        default="active",
    )
    context_show.add_argument(
        "--view",
        choices=("restricted", "general"),
        default="restricted",
        help=(
            "Read the complete private context or only its selected items, without private "
            "source links or internal identifiers."
        ),
    )
    context_show.add_argument(
        "--include-history",
        action="store_true",
        help="Include superseded items created by older runtimes.",
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
        help="Update or approve a named context from a JSON object.",
    )
    context_update.add_argument("--id", required=True, dest="context_id")
    context_update.add_argument(
        "--action",
        required=True,
        choices=(
            "append",
            "supersede",
            "advance_checkpoint",
            "approve_general",
        ),
    )
    context_update.add_argument("--expected-version", required=True, type=int)
    context_update.add_argument(
        "--payload-file",
        required=True,
        metavar="FILE|-",
        help="Read the update payload as a JSON object from FILE or standard input (-).",
    )
    context_update.add_argument(
        "--confirm-general-release",
        action="store_true",
        help=(
            "Confirm the complete set selected for the general view. This changes private "
            "selection state and does not publish or transmit anything."
        ),
    )

    context_archive = context_commands.add_parser(
        "archive",
        help="Archive a named context while preserving its history.",
    )
    context_archive.add_argument("--id", required=True, dest="context_id")
    context_archive.add_argument("--expected-version", required=True, type=int)

    context_commands.add_parser(
        "migrate",
        help="Explicitly migrate the private named-context database.",
    )

    queue = commands.add_parser(
        "interpretation-queue",
        help="Inspect optional persistent semantic-cache maintenance state.",
    )
    queue.add_argument("--corpus", required=True)
    queue.add_argument("--limit", type=int, default=50)
    queue.add_argument(
        "--include-outdated",
        action="store_true",
        help="Include queue items whose active projection does not match the current adapter.",
    )

    material = commands.add_parser(
        "interpretation-material",
        help="Read a bounded source batch for optional semantic-cache maintenance.",
    )
    material.add_argument("--corpus", required=True)
    material.add_argument("--queue-id", required=True)
    material.add_argument("--start-ordinal", type=int)
    material.add_argument("--max-units", type=int, default=40)
    material.add_argument("--max-chars", type=int, default=30_000)

    reconcile = commands.add_parser(
        "reconcile-completed-checkpoint",
        help="Operator-only repair for one verified historical queue residue.",
    )
    reconcile.add_argument("--corpus", required=True)
    reconcile.add_argument("--queue-id", required=True)
    reconcile.add_argument("--expected-snapshot-id", required=True)
    reconcile.add_argument("--expected-updated-at", required=True)

    semantic_context = commands.add_parser(
        "semantic-context",
        help="Inspect the optional legacy source-linked semantic cache.",
    )
    semantic_context.add_argument("--corpus", required=True)
    semantic_context.add_argument("--query")
    semantic_context.add_argument("--limit", type=int, default=50)

    golden = commands.add_parser(
        "evaluate-golden",
        help="Evaluate the current projection against a private-safe golden annotation.",
    )
    golden.add_argument("--annotation", required=True, type=Path)

    doctor = commands.add_parser("doctor", help="Check runtime prerequisites.")
    doctor.add_argument("--corpus")
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
            if args.clear and (
                args.exclude_directory_name or args.exclude_path_prefix
            ):
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
                exclude_path_prefixes=(
                    [] if args.clear else args.exclude_path_prefix
                ),
            )
        if args.corpus_command == "rebind-root":
            return service.rebind_source_root(
                corpus_id=args.corpus_id,
                source_root=args.root,
                expected_source_root=args.expected_root,
            )
        return {"corpora": service.corpora()}
    if args.command == "overview":
        return service.overview(
            audience="local_cli",
            max_items_per_context=args.max_items_per_context,
        )
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
            include_derived=args.include_derived,
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
    if args.command == "migrate":
        if args.dry_run:
            return service.migration_status(args.corpus)
        return service.migrate(args.corpus)
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
    if args.command == "cleanup-source-copies":
        return service.cleanup_source_copies(
            args.corpus,
            confirm_delete=args.confirm_delete_source_copies,
        )
    if args.command == "search":
        return service.search(args.corpus, args.query, limit=args.limit)
    if args.command == "read":
        return service.read_units(
            args.corpus,
            args.unit_ids,
            neighbor_span=args.neighbor_span,
            max_chars=args.max_chars,
        )
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
                confirm_persistent_context_write=True,
                audience="local_cli",
            )
        if args.source_command == "refresh":
            return service.corpus_source_update(
                action="refresh",
                corpus_id=args.corpus,
                binding_id=args.binding_id,
                payload={"run_id": args.run_id},
                confirm_persistent_context_write=True,
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
        raise AssertionError(
            f"unhandled source command: {args.source_command}"
        )
    if args.command == "context":
        if args.context_command == "list":
            return service.context_read(
                context_id=None,
                state=args.state,
                include_history=False,
                limit=args.limit,
                offset=args.offset,
                audience="local_cli",
                view=args.view,
            )
        if args.context_command == "show":
            return service.context_read(
                context_id=args.context_id,
                state=args.state,
                include_history=args.include_history,
                limit=args.limit,
                offset=args.offset,
                audience="local_cli",
                view=args.view,
            )
        if args.context_command == "create":
            return service.context_update(
                action="create",
                context_id=args.context_id,
                expected_version=0,
                payload=load_json_object(args.payload_file),
                confirm_persistent_context_write=True,
                audience="local_cli",
            )
        if args.context_command == "update":
            return service.context_update(
                action=args.action,
                context_id=args.context_id,
                expected_version=args.expected_version,
                payload=load_json_object(args.payload_file),
                confirm_persistent_context_write=True,
                confirm_general_release_approval=(
                    args.confirm_general_release
                ),
                audience="local_cli",
            )
        if args.context_command == "archive":
            return service.context_update(
                action="archive",
                context_id=args.context_id,
                expected_version=args.expected_version,
                payload={},
                confirm_persistent_context_write=True,
                audience="local_cli",
            )
        if args.context_command == "migrate":
            return service.context_migrate()
        raise AssertionError(
            f"unhandled context command: {args.context_command}"
        )
    if args.command == "interpretation-queue":
        return service.interpretation_queue(
            args.corpus,
            limit=args.limit,
            include_outdated=args.include_outdated,
        )
    if args.command == "interpretation-material":
        return service.interpretation_material(
            args.corpus,
            queue_id=args.queue_id,
            start_ordinal=args.start_ordinal,
            max_units=args.max_units,
            max_chars=args.max_chars,
        )
    if args.command == "reconcile-completed-checkpoint":
        return service.reconcile_completed_checkpoint(
            args.corpus,
            queue_id=args.queue_id,
            expected_snapshot_id=args.expected_snapshot_id,
            expected_updated_at=args.expected_updated_at,
        )
    if args.command == "semantic-context":
        return service.semantic_context(args.corpus, query=args.query, limit=args.limit)
    if args.command == "evaluate-golden":
        return service.evaluate_extraction_golden(
            load_golden_annotation(args.annotation)
        )
    if args.command == "doctor":
        return service.doctor(args.corpus)
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
    except Exception as exc:
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
