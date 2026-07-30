"""Small local CLI for importing and inspecting the Sense profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .errors import SenseError
from .model import ProfileDocument
from .service import SenseService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sense")
    parser.add_argument(
        "--data-root",
        type=Path,
        help="Override the private Sense data directory.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    import_profile = commands.add_parser(
        "import-profile",
        help="Import a reviewed profile document as a read-only preview.",
    )
    import_profile.add_argument("--input", required=True, type=Path)
    import_profile.add_argument("--replace-preview", action="store_true")
    import_profile.add_argument("--expected-preview-revision", type=int)
    import_profile.add_argument("--expected-preview-digest")

    activate = commands.add_parser(
        "activate",
        help="Activate a reviewed preview after explicit local confirmation.",
    )
    activate.add_argument("--expected-revision", required=True, type=int)
    activate.add_argument("--confirm-profile-digest", required=True)
    activate.add_argument(
        "--confirm-reviewed-profile",
        action="store_true",
        help="Confirm that the current preview was reviewed and should become active.",
    )

    read = commands.add_parser("read", help="Read the current profile.")
    read.add_argument(
        "--view",
        choices=("index", "sections", "full"),
        default="index",
    )
    read.add_argument("--section-id", action="append", default=[])
    read.add_argument("--include-sources", action="store_true")

    history = commands.add_parser(
        "history",
        help="List retained revisions or compare two retained revisions.",
    )
    history.add_argument("--from-revision", type=int)
    history.add_argument("--to-revision", type=int)

    commands.add_parser("status", help="Show private store status.")
    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    service = SenseService(args.data_root)
    if args.command == "import-profile":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        profile = ProfileDocument.model_validate(payload)
        return service.import_profile(
            profile,
            replace_preview=args.replace_preview,
            expected_preview_revision=args.expected_preview_revision,
            expected_preview_digest=args.expected_preview_digest,
        )
    if args.command == "activate":
        return service.control(
            action="activate",
            expected_revision=args.expected_revision,
            confirm_profile_digest=args.confirm_profile_digest,
            trusted_user_action=args.confirm_reviewed_profile,
        )
    if args.command == "read":
        return service.read(
            view=args.view,
            section_ids=args.section_id or None,
            include_sources=args.include_sources,
        )
    if args.command == "history":
        return service.history(
            from_revision=args.from_revision,
            to_revision=args.to_revision,
        )
    if args.command == "status":
        return service.status()
    raise ValueError(f"unsupported command: {args.command}")


def main() -> None:
    args = _parser().parse_args()
    try:
        result = _run(args)
    except (SenseError, OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "ok": False,
            "error": {
                "code": getattr(exc, "code", "invalid_request"),
                "message": str(exc),
                "details": getattr(exc, "details", {}),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1) from None
    print(
        json.dumps(
            {"ok": True, "result": result},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
