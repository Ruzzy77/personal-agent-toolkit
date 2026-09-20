#!/usr/bin/python3
"""Root-only, fixed-interface IPv4 guard for one Personal Agent Host bridge.

Install this file root-owned at /usr/local/libexec/personal-agent-host-egress-guard
and grant the Host service account passwordless sudo only for its three fixed
subcommands. It accepts no addresses, ports, chains, rule text, or commands.
Every executable path is fixed so the service account's environment is not a
root command-selection boundary.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

DOCKER = "/usr/bin/docker"
IP = "/usr/sbin/ip"
IPTABLES = "/usr/sbin/iptables"
NETWORK = re.compile(r"pah-egress-([0-9a-f]{12})")
LABEL = "personal-agent-host.job"
PRIVATE = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
    "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
)


def run(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=check,
        text=True,
        capture_output=True,
        timeout=15,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )


def bridge(network: str) -> str:
    match = NETWORK.fullmatch(network)
    if match is None:
        raise RuntimeError("invalid network")
    identifier = run(DOCKER, "network", "inspect", "--format", "{{.Id}}", network).stdout.strip()
    label = run(
        DOCKER, "network", "inspect", "--format",
        "{{index .Labels \"" + LABEL + "\"}}", network,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", identifier) or label != match.group(1):
        raise RuntimeError("network is not Host-owned")
    device = "br-" + identifier[:12]
    run(IP, "link", "show", "dev", device)
    return device


def tagged(chain: str, device: str, *rule: str) -> tuple[str, ...]:
    return (IPTABLES, "-w", chain, *rule, "-m", "comment", "--comment", "pah-egress-" + device)


def rules(device: str) -> list[tuple[str, ...]]:
    # INPUT rejects bridge-to-host/gateway traffic. DOCKER-USER is before
    # Docker's forwarding accept rules and does not publish any port.
    values = [tagged("INPUT", device, "-i", device, "-j", "DROP")]
    values += [
        tagged("DOCKER-USER", device, "-o", device, "-m", "conntrack",
               "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN"),
        tagged("DOCKER-USER", device, "-o", device, "-j", "DROP"),
    ]
    values += [
        tagged("DOCKER-USER", device, "-i", device, "-d", destination, "-j", "DROP")
        for destination in PRIVATE
    ]
    values += [
        tagged("DOCKER-USER", device, "-i", device, "-p", protocol, "-j", "RETURN")
        for protocol in ("tcp", "udp", "icmp")
    ]
    values.append(tagged("DOCKER-USER", device, "-i", device, "-j", "DROP"))
    return values


def exists(rule: tuple[str, ...]) -> bool:
    return run(*rule[:2], "-C", *rule[2:], check=False).returncode == 0


def detach(values: list[tuple[str, ...]]) -> None:
    for rule in values:
        while exists(rule):
            run(*rule[:2], "-D", *rule[2:])


def attach(values: list[tuple[str, ...]]) -> None:
    if all(exists(rule) for rule in values):
        return
    detach(values)
    # iptables inserts at position one; reverse preserves the stated order.
    for rule in reversed(values):
        run(*rule[:2], "-I", *rule[2:])


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("must run as root")
    if len(argv) != 3 or argv[1] not in {"attach", "check", "detach"}:
        raise RuntimeError("usage: egress-guard {attach|check|detach} pah-egress-<job>")
    try:
        values = rules(bridge(argv[2]))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        if argv[1] == "detach":
            return 0
        raise
    if argv[1] == "attach":
        attach(values)
    elif argv[1] == "check":
        if not all(exists(rule) for rule in values):
            raise RuntimeError("required guard rule is absent")
    else:
        detach(values)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("egress guard:", exc, file=sys.stderr)
        raise SystemExit(1)
