#!/usr/bin/env python3
"""Resolve a pinned Document Files release; download only during explicit preparation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "dependencies/document-files.json"
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024


def release_lock() -> dict:
    path = Path(os.environ.get("DOCUMENT_FILES_RELEASE_LOCK", LOCK_PATH))
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("schemaVersion") != "document-files.release-lock.v1":
        raise ValueError("Unsupported Document Files release lock")
    if result.get("state") not in {"migration_pending", "pinned"}:
        raise ValueError("Invalid Document Files release state")
    if result["state"] == "pinned":
        if not re.fullmatch(r"\d+\.\d+\.\d+", result.get("version", "")):
            raise ValueError("Invalid Document Files version")
        if not re.fullmatch(r"[0-9a-f]{40}", result.get("sourceCommit", "")):
            raise ValueError("An immutable source commit is required")
        if not {"host", "wheel"} <= result.get("artifacts", {}).keys():
            raise ValueError("Both host and wheel artifacts are required")
        for name in result["artifacts"]:
            artifact = result.get("artifacts", {}).get(name, {})
            if not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", "")):
                raise ValueError(f"Missing {name} artifact checksum")
            url = urllib.parse.urlsplit(artifact.get("url", ""))
            if (
                url.scheme != "https"
                or url.netloc != "github.com"
                or not url.path.startswith("/Ruzzy77/document-files/releases/download/")
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Artifacts must name an exact independent GitHub release"
                )
    return result


def _cache() -> Path:
    return (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "personal-agent-toolkit"
        / "document-files"
    )


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def artifact_path(
    name: str, *, prepare: bool = False, local: Path | None = None
) -> Path:
    lock = release_lock()
    if lock["state"] != "pinned":
        raise ValueError("The independent release has not been activated")
    item = lock["artifacts"][name]
    filename = Path(urllib.parse.urlsplit(item["url"]).path).name
    destination = _cache() / item["sha256"] / filename
    if destination.is_file() and _digest(destination) == item["sha256"]:
        return destination
    if not prepare:
        raise ValueError(
            "Pinned artifact is not prepared; run scripts/document_files_release.py prepare"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staged:
        temporary = Path(staged.name)
        try:
            source = (
                local.open("rb")
                if local is not None
                else urllib.request.urlopen(item["url"], timeout=60)
            )
            with source:
                size = 0
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ValueError("Release artifact exceeds preparation limit")
                    staged.write(block)
            staged.close()
            if _digest(temporary) != item["sha256"]:
                raise ValueError("Release artifact checksum mismatch")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def document_source() -> Path:
    lock = release_lock()
    if lock["state"] == "migration_pending":
        return ROOT / "plugins/document-files"
    archive = artifact_path("host")
    destination = archive.parent / "unpacked"
    # Verify every cached member rather than trusting a mutable extracted cache.
    with zipfile.ZipFile(archive) as package:
        files = [item for item in package.infolist() if not item.is_dir()]
        if (
            len(files) > 10000
            or sum(item.file_size for item in files) > MAX_ARCHIVE_BYTES
        ):
            raise ValueError("Release archive exceeds extraction limits")
        for item in files:
            parts = Path(item.filename).parts
            if (
                not parts
                or parts[0] != "document-files"
                or ".." in parts
                or "\\" in item.filename
                or item.filename.startswith("/")
                or (item.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Unsafe release archive member")
        if destination.exists():
            expected = {item.filename for item in files}
            actual = {
                p.relative_to(destination).as_posix()
                for p in destination.rglob("*")
                if p.is_file()
            }
            if actual == expected and all(
                not (destination / item.filename).is_symlink()
                and (destination / item.filename).read_bytes() == package.read(item)
                for item in files
            ):
                root = destination / "document-files"
                _check_source_identity(root, lock)
                return root
            raise ValueError(
                "Prepared release cache changed; remove that cache and prepare again"
            )
        with tempfile.TemporaryDirectory(dir=archive.parent) as directory:
            staged = Path(directory) / "unpacked"
            staged.mkdir()
            for item in files:
                output = staged / item.filename
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(package.read(item))
                output.chmod(0o755 if (item.external_attr >> 16) & 0o111 else 0o644)
            os.replace(staged, destination)
    root = destination / "document-files"
    try:
        _check_source_identity(root, lock)
    except (ValueError, OSError):
        shutil.rmtree(destination)
        raise
    return root


def _check_source_identity(root: Path, lock: dict) -> None:
    import tomllib

    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    provenance = json.loads((root / "BUILD.json").read_text(encoding="utf-8"))
    if (
        version != lock["version"]
        or provenance.get("version") != lock["version"]
        or provenance.get("sourceCommit") != lock["sourceCommit"]
        or provenance.get("dirtySource") is not False
    ):
        raise ValueError(
            "Release metadata does not match the clean pinned source/version"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "source", "wheel", "status"))
    parser.add_argument("--artifact", default="host")
    parser.add_argument(
        "--file",
        type=Path,
        help="Use an already downloaded artifact, still verifying its pinned checksum",
    )
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(release_lock(), indent=2))
    elif args.command == "prepare":
        print(artifact_path(args.artifact, prepare=True, local=args.file))
    elif args.command == "wheel":
        print(artifact_path("wheel"))
    else:
        print(document_source())


if __name__ == "__main__":
    main()
