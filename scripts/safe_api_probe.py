#!/usr/bin/env python3
"""Make a small authenticated API probe without logging credentials or bodies.

Run this from a trusted interactive terminal. The bearer is read with echo disabled
and is never accepted as a command-line argument or environment variable.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


def probe(url: str, token: str, timeout: float) -> dict[str, int | str | None]:
    request = Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "TrendRadar-Safe-Probe/1.0",
    }, method="GET")
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            return {"status": response.status, "elapsed_ms": round((time.monotonic() - started) * 1000), "error": None}
    except HTTPError as exc:
        return {"status": exc.code, "elapsed_ms": round((time.monotonic() - started) * 1000), "error": "HTTPError"}
    except (URLError, TimeoutError, OSError) as exc:
        # Exception strings may include request URLs or implementation details.
        # Emit only the exception type; never serialize the exception object.
        return {"status": 0, "elapsed_ms": round((time.monotonic() - started) * 1000), "error": type(exc).__name__}


def validate_target(url: str) -> None:
    parsed = urlsplit(url)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    is_approved_relay = parsed.scheme == "https" and parsed.hostname == "trendradar-intelligence-relay.vik1ang.workers.dev"
    if parsed.username or parsed.password or parsed.fragment or not (is_approved_relay or is_loopback_http):
        raise ValueError("Refusing to send bearer credentials to an unapproved target")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="API endpoint to probe; do not include credentials in the URL")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    try:
        validate_target(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    if not sys.stdin.isatty():
        parser.error("Run from an interactive terminal so hidden credential input is available")
    token = getpass.getpass("API bearer (input hidden; not logged): ")
    if not token or "\n" in token or "\r" in token:
        parser.error("A single-line bearer value is required")
    print(json.dumps(probe(args.url, token, args.timeout), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
