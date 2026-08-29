"""Runtime paths and source-boundary validation."""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError, SourceBoundaryError, WorkspaceValidationError

CORPUS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WORKSPACE_ID_RE = CORPUS_ID_RE
EXECUTION_POLICIES = {"local_only", "external_host_allowed"}
SOURCE_SCOPE_MAX_DIRECTORY_NAMES = 32
SOURCE_SCOPE_MAX_PATH_PREFIXES = 64
SOURCE_SCOPE_MAX_COMPONENT_CHARS = 128
SOURCE_SCOPE_MAX_PATH_CHARS = 512
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


def default_data_root() -> Path:
    configured = os.environ.get("CORPUS_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "Library" / "Application Support" / "Corpus").resolve()


def normalize_corpus_id(corpus_id: str) -> str:
    normalized = corpus_id.strip().lower().replace(" ", "-")
    if not CORPUS_ID_RE.fullmatch(normalized):
        raise ConfigurationError(
            "corpus id must be 1-64 lowercase letters, digits, dots, underscores, or hyphens",
            details={"corpus_id": corpus_id, "normalized": normalized},
        )
    return normalized


def normalize_workspace_id(workspace_id: str) -> str:
    if not isinstance(workspace_id, str):
        raise WorkspaceValidationError("workspace id must be a string")
    normalized = workspace_id.strip().lower().replace(" ", "-")
    if not WORKSPACE_ID_RE.fullmatch(normalized):
        raise WorkspaceValidationError(
            "workspace id must be 1-64 lowercase letters, digits, dots, underscores, or hyphens",
            details={"workspace_id": workspace_id, "normalized": normalized},
        )
    return normalized


def normalize_source_scope(
    *,
    exclude_directory_names: object = (),
    exclude_path_prefixes: object = (),
) -> dict[str, list[str]]:
    if not isinstance(exclude_directory_names, (list, tuple)):
        raise ConfigurationError(
            "exclude_directory_names must be a list",
            details={"field": "exclude_directory_names"},
        )
    if not isinstance(exclude_path_prefixes, (list, tuple)):
        raise ConfigurationError(
            "exclude_path_prefixes must be a list",
            details={"field": "exclude_path_prefixes"},
        )
    if len(exclude_directory_names) > SOURCE_SCOPE_MAX_DIRECTORY_NAMES:
        raise ConfigurationError(
            "too many excluded directory names",
            details={"maximum": SOURCE_SCOPE_MAX_DIRECTORY_NAMES},
        )
    if len(exclude_path_prefixes) > SOURCE_SCOPE_MAX_PATH_PREFIXES:
        raise ConfigurationError(
            "too many excluded path prefixes",
            details={"maximum": SOURCE_SCOPE_MAX_PATH_PREFIXES},
        )

    normalized_names: set[str] = set()
    for raw_name in exclude_directory_names:
        if not isinstance(raw_name, str):
            raise ConfigurationError(
                "excluded directory names must be strings",
                details={"field": "exclude_directory_names"},
            )
        name = unicodedata.normalize("NFC", raw_name.strip())
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or len(name) > SOURCE_SCOPE_MAX_COMPONENT_CHARS
        ):
            raise ConfigurationError(
                "excluded directory name must be one plain path component",
                details={"value": raw_name},
            )
        normalized_names.add(name)

    normalized_prefixes: set[str] = set()
    for raw_prefix in exclude_path_prefixes:
        if not isinstance(raw_prefix, str):
            raise ConfigurationError(
                "excluded path prefixes must be strings",
                details={"field": "exclude_path_prefixes"},
            )
        prefix = unicodedata.normalize("NFC", raw_prefix.strip().replace("\\", "/"))
        parts = prefix.split("/")
        if (
            not prefix
            or prefix.startswith("/")
            or prefix.endswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or "\x00" in prefix
            or len(prefix) > SOURCE_SCOPE_MAX_PATH_CHARS
        ):
            raise ConfigurationError(
                "excluded path prefix must be a normalized root-relative path",
                details={"value": raw_prefix},
            )
        normalized_prefixes.add("/".join(parts))

    return {
        "exclude_directory_names": sorted(normalized_names),
        "exclude_path_prefixes": sorted(normalized_prefixes),
    }


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_source_root(source_root: Path, data_root: Path) -> Path:
    source_root = source_root.expanduser().resolve(strict=True)
    data_root = data_root.expanduser().resolve(strict=False)
    if not source_root.is_dir():
        raise ConfigurationError(
            "source root is not a directory",
            details={"source_root": str(source_root)},
        )
    if is_within(data_root, source_root) or is_within(source_root, data_root):
        raise SourceBoundaryError(
            "runtime data and source roots must not overlap",
            details={"source_root": str(source_root), "data_root": str(data_root)},
        )
    return source_root


