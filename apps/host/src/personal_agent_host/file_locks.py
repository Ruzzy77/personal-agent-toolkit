"""Serialize file mutations across roots, including overlapping root aliases."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

_mutation_lock = RLock()


def root_lock(_root: Path) -> RLock:
    # A root may alias a subtree of another root. One reentrant lock keeps
    # version checks and replacements atomic across both API entry points.
    return _mutation_lock
