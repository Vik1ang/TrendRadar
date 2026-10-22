"""Read TrendRadar's live SQLite snapshots through a bounded docker exec.

Default mode is read-only dry-run. ``--apply`` streams the validated JSONL to a
one-shot importer container on the private ingestion network; no Docker socket
is mounted into an application or importer container.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trendradar.intelligence.api import IngestionAPI
from trendradar.intelligence.normalize import build_batch
from trendradar.intelligence.stream import ExportedSnapshot, parse_export_jsonl
from trendradar.intelligence.sync import sync_exported_snapshots
from trendradar.storage.base import NewsData, RSSData

MAX_EXPORT_BYTES = 16 * 1024 * 1024
STATE_DIR = Path(os.environ.get(
    "P2_SYNC_STATE_DIR", str(Path.home() / ".local/state/trendradar-p2-sync")
)).expanduser()
CURSOR_PATH = STATE_DIR / "cursors.json"
TREND_RADAR_CONTAINER = os.environ.get("P2_SYNC_TREND_RADAR_CONTAINER", "trendradar")
COMPOSE_PROJECT = os.environ.get("P2_SYNC_COMPOSE_PROJECT", "trendradar-lan-acceptance")
INGEST_CONTAINER = os.environ.get("P2_SYNC_INGEST_CONTAINER", "")

_INGEST_NODE_SCRIPT = r"""
let raw='';
process.stdin.setEncoding('utf8');
process.stdin.on('data', c => { raw += c; if (Buffer.byteLength(raw) > 1048576) process.exit(12); });
process.stdin.on('end', async () => {
  const token = process.env.INGEST_TOKEN;
  if (!token) { console.error(JSON.stringify({code:'INGEST_TOKEN_MISSING'})); process.exit(13); }
  try {
    const response = await fetch('http://127.0.0.1:3002/api/v1/internal/ingestion', {
      method:'POST', headers:{'content-type':'application/json','authorization':`Bearer ${token}`,'user-agent':'TrendRadar-SQLite-Sync/1.0'},
      body:raw, signal:AbortSignal.timeout(20000)
    });
    const body = await response.text();
    if (!response.ok) {
      let code='INGESTION_REJECTED'; try { code=JSON.parse(body)?.error?.code || code; } catch {}
      console.error(JSON.stringify({status:response.status,code})); process.exit(14);
    }
    process.stdout.write(body);
  } catch (error) {
    console.error(JSON.stringify({code: error?.name === 'TimeoutError' ? 'INGESTION_TIMEOUT' : 'INGESTION_TRANSPORT_ERROR'}));
    process.exit(15);
  }
});
"""


def _run(
    command: list[str], *, input_bytes: bytes | None = None, timeout: int = 45,
    env: dict[str, str] | None = None,
) -> bytes:
    try:
        result = subprocess.run(command, input=input_bytes, capture_output=True,
                                timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("bounded Docker operation timed out") from exc
    if result.returncode != 0:
        # Never echo child stderr: container errors can include source data/config.
        raise RuntimeError(f"Docker operation failed (exit={result.returncode})")
    return result.stdout


def _dates() -> list[str]:
    now = datetime.now(ZoneInfo(os.environ.get("TREND_RADAR_TIMEZONE", "Asia/Shanghai")))
    today = now.date()
    return [(today - timedelta(days=1)).isoformat(), today.isoformat()]


def _cursors() -> dict[str, Any]:
    active_dates = set(_dates())
    try:
        raw = CURSOR_PATH.read_text(encoding="utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            return {}
        return {
            key: marker for key, marker in value.items()
            if isinstance(key, str) and key.rsplit(":", 1)[-1] in active_dates
        }
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        # Replaying is safe; stable article IDs and dedupe_key make the import idempotent.
        return {}


def _export() -> list[ExportedSnapshot]:
    cursors = _cursors()
    command = [
        "docker", "exec", "-i", "-e", f"P2_SYNC_DATES={','.join(_dates())}",
        "-e", f"P2_SYNC_CURSOR_JSON={json.dumps(cursors, separators=(',', ':'))}",
        TREND_RADAR_CONTAINER, "python", "-",
    ]
    output = _run(command, input_bytes=(ROOT / "scripts/p2_sqlite_export.py").read_bytes(), timeout=45)
    if len(output) > MAX_EXPORT_BYTES:
        raise RuntimeError("SQLite export exceeded 16 MiB safety limit")
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeError("SQLite export is not UTF-8 JSONL") from exc
    return parse_export_jsonl(lines)


def _postgres_container() -> str:
    configured = os.environ.get("P2_SYNC_POSTGRES_CONTAINER")
    if configured:
        return configured
    ids = _run([
        "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJECT}",
        "--filter", "label=com.docker.compose.service=postgres",
    ]).decode().splitlines()
    if len(ids) != 1:
        raise RuntimeError("could not identify exactly one Intelligence PostgreSQL container")
    return ids[0]


def _ingestion_container() -> str:
    if INGEST_CONTAINER:
        return INGEST_CONTAINER
    ids = _run([
        "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJECT}",
        "--filter", "label=com.docker.compose.service=ingest-api",
    ]).decode().splitlines()
    if len(ids) != 1:
        raise RuntimeError("could not identify exactly one private ingestion API container")
    return ids[0]


def _existing_ids() -> set[str]:
    container = _postgres_container()
    query = "SELECT id::text FROM articles ORDER BY id"
    command = [
        "docker", "exec", container, "sh", "-c",
        'psql -X -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c ' + repr(query),
    ]
    output = _run(command, timeout=20)
    return {line.strip() for line in output.decode().splitlines() if line.strip()}


def _dry_run(snapshots: list[ExportedSnapshot]) -> dict[str, Any]:
    existing = _existing_ids()
    candidates: dict[str, dict[str, Any]] = {}
    raw_read = 0
    per_source: dict[str, dict[str, int]] = {}
    for snapshot in snapshots:
        raw_read += snapshot.item_count
        _, articles = build_batch(
            snapshot.data if isinstance(snapshot.data, NewsData) else None,
            snapshot.data if isinstance(snapshot.data, RSSData) else None,
            "Asia/Shanghai",
        )
        source_key = f"{snapshot.source}:{snapshot.date}"
        per_source[source_key] = {
            "read": snapshot.item_count,
            "failed": len(snapshot.data.failed_ids),
            "normalized": len(articles),
        }
        for article in articles:
            candidates[article["dedupeKey"]] = article

    normalized = list(candidates.values())
    ids = {article["id"] for article in normalized}
    category_counts: Counter[str] = Counter()
    for article in normalized:
        for category in article["categories"]:
            category_counts[category["id"]] += 1
    return {
        "mode": "dry-run-read-only",
        "snapshotCount": len(snapshots),
        "sourceSnapshots": per_source,
        "sourceRowsRead": raw_read,
        "normalizedUnique": len(normalized),
        "duplicateRowsWithinExport": raw_read - len(normalized),
        "estimatedNew": len(ids - existing),
        "estimatedExistingUpserts": len(ids & existing),
        "existingPostgresArticles": len(existing),
        "ruleCategories": dict(sorted(category_counts.items())),
        "uncategorized": sum(not article["categories"] for article in normalized),
        "aiModelCalls": 0,
        "productionWrites": 0,
    }


def _check_migration() -> None:
    container = _postgres_container()
    command = [
        "docker", "exec", container, "sh", "-c",
        ('psql -X -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '
         "\"SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version='002_analysis_queue_and_budget.sql')\""),
    ]
    output = _run(command, timeout=20).decode().strip()
    if output != "t":
        raise RuntimeError("production Migration 002 is not applied; --apply is blocked")


def _check_ai_budget() -> None:
    container = _ingestion_container()
    script = "console.log(JSON.stringify({perRun:process.env.INTELLIGENCE_MAX_ANALYSES_PER_RUN||'1',perDay:process.env.INTELLIGENCE_MAX_ANALYSES_PER_DAY||'0',concurrent:process.env.INTELLIGENCE_ANALYSIS_MAX_CONCURRENT||'1',attempts:process.env.INTELLIGENCE_ANALYSIS_MAX_ATTEMPTS||'2'}))"
    output = _run(["docker", "exec", container, "node", "-e", script], timeout=10)
    try:
        budget = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("could not verify ingestion analysis budget") from exc
    if budget != {"perRun": "1", "perDay": "0", "concurrent": "1", "attempts": "2"}:
        raise RuntimeError("ingestion API analysis budget differs from approved fail-closed values")


class DockerExecIngestionAPI(IngestionAPI):
    """Pass payload via stdin; the independent ingest token stays inside its container."""

    def __init__(self, container: str) -> None:
        self.container = container

    def import_batch(self, body: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 900 * 1024:
            raise RuntimeError("normalized import batch exceeds 900 KiB safety limit")
        output = _run(
            ["docker", "exec", "-i", self.container, "node", "-e", _INGEST_NODE_SCRIPT],
            input_bytes=encoded,
            timeout=30,
        )
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ingestion API returned invalid response JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("runId"), str):
            raise TypeError("ingestion API response lacks run ID")
        return value


def _apply(snapshots: list[ExportedSnapshot]) -> dict[str, Any]:
    _check_migration()
    _check_ai_budget()
    os.environ["INTELLIGENCE_ENABLED"] = "true"
    os.environ["INTELLIGENCE_ANALYSIS_ENABLED"] = "false"
    api = DockerExecIngestionAPI(_ingestion_container())
    return sync_exported_snapshots(snapshots, api, CURSOR_PATH)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the new snapshot to Intelligence PostgreSQL")
    mode.add_argument("--dry-run", action="store_true", help="read/normalize/compare without PostgreSQL writes")
    args = parser.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)
    lock_path = STATE_DIR / "sync.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"mode": "skipped", "reason": "sync_already_running"}))
            return 0
        snapshots = _export()
        if args.apply:
            result = _apply(snapshots)
            print(json.dumps({"mode": "applied", **result}, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(_dry_run(snapshots), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"mode": "failed", "errorType": type(exc).__name__, "detail": str(exc)[:200]}), file=sys.stderr)
        raise SystemExit(1)