def _runtime_path_error(
    path: Path, reason: str, *, mode: int | None = None
) -> ConfigurationError:
    details: dict[str, str | int] = {"path": str(path), "reason": reason}
    if mode is not None:
        details["mode"] = oct(stat.S_IMODE(mode))
    return ConfigurationError("private runtime path is unsafe", details=details)


def _require_plain_name(name: str, *, path: Path) -> None:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise _runtime_path_error(path, "invalid_relative_name")


def _require_private_directory(metadata: os.stat_result, *, path: Path) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise _runtime_path_error(path, "not_directory", mode=metadata.st_mode)
    if metadata.st_uid != os.geteuid():
        raise _runtime_path_error(path, "wrong_owner", mode=metadata.st_mode)
    if stat.S_IMODE(metadata.st_mode) != PRIVATE_DIRECTORY_MODE:
        error = _runtime_path_error(path, "unsafe_permissions", mode=metadata.st_mode)
        error.details["operator_action"] = (
            "verify this owned directory, then set its mode to 0700 before retrying"
        )
        raise error


def _require_private_regular_file(
    metadata: os.stat_result,
    *,
    path: Path,
    expected_mode: int = PRIVATE_FILE_MODE,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise _runtime_path_error(path, "not_regular_file", mode=metadata.st_mode)
    if metadata.st_uid != os.geteuid():
        raise _runtime_path_error(path, "wrong_owner", mode=metadata.st_mode)
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise _runtime_path_error(path, "unsafe_permissions", mode=metadata.st_mode)
    if metadata.st_nlink != 1:
        raise _runtime_path_error(path, "unexpected_link_count", mode=metadata.st_mode)


def _open_directory_at(
    parent_descriptor: int,
    name: str,
    *,
    path: Path,
    create: bool,
    require_private: bool,
) -> int:
    _require_plain_name(name, path=path)
    created = False
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise _runtime_path_error(path, "missing") from None
        try:
            os.mkdir(name, PRIVATE_DIRECTORY_MODE, dir_fd=parent_descriptor)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise _runtime_path_error(path, f"create_failed:{exc.errno}") from exc
        try:
            before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise _runtime_path_error(
                path, f"lstat_after_create_failed:{exc.errno}"
            ) from exc
    except OSError as exc:
        raise _runtime_path_error(path, f"lstat_failed:{exc.errno}") from exc

    if not stat.S_ISDIR(before.st_mode):
        raise _runtime_path_error(path, "not_directory", mode=before.st_mode)
    if require_private or created:
        _require_private_directory(before, path=path)

    try:
        descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise _runtime_path_error(path, f"open_failed:{exc.errno}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not stat.S_ISDIR(opened.st_mode)
        ):
            raise _runtime_path_error(path, "changed_during_open")
        if require_private or created:
            _require_private_directory(opened, path=path)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def open_private_directory(path: Path, *, create: bool = False) -> int:
    """Open one owned directory below a canonicalized, pre-existing parent."""

    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise _runtime_path_error(expanded, "not_absolute")
    missing_parents: list[str] = []
    existing_parent = expanded.parent
    while True:
        try:
            canonical_parent = existing_parent.resolve(strict=True)
            break
        except FileNotFoundError:
            if not create or existing_parent == existing_parent.parent:
                raise _runtime_path_error(expanded, "missing_parent") from None
            missing_parents.append(existing_parent.name)
            existing_parent = existing_parent.parent
    canonical = canonical_parent.joinpath(*reversed(missing_parents), expanded.name)
    parts = canonical.parts
    if len(parts) < 2:
        raise _runtime_path_error(expanded, "root_not_allowed")

    descriptor = os.open(parts[0], _DIRECTORY_OPEN_FLAGS)
    missing_parent_created = False
    try:
        for index, name in enumerate(parts[1:], start=1):
            component_path = Path(*parts[: index + 1])
            target = index == len(parts) - 1
            try:
                next_descriptor = _open_directory_at(
                    descriptor,
                    name,
                    path=component_path,
                    create=create and (target or missing_parent_created),
                    require_private=target,
                )
            except ConfigurationError as exc:
                if create and not target and exc.details.get("reason") == "missing":
                    missing_parent_created = True
                    next_descriptor = _open_directory_at(
                        descriptor,
                        name,
                        path=component_path,
                        create=True,
                        require_private=True,
                    )
                else:
                    raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def ensure_private_directory_at(
    parent_descriptor: int,
    name: str,
    *,
    path: Path,
) -> int:
    """Create or open one private child directory relative to a verified parent."""

    return _open_directory_at(
        parent_descriptor,
        name,
        path=path,
        create=True,
        require_private=True,
    )


def open_private_file_at(
    parent_descriptor: int,
    name: str,
    *,
    path: Path,
    flags: int = os.O_RDONLY,
    create: bool = False,
    exclusive: bool = False,
    expected_mode: int = PRIVATE_FILE_MODE,
) -> tuple[int, bool]:
    """Open a private regular file relative to a verified directory descriptor."""

    _require_plain_name(name, path=path)
    base_flags = flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    created = False
    before: os.stat_result | None = None
    if create:
        try:
            descriptor = os.open(
                name,
                base_flags | os.O_CREAT | os.O_EXCL,
                expected_mode,
                dir_fd=parent_descriptor,
            )
            created = True
        except FileExistsError:
            if exclusive:
                raise _runtime_path_error(path, "already_exists") from None
            create = False
        except OSError as exc:
            raise _runtime_path_error(path, f"create_failed:{exc.errno}") from exc
    if not create and not created:
        try:
            before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            raise _runtime_path_error(path, "missing") from None
        except OSError as exc:
            raise _runtime_path_error(path, f"lstat_failed:{exc.errno}") from exc
        _require_private_regular_file(before, path=path, expected_mode=expected_mode)
        try:
            descriptor = os.open(name, base_flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise _runtime_path_error(path, f"open_failed:{exc.errno}") from exc

    try:
        opened = os.fstat(descriptor)
        _require_private_regular_file(opened, path=path, expected_mode=expected_mode)
        if before is not None and (
            opened.st_dev != before.st_dev or opened.st_ino != before.st_ino
        ):
            raise _runtime_path_error(path, "changed_during_open")
        return descriptor, created
    except Exception:
        os.close(descriptor)
        if created:
            with suppress(OSError):
                os.unlink(name, dir_fd=parent_descriptor)
        raise


@contextmanager
def private_directory(path: Path, *, create: bool = False) -> Iterator[int]:
    descriptor = open_private_directory(path, create=create)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    corpus_id: str

    @property
    def catalog_db(self) -> Path:
        return self.data_root / "catalog.sqlite"

    @property
    def corpus_root(self) -> Path:
        return self.data_root / "corpora" / self.corpus_id

    @property
    def corpus_db(self) -> Path:
        return self.corpus_root / "corpus.sqlite"

    @property
    def blobs(self) -> Path:
        return self.corpus_root / "blobs"

    @property
    def staging(self) -> Path:
        return self.corpus_root / "staging"

    @property
    def runtime(self) -> Path:
        return self.data_root / "runtime"

    @contextmanager
    def open_corpus_root(self) -> Iterator[int]:
        descriptors: list[int] = []
        try:
            data_descriptor = open_private_directory(self.data_root)
            descriptors.append(data_descriptor)
            corpora_descriptor = _open_directory_at(
                data_descriptor,
                "corpora",
                path=self.data_root / "corpora",
                create=False,
                require_private=True,
            )
            descriptors.append(corpora_descriptor)
            corpus_descriptor = _open_directory_at(
                corpora_descriptor,
                self.corpus_id,
                path=self.corpus_root,
                create=False,
                require_private=True,
            )
            descriptors.append(corpus_descriptor)
            yield corpus_descriptor
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @contextmanager
    def open_corpus_directory(self, name: str) -> Iterator[int]:
        allowed = {"blobs": self.blobs, "staging": self.staging}
        try:
            path = allowed[name]
        except KeyError as exc:
            raise _runtime_path_error(
                self.corpus_root / name, "unknown_owned_directory"
            ) from exc
        with self.open_corpus_root() as corpus_descriptor:
            child_descriptor = _open_directory_at(
                corpus_descriptor,
                name,
                path=path,
                create=False,
                require_private=True,
            )
            try:
                yield child_descriptor
            finally:
                os.close(child_descriptor)

    @contextmanager
    def open_runtime(self) -> Iterator[int]:
        with private_directory(self.data_root) as data_descriptor:
            runtime_descriptor = _open_directory_at(
                data_descriptor,
                "runtime",
                path=self.runtime,
                create=False,
                require_private=True,
            )
            try:
                yield runtime_descriptor
            finally:
                os.close(runtime_descriptor)

    def ensure(self) -> None:
        descriptors: list[int] = []
        try:
            data_descriptor = open_private_directory(self.data_root, create=True)
            descriptors.append(data_descriptor)
            corpora_path = self.data_root / "corpora"
            corpora_descriptor = ensure_private_directory_at(
                data_descriptor,
                "corpora",
                path=corpora_path,
            )
            descriptors.append(corpora_descriptor)
            corpus_descriptor = ensure_private_directory_at(
                corpora_descriptor,
                self.corpus_id,
                path=self.corpus_root,
            )
            descriptors.append(corpus_descriptor)
            for name, path in (
                ("blobs", self.blobs),
                ("staging", self.staging),
            ):
                child_descriptor = ensure_private_directory_at(
                    corpus_descriptor,
                    name,
                    path=path,
                )
                os.close(child_descriptor)
            runtime_descriptor = ensure_private_directory_at(
                data_descriptor,
                "runtime",
                path=self.runtime,
            )
            os.close(runtime_descriptor)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


@dataclass(frozen=True)
class WorkspaceRuntimePaths:
    """Private Corpus-owned files used to stage and recover workspace writes."""

    data_root: Path
    workspace_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspace_id",
            normalize_workspace_id(self.workspace_id),
        )

    @property
    def workspace_db(self) -> Path:
        return self.data_root / "workspaces.sqlite3"

    @property
    def runtime_root(self) -> Path:
        return self.data_root / "workspace-runtime"

    @property
    def workspace_root(self) -> Path:
        return self.runtime_root / self.workspace_id

    @property
    def recovery(self) -> Path:
        return self.workspace_root / "recovery"

    @property
    def staging(self) -> Path:
        return self.workspace_root / "staging"

    @property
    def trash(self) -> Path:
        return self.workspace_root / "trash"

    @contextmanager
    def open_workspace_root(self) -> Iterator[int]:
        descriptors: list[int] = []
        try:
            data_descriptor = open_private_directory(self.data_root)
            descriptors.append(data_descriptor)
            runtime_descriptor = _open_directory_at(
                data_descriptor,
                "workspace-runtime",
                path=self.runtime_root,
                create=False,
                require_private=True,
            )
            descriptors.append(runtime_descriptor)
            workspace_descriptor = _open_directory_at(
                runtime_descriptor,
                self.workspace_id,
                path=self.workspace_root,
                create=False,
                require_private=True,
            )
            descriptors.append(workspace_descriptor)
            yield workspace_descriptor
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @contextmanager
    def open_workspace_directory(self, name: str) -> Iterator[int]:
        allowed = {
            "recovery": self.recovery,
            "staging": self.staging,
            "trash": self.trash,
        }
        try:
            path = allowed[name]
        except KeyError as exc:
            raise _runtime_path_error(
                self.workspace_root / name,
                "unknown_owned_directory",
            ) from exc
        with self.open_workspace_root() as workspace_descriptor:
            child_descriptor = _open_directory_at(
                workspace_descriptor,
                name,
                path=path,
                create=False,
                require_private=True,
            )
            try:
                yield child_descriptor
            finally:
                os.close(child_descriptor)

    def ensure(self) -> None:
        descriptors: list[int] = []
        try:
            data_descriptor = open_private_directory(self.data_root, create=True)
            descriptors.append(data_descriptor)
            runtime_descriptor = ensure_private_directory_at(
                data_descriptor,
                "workspace-runtime",
                path=self.runtime_root,
            )
            descriptors.append(runtime_descriptor)
            workspace_descriptor = ensure_private_directory_at(
                runtime_descriptor,
                self.workspace_id,
                path=self.workspace_root,
            )
            descriptors.append(workspace_descriptor)
            for name, path in (
                ("recovery", self.recovery),
                ("staging", self.staging),
                ("trash", self.trash),
            ):
                child_descriptor = ensure_private_directory_at(
                    workspace_descriptor,
                    name,
                    path=path,
                )
                os.close(child_descriptor)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
