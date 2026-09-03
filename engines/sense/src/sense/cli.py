"""Small local CLI for importing, inspecting, and deleting Sense data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .errors import ConfirmationRequiredError, SenseError
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
        help="Import one reviewed profile as the current Sense profile.",
    )
    import_profile.add_argument("--input", required=True, type=Path)
    import_profile.add_argument("--replace", action="store_true")
    import_profile.add_argument(
        "--confirm-replace",
        action="store_true",
        help="Confirm permanent replacement of an existing profile.",
    )

    read = commands.add_parser("read", help="Read the current profile.")
    read.add_argument(
        "--view",
        choices=("index", "sections", "full"),
        default="index",
    )
    read.add_argument("--section-id", action="append", default=[])

    section = commands.add_parser(
        "section",
        help="Manage content attached to one current Sense section.",
    )
    section_commands = section.add_subparsers(
        dest="section_command",
        required=True,
    )
    section_skill = section_commands.add_parser(
        "skill",
        help="Read or change the approved workflow guidance attached to one section.",
    )
    section_skill_commands = section_skill.add_subparsers(
        dest="section_skill_command",
        required=True,
    )
    section_skill_show = section_skill_commands.add_parser(
        "show",
        help="Read the Section Skill and its current version token.",
    )
    section_skill_show.add_argument("--id", required=True, dest="section_id")

    section_skill_set = section_skill_commands.add_parser(
        "set",
        help="Copy one reviewed SKILL.md into the private section skill folder.",
    )
    section_skill_set.add_argument("--id", required=True, dest="section_id")
    section_skill_set.add_argument(
        "--skill-file",
        required=True,
        type=Path,
        help="Reviewed SKILL.md to store with the selected Sense section.",
    )
    section_skill_set.add_argument(
        "--expected-version",
        required=True,
        help="Current Section Skill version, or 'absent' when creating it.",
    )
    section_skill_set.add_argument(
        "--confirm-section-skill-write",
        action="store_true",
        help="Confirm that this guidance may be returned to Chat with the selected section.",
    )

    section_skill_remove = section_skill_commands.add_parser(
        "remove",
        help="Remove the approved Section Skill without changing the source draft.",
    )
    section_skill_remove.add_argument("--id", required=True, dest="section_id")
    section_skill_remove.add_argument("--expected-version", required=True)
    section_skill_remove.add_argument(
        "--confirm-section-skill-remove",
        action="store_true",
    )

    remove_section = commands.add_parser(
        "remove-section",
        help="Permanently remove one current Sense section.",
    )
    remove_section.add_argument("--section-id", required=True)
    remove_section.add_argument("--previous-section-sha256", required=True)
    remove_section.add_argument("--confirm-permanent-delete", action="store_true")

    remove_database = commands.add_parser(
        "remove-database",
        help="Permanently remove all Sense profile data.",
    )
    remove_database.add_argument("--confirm-permanent-delete", action="store_true")

    commands.add_parser("status", help="Show the local store status.")
    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "import-profile" and args.replace and not args.confirm_replace:
        raise ConfirmationRequiredError(
            "replacing the current Sense profile requires --confirm-replace"
        )
    if args.command in {"remove-section", "remove-database"} and not (
        args.confirm_permanent_delete
    ):
        raise ConfirmationRequiredError(
            "permanent deletion requires --confirm-permanent-delete"
        )
    service = SenseService(args.data_root)
    if args.command == "import-profile":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        profile = ProfileDocument.model_validate(payload)
        return service.import_profile(
            profile,
            replace=args.replace,
            trusted_user_action=args.confirm_replace,
        )
    if args.command == "read":
        return service.read(
            view=args.view,
            section_ids=args.section_id or None,
        )
    if args.command == "section":
        if args.section_command == "skill":
            if args.section_skill_command == "show":
                return service.section_skill_read(
                    section_id=args.section_id,
                    audience="local_cli",
                )
            if args.section_skill_command == "set":
                return service.section_skill_set(
                    section_id=args.section_id,
                    skill_file=args.skill_file,
                    expected_version=args.expected_version,
                    confirm_section_skill_write=args.confirm_section_skill_write,
                )
            if args.section_skill_command == "remove":
                return service.section_skill_remove(
                    section_id=args.section_id,
                    expected_version=args.expected_version,
                    confirm_section_skill_remove=args.confirm_section_skill_remove,
                )
            raise AssertionError(
                f"unsupported Section Skill command: {args.section_skill_command}"
            )
        raise AssertionError(f"unsupported section command: {args.section_command}")
    if args.command == "remove-section":
        return service.remove_section(
            section_id=args.section_id,
            previous_section_sha256=args.previous_section_sha256,
            trusted_user_action=args.confirm_permanent_delete,
        )
    if args.command == "remove-database":
        return service.remove_database(
            trusted_user_action=args.confirm_permanent_delete,
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
