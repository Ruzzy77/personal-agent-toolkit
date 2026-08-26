"""Private, user-approved workflow guidance attached to Sense sections."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import (
    ConfirmationRequiredError,
    SectionSkillConflictError,
    SectionSkillValidationError,
    UnsafeStorageError,
)
from .model import SECTION_ID_RE
from .store import PRIVATE_DIRECTORY_MODE, PRIVATE_FILE_MODE, SenseStore

SECTION_SKILL_MAX_BYTES = 32 * 1024
SECTION_SKILL_MAX_DESCRIPTION_CHARS = 1_000
SECTION_SKILL_MAX_INSTRUCTIONS_CHARS = 24_000
SECTION_SKILL_VERSION_PREFIX = "sense-section-skill-v1:"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(
    r"\A---\n(?P<frontmatter>.*?)\n---\n(?P<body>.*)\Z",
    re.DOTALL,
)
_PRIVATE_CONTENT_PATTERNS = (
    re.compile("/" + r"Users/[^/\s]+/"),
    re.compile("/" + r"home/[^/\s]+/"),
    re.compile(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]", re.IGNORECASE),
    re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_-]{20,}|"
        r"github_pat_[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
        r"glpat_[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|"
        r"npm_[A-Za-z0-9_-]{20,})\b"
    ),
)


def _skill_version(content: bytes) -> str:
    return SECTION_SKILL_VERSION_PREFIX + hashlib.sha256(content).hexdigest()


def _decode_scalar(value: str, *, field: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SectionSkillValidationError(
                "Section Skill frontmatter contains an invalid quoted value",
                details={"field": field},
            ) from exc
        if not isinstance(decoded, str):
            raise SectionSkillValidationError(
                "Section Skill frontmatter values must be strings",
                details={"field": field},
            )
        return decoded.strip()
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'").strip()
    return value


def _parse_skill(content: bytes) -> dict[str, str]:
    if len(content) > SECTION_SKILL_MAX_BYTES:
        raise SectionSkillValidationError(
            "Section Skill exceeds the supported size",
            details={"maximum_bytes": SECTION_SKILL_MAX_BYTES},
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SectionSkillValidationError("Section Skill must be UTF-8 text") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise SectionSkillValidationError("Section Skill contains a null byte")
    for pattern in _PRIVATE_CONTENT_PATTERNS:
        if pattern.search(text):
            raise SectionSkillValidationError(
                "Section Skill contains private local data that cannot be sent to Chat",
                details={"reason": "private_content_detected"},
            )

    match = _FRONTMATTER_RE.fullmatch(text)
    if match is None:
        raise SectionSkillValidationError(
            "Section Skill must contain YAML frontmatter followed by instructions"
        )
    metadata: dict[str, str] = {}
    for raw_line in match.group("frontmatter").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        normalized_key = key.strip()
        if normalized_key not in {"name", "description"}:
            continue
        if normalized_key in metadata:
            raise SectionSkillValidationError(
                "Section Skill frontmatter contains a duplicate field",
                details={"field": normalized_key},
            )
        metadata[normalized_key] = _decode_scalar(raw_value, field=normalized_key)

    name = metadata.get("name", "")
    description = metadata.get("description", "")
    instructions = match.group("body").strip()
    if not name or len(name) > 64 or _SKILL_NAME_RE.fullmatch(name) is None:
        raise SectionSkillValidationError(
            "Section Skill name must use lowercase hyphen-case and be at most 64 characters"
        )
    if not description or len(description) > SECTION_SKILL_MAX_DESCRIPTION_CHARS:
        raise SectionSkillValidationError(
            "Section Skill description is missing or too long",
            details={"maximum_chars": SECTION_SKILL_MAX_DESCRIPTION_CHARS},
        )
    if not instructions or len(instructions) > SECTION_SKILL_MAX_INSTRUCTIONS_CHARS:
        raise SectionSkillValidationError(
            "Section Skill instructions are missing or too long",
            details={"maximum_chars": SECTION_SKILL_MAX_INSTRUCTIONS_CHARS},
        )
    return {
        "name": name,
        "description": description,
        "instructions": instructions,
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _read_source_skill(path: Path, *, data_root: Path) -> bytes:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = (Path.cwd() / expanded).resolve(strict=False)
    try:
        before = os.lstat(expanded)
    except OSError as exc:
        raise SectionSkillValidationError(
            "Section Skill source file is unavailable",
            details={"reason": f"stat_failed:{exc.errno}"},
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise SectionSkillValidationError(
            "Section Skill source must be a regular file, not a link"
        )
    canonical = expanded.resolve(strict=True)
    if _is_within(canonical, data_root.resolve(strict=False)):
        raise SectionSkillValidationError(
            "Section Skill source must be outside the private Sense runtime"
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(canonical, flags)
    except OSError as exc:
        raise SectionSkillValidationError(
            "Section Skill source file could not be opened",
            details={"reason": f"open_failed:{exc.errno}"},
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise SectionSkillValidationError(
                "Section Skill source changed while it was opened"
            )
        chunks: list[bytes] = []
        remaining = SECTION_SKILL_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
            or final.st_size != opened.st_size
            or final.st_mtime_ns != opened.st_mtime_ns
            or final.st_ctime_ns != opened.st_ctime_ns
        ):
            raise SectionSkillValidationError(
                "Section Skill source changed while it was read"
            )
    finally:
        os.close(descriptor)
    _parse_skill(content)
    return content


def _require_private_directory(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise UnsafeStorageError(
            "Sense Section Skill directory is unavailable",
            details={"path": str(path), "reason": f"stat_failed:{exc.errno}"},
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise UnsafeStorageError(
            "Sense Section Skill storage must use plain directories",
            details={"path": str(path)},
        )
    if metadata.st_uid != os.getuid():
        raise UnsafeStorageError(
            "Sense Section Skill storage must be owned by the current user",
            details={"path": str(path)},
        )
    if stat.S_IMODE(metadata.st_mode) != PRIVATE_DIRECTORY_MODE:
        raise UnsafeStorageError(
            "Sense Section Skill directory permissions are unsafe",
            details={"path": str(path), "mode": oct(stat.S_IMODE(metadata.st_mode))},
        )


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    except FileExistsError:
        pass
    _require_private_directory(path)


class SectionSkillService:
    """Store one approved SKILL.md for each current Sense section."""

    def __init__(self, data_root: Path, *, store: SenseStore) -> None:
        self.data_root = data_root
        self.store = store

    def _sections_root(self) -> Path:
        return self.data_root / "sections"

    def _section_root(self, section_id: str) -> Path:
        return self._sections_root() / section_id

    def _skill_root(self, section_id: str) -> Path:
        return self._section_root(section_id) / "skill"

    def _skill_path(self, section_id: str) -> Path:
        return self._skill_root(section_id) / "SKILL.md"

    @staticmethod
    def _validate_section_id(section_id: str) -> str:
        if not isinstance(section_id, str) or SECTION_ID_RE.fullmatch(section_id) is None:
            raise SectionSkillValidationError(
                "Section Skill section id must use lowercase hyphen-case"
            )
        return section_id

    def _require_section(self, profile: Any, section_id: str) -> None:
        self.store._find_section(profile, section_id)

    def _ensure_skill_root(self, section_id: str) -> Path:
        _require_private_directory(self.data_root)
        for path in (
            self._sections_root(),
            self._section_root(section_id),
            self._skill_root(section_id),
        ):
            _ensure_private_directory(path)
        return self._skill_root(section_id)

    def _read_stored(self, section_id: str) -> tuple[bytes, os.stat_result] | None:
        path = self._skill_path(section_id)
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise UnsafeStorageError(
                "Sense Section Skill could not be inspected",
                details={"path": str(path), "reason": f"stat_failed:{exc.errno}"},
            ) from exc
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise UnsafeStorageError(
                "Sense Section Skill must be a regular file",
                details={"path": str(path)},
            )
        if (
            before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != PRIVATE_FILE_MODE
            or before.st_nlink != 1
        ):
            raise UnsafeStorageError(
                "Sense Section Skill file permissions are unsafe",
                details={"path": str(path), "mode": oct(stat.S_IMODE(before.st_mode))},
            )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise UnsafeStorageError(
                "Sense Section Skill could not be opened",
                details={"path": str(path), "reason": f"open_failed:{exc.errno}"},
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or not stat.S_ISREG(opened.st_mode)
            ):
                raise UnsafeStorageError("Sense Section Skill changed while it was opened")
            chunks: list[bytes] = []
            remaining = SECTION_SKILL_MAX_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        finally:
            os.close(descriptor)
        _parse_skill(content)
        return content, before

    @staticmethod
    def _project(
        content: bytes,
        metadata: os.stat_result,
        *,
        audience: str,
        include_instructions: bool,
        storage_root: Path,
    ) -> dict[str, Any]:
        parsed = _parse_skill(content)
        result: dict[str, Any] = {
            "name": parsed["name"],
            "description": parsed["description"],
            "version": _skill_version(content),
            "updated_at": datetime.fromtimestamp(metadata.st_mtime, UTC).isoformat(),
            "provenance": "user_approved_sense_skill",
            "scope": "linked_section",
            "source_evidence": False,
        }
        if include_instructions:
            result["instructions"] = parsed["instructions"]
        if audience == "local_cli":
            result["storage_path"] = str(storage_root)
        return result

    def read(
        self,
        *,
        section_id: str,
        audience: str = "local_cli",
        include_instructions: bool = True,
        require_section: bool = True,
    ) -> dict[str, Any] | None:
        normalized_id = self._validate_section_id(section_id)
        if audience not in {"local_cli", "external_mcp"}:
            raise SectionSkillValidationError("unsupported Section Skill audience")
        if require_section:
            self._require_section(self.store.read().profile, normalized_id)
        stored = self._read_stored(normalized_id)
        if stored is None:
            return None
        content, metadata = stored
        return self._project(
            content,
            metadata,
            audience=audience,
            include_instructions=include_instructions,
            storage_root=self._skill_root(normalized_id),
        )

    def set(
        self,
        *,
        section_id: str,
        skill_file: Path,
        expected_version: str,
        confirm_section_skill_write: bool,
    ) -> dict[str, Any]:
        normalized_id = self._validate_section_id(section_id)
        if not confirm_section_skill_write:
            raise ConfirmationRequiredError(
                "Section Skill write requires explicit confirmation"
            )
        if not isinstance(expected_version, str) or not expected_version:
            raise SectionSkillValidationError("expected Section Skill version is required")
        content = _read_source_skill(skill_file, data_root=self.data_root)

        with closing(self.store._connect_write()) as connection:
            self.store._begin_exclusive(connection)
            current_profile = self.store._load_current(connection).profile
            self._require_section(current_profile, normalized_id)
            current = self.read(
                section_id=normalized_id,
                audience="local_cli",
                require_section=False,
            )
            observed_version = current["version"] if current is not None else "absent"
            if expected_version != observed_version:
                raise SectionSkillConflictError(
                    "Section Skill changed before it could be replaced",
                    details={
                        "section_id": normalized_id,
                        "expected_version": expected_version,
                        "observed_version": observed_version,
                    },
                )
            new_version = _skill_version(content)
            if new_version == observed_version:
                connection.execute("COMMIT")
                return {
                    "section_id": normalized_id,
                    "changed": False,
                    "skill": current,
                }

            skill_root = self._ensure_skill_root(normalized_id)
            temporary_path = skill_root / f".SKILL.{uuid.uuid4().hex}.tmp"
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(temporary_path, flags, PRIVATE_FILE_MODE)
            try:
                view = memoryview(content)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise UnsafeStorageError("Section Skill write made no progress")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                os.replace(temporary_path, self._skill_path(normalized_id))
                os.chmod(self._skill_path(normalized_id), PRIVATE_FILE_MODE)
                directory_descriptor = os.open(skill_root, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
            except Exception:
                with suppress(FileNotFoundError):
                    temporary_path.unlink()
                raise
            connection.execute("COMMIT")

        skill = self.read(
            section_id=normalized_id,
            audience="local_cli",
            require_section=False,
        )
        return {
            "section_id": normalized_id,
            "changed": True,
            "skill": skill,
        }

    def remove(
        self,
        *,
        section_id: str,
        expected_version: str,
        confirm_section_skill_remove: bool,
    ) -> dict[str, Any]:
        normalized_id = self._validate_section_id(section_id)
        if not confirm_section_skill_remove:
            raise ConfirmationRequiredError(
                "Section Skill removal requires explicit confirmation"
            )
        with closing(self.store._connect_write()) as connection:
            self.store._begin_exclusive(connection)
            current_profile = self.store._load_current(connection).profile
            self._require_section(current_profile, normalized_id)
            current = self.read(
                section_id=normalized_id,
                audience="local_cli",
                require_section=False,
            )
            observed_version = current["version"] if current is not None else "absent"
            if expected_version != observed_version:
                raise SectionSkillConflictError(
                    "Section Skill changed before it could be removed",
                    details={
                        "section_id": normalized_id,
                        "expected_version": expected_version,
                        "observed_version": observed_version,
                    },
                )
            if current is None:
                connection.execute("COMMIT")
                return {"section_id": normalized_id, "changed": False, "skill": None}
            self._skill_path(normalized_id).unlink()
            directory_descriptor = os.open(self._skill_root(normalized_id), os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            connection.execute("COMMIT")
        self._remove_empty_skill_directories(normalized_id)
        return {"section_id": normalized_id, "changed": True, "skill": None}

    def _remove_empty_skill_directories(self, section_id: str) -> None:
        for path in (self._skill_root(section_id), self._section_root(section_id)):
            with suppress(FileNotFoundError, OSError):
                path.rmdir()
        with suppress(FileNotFoundError, OSError):
            self._sections_root().rmdir()

    def purge(self, *, section_id: str) -> list[str]:
        normalized_id = self._validate_section_id(section_id)
        root = self._section_root(normalized_id)
        if not root.exists():
            return []
        _require_private_directory(root)
        shutil.rmtree(root)
        with suppress(FileNotFoundError, OSError):
            self._sections_root().rmdir()
        return [str(root)]

    def purge_all(self) -> list[str]:
        root = self._sections_root()
        if not root.exists():
            return []
        _require_private_directory(root)
        shutil.rmtree(root)
        return [str(root)]
