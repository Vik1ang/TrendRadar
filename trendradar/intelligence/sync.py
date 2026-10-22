"""Import completed TrendRadar SQLite crawl snapshots without crawling sources."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from trendradar.intelligence.api import IngestionAPI
from trendradar.intelligence.normalize import build_batch
from trendradar.intelligence.pipeline import run_intelligence_pipeline
from trendradar.intelligence.stream import ExportedSnapshot, parse_export_jsonl
from trendradar.storage.base import NewsData, RSSData
from trendradar.storage.local import LocalStorageBackend


class SnapshotReader(Protocol):
    def get_latest_crawl_data(self, date: str | None = None) -> NewsData | None: ...

    def get_latest_rss_data(self, date: str | None = None) -> RSSData | None: ...


class ReadOnlySnapshotStorage(LocalStorageBackend):
    """Use TrendRadar's existing SQLite readers without schema writes."""

    def _get_db_path(self, date: str | None = None, db_type: str = "news") -> Path:
        return Path(self.data_dir) / db_type / f"{self._format_date_folder(date)}.db"

    def _get_connection(self, date: str | None = None, db_type: str = "news") -> sqlite3.Connection:
        db_path = self._get_db_path(date, db_type)
        key = str(db_path)
        if key not in self._db_connections:
            connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            self._db_connections[key] = connection
        return self._db_connections[key]


Pipeline = Callable[..., dict[str, Any] | None]


def _marker(value: NewsData | RSSData | None) -> str | None:
    if value is None:
        return None
    return f"{value.date}:{value.crawl_time}"


def _read_cursors(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        # Replaying the latest batch is safe: article and queue writes are idempotent.
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        key: item for key, item in value.items()
        if isinstance(key, str) and (
            (key in {"news", "rss"} and isinstance(item, str))
            or (key.startswith(("news:", "rss:"))
                and isinstance(item, dict) and isinstance(item.get("recordId"), int))
        )
    }


