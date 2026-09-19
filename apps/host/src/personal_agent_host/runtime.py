"""Report installed sandbox profiles without executing user work."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personal_agent_host.config import HostConfig


async def inspect_profile(name: str, image: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name, "image": image, "available": False, "features": [],
    }
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", "--format",
            "{{.Id}}\n{{json .Config.Labels}}", image,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
            result["reason"] = "image_inspection_timed_out"
            return result
        if process.returncode:
            result["reason"] = "image_not_installed"
            return result
        identifier, labels_json = stdout.decode("utf-8").strip().split("\n", 1)
        labels = json.loads(labels_json) or {}
        features = json.loads(labels.get("org.personal-agent.features", "[]"))
        if not isinstance(features, list) or not all(
            isinstance(item, str) for item in features
        ):
            raise ValueError("invalid feature manifest")
        result.update(available=True, image_id=identifier, features=features)
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        result["reason"] = "image_inspection_failed"
    return result


async def execution_capabilities(config: HostConfig) -> dict[str, Any]:
    profiles = config.execution_profiles or {"base": config.sandbox_image}
    values = await asyncio.gather(
        *(inspect_profile(name, image) for name, image in sorted(profiles.items()))
    )
    return {
        "default_profile": "base",
        "profiles": values,
        "network": {
            "default": "none",
            "allowed_https_hosts": sorted(config.https_host_allowlist),
            "policy": "https_connect_destinations",
        },
    }
