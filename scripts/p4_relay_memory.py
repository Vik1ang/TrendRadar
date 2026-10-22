"""Parse Docker CLI memory usage for P4 Relay diagnostics."""

from __future__ import annotations

import re
import subprocess

_MEMORY_RE = re.compile(r"^(?P<amount>(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$")
_BYTES_PER_UNIT = {
    "B": 1,
    "kB": 1_000,
    "KB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "KiB": 1_024,
    "MiB": 1_048_576,
    "GiB": 1_073_741_824,
    "TiB": 1_099_511_627_776,
}


def parse_docker_memory_usage(value: str) -> float:
    """Return the used memory in MiB from ``docker stats``' MemUsage output.

    Docker renders values with the unit attached (for example,
    ``20.16MiB / 755.3GiB``); do not split the number and unit on whitespace.
    """
    used = value.split("/", maxsplit=1)[0].strip()
    match = _MEMORY_RE.fullmatch(used)
    if match is None:
        raise ValueError("Invalid Docker memory usage value")
    amount = float(match.group("amount"))
    bytes_per_unit = _BYTES_PER_UNIT[match.group("unit")]
    return round(amount * bytes_per_unit / _BYTES_PER_UNIT["MiB"], 3)


def read_docker_memory_mib(container: str) -> float:
    """Read a single container memory sample without exposing container env."""
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return parse_docker_memory_usage(result.stdout.strip())