def _write_cursors(path: Path, cursors: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(cursors, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def sync_exported_snapshots(
    snapshots: list[ExportedSnapshot],
    api: IngestionAPI,
    cursor_path: Path,
    *,
    pipeline: Pipeline = run_intelligence_pipeline,
) -> dict[str, Any]:
    """Import host-streamed snapshots; only commit each source/date cursor after API success."""
    cursors = _read_cursors(cursor_path)
    active_dates = {
        item.strip() for item in os.environ.get("P2_SYNC_DATES", "").split(",") if item.strip()
    }
    if active_dates:
        cursors = {
            key: marker for key, marker in cursors.items()
            if key.rsplit(":", 1)[-1] in active_dates
        }
    by_date: dict[str, dict[str, ExportedSnapshot]] = {}
    for snapshot in snapshots:
        by_date.setdefault(snapshot.date, {})[snapshot.source] = snapshot

    results: list[dict[str, Any]] = []
    imported_sources: list[str] = []
    deduplicated_count = 0
    for day in sorted(by_date):
        changed = {
            source: snapshot for source, snapshot in by_date[day].items()
            if cursors.get(f"{source}:{day}") != snapshot.marker
        }
        if not changed:
            continue
        news_snapshot = changed.get("news")
        rss_snapshot = changed.get("rss")
        news_data = news_snapshot.data if news_snapshot and isinstance(news_snapshot.data, NewsData) else None
        rss_data = rss_snapshot.data if rss_snapshot and isinstance(rss_snapshot.data, RSSData) else None
        _, normalized_articles = build_batch(news_data, rss_data, "Asia/Shanghai")
        raw_count = sum(snapshot.item_count for snapshot in changed.values())
        deduplicated_count += max(0, raw_count - len(normalized_articles))
        metadata = {
            "sqliteSync": {
                "sourceDatabaseDate": day,
                "sources": [
                    {
                        "source": source,
                        "recordId": snapshot.marker["recordId"],
                        "crawlTime": snapshot.marker["crawlTime"],
                        "readCount": snapshot.item_count,
                        "failedCount": len(snapshot.data.failed_ids),
                    }
                    for source, snapshot in sorted(changed.items())
                ],
                "readCount": raw_count,
                "deduplicatedCount": max(0, raw_count - len(normalized_articles)),
                "failureCount": sum(len(snapshot.data.failed_ids) for snapshot in changed.values()),
            }
        }
        result = pipeline(
            news_data,
            rss_data,
            {"TIMEZONE": "Asia/Shanghai"},
            api=api,
            run_metadata=metadata,
        )
        if result is None:
            raise RuntimeError("SQLite import did not complete; retaining source cursors")
        results.append(result)
        advanced = False
        for source, snapshot in changed.items():
            # The API commit succeeded, so acknowledge this snapshot even when
            # an upstream feed failed. The failed-source count is persisted in
            # the partial run; the next crawl is queried from this cursor and
            # will include that source once it succeeds. Replaying an unchanged
            # partial snapshot every poll would create duplicate run noise.
            cursors[f"{source}:{day}"] = snapshot.marker
            imported_sources.append(f"{source}:{day}")
            advanced = True
        if advanced:
            _write_cursors(cursor_path, cursors)

    return {
        "runCount": len(results),
        "sourcesAdvanced": imported_sources,
        "sourceReadCount": sum(snapshot.item_count for snapshot in snapshots),
        "deduplicatedCount": deduplicated_count,
        "failedCount": sum(len(snapshot.data.failed_ids) for snapshot in snapshots),
        "insertedCount": sum(int(result.get("insertedCount", 0)) for result in results),
        "updatedCount": sum(int(result.get("updatedCount", 0)) for result in results),
        "results": results,
    }


def sync_latest_once(
    storage: SnapshotReader,
    api: IngestionAPI,
    cursor_path: Path,
    *,
    today: date,
    pipeline: Pipeline = run_intelligence_pipeline,
) -> dict[str, Any] | None:
    """Import only a newly completed latest crawl from each existing SQLite DB."""
    cursors = _read_cursors(cursor_path)
    news_data = storage.get_latest_crawl_data(today.isoformat())
    rss_data = storage.get_latest_rss_data(today.isoformat())
    news_marker = _marker(news_data)
    rss_marker = _marker(rss_data)
    news_changed = news_marker is not None and cursors.get("news") != news_marker
    rss_changed = rss_marker is not None and cursors.get("rss") != rss_marker
    if not news_changed and not rss_changed:
        return None

    # Do not resend an unchanged source snapshot merely because the other DB changed.
    result = pipeline(
        news_data if news_changed else None,
        rss_data if rss_changed else None,
        {"TIMEZONE": "Asia/Shanghai"},
        api=api,
    )
    if result is None:
        raise RuntimeError("Intelligence import did not run; leaving SQLite cursor unchanged")
    if news_changed and news_marker:
        cursors["news"] = news_marker
    if rss_changed and rss_marker:
        cursors["rss"] = rss_marker
    _write_cursors(cursor_path, cursors)
    return result


def run_stream_once() -> None:
    """Consume a host-produced JSONL stream and import one bounded sync run."""
    if os.environ.get("INTELLIGENCE_ANALYSIS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("SQLite sync is import-only; real model calls are disabled")
    if int(os.environ.get("INTELLIGENCE_MAX_ANALYSES_PER_DAY", "0")) != 0:
        raise RuntimeError("SQLite sync requires INTELLIGENCE_MAX_ANALYSES_PER_DAY=0")
    os.environ["INTELLIGENCE_ENABLED"] = "true"
    snapshots = parse_export_jsonl(sys.stdin)
    cursor_path = Path(os.environ.get("INTELLIGENCE_SYNC_STATE_PATH", "/var/lib/trendradar-sync/cursors.json"))
    api = IngestionAPI()
    try:
        result = sync_exported_snapshots(snapshots, api, cursor_path)
        print(json.dumps({
            "event": "sqlite_sync_completed",
            "runCount": result["runCount"],
            "sourceReadCount": result["sourceReadCount"],
            "insertedCount": result["insertedCount"],
            "updatedCount": result["updatedCount"],
            "failedCount": result["failedCount"],
            "sourcesAdvanced": result["sourcesAdvanced"],
        }), flush=True)
    finally:
        api.session.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--stdin"]:
        run_stream_once()
    else:
        raise SystemExit("supported argument: --stdin")
