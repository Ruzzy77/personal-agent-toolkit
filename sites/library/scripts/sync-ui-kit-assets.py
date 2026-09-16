#!/usr/bin/env python3
"""Install the pinned UI Kit release used by generated Library issue pages."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_ROOT = ROOT / "node_modules" / "@personal-agent" / "ui-kit"
TOOLS = SDK_ROOT / "tools" / "sdk_assets.py"
DESTINATION = ROOT / "public" / "ui-kit" / "current"


def load_package_class():
    spec = importlib.util.spec_from_file_location("personal_agent_ui_kit_assets", TOOLS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"UI Kit asset tool not found: {TOOLS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Package


def main() -> None:
    Package = load_package_class()
    package = Package(SDK_ROOT)
    with tempfile.TemporaryDirectory(prefix="library-ui-kit-", dir=DESTINATION.parent) as temp:
        staged = Path(temp) / "current"
        staged.mkdir()
        names = ("tokens.css", "seomun.css")
        package.install_flat(staged, names=names)
        (staged / "sdk.json").write_text(
            json.dumps(
                package.metadata("linked", base="/ui-kit/current/", names=names),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if DESTINATION.exists():
            shutil.rmtree(DESTINATION)
        staged.replace(DESTINATION)


if __name__ == "__main__":
    main()
