"""Import completed TrendRadar SQLite crawl snapshots without crawling sources."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from trendradar.intelligence.api import IngestionAPI, IntelligenceAPIError
from trendradar.intelligence.pipeline import run_intelligence_pipeline
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


def _read_cursors(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        # Replaying the latest batch is safe: article and queue writes are idempotent.
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key in {"news", "rss"} and isinstance(item, str)}


def _write_cursors(path: Path, cursors: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(cursors, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


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


def _positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        result = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def run_forever() -> None:
    if os.environ.get("INTELLIGENCE_ANALYSIS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("The SQLite sync service is import-only; automatic analysis must remain disabled")
    if os.environ.get("INTELLIGENCE_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("INTELLIGENCE_ENABLED must be true for the import-only sync service")

    interval = _positive_int("INTELLIGENCE_SYNC_INTERVAL_SECONDS", 60, 15, 3600)
    timezone = os.environ.get("TREND_RADAR_TIMEZONE", "Asia/Shanghai")
    cursor_path = Path(os.environ.get("INTELLIGENCE_SYNC_STATE_PATH", "/var/lib/trendradar-sync/cursors.json"))
    api = IngestionAPI()
    storage = ReadOnlySnapshotStorage(
        data_dir=os.environ.get("TREND_RADAR_OUTPUT_DIR", "/app/output"),
        enable_txt=False,
        enable_html=False,
        timezone=timezone,
    )
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("SQLite ingestion sync started; source crawl remains owned by TrendRadar", flush=True)
    try:
        while not stopping:
            try:
                today = datetime.now(ZoneInfo(timezone)).date()
                result = sync_latest_once(storage, api, cursor_path, today=today)
                if result:
                    print(json.dumps({
                        "event": "snapshot_imported",
                        "articleCount": result.get("articleCount", 0),
                        "insertedCount": result.get("insertedCount", 0),
                        "updatedCount": result.get("updatedCount", 0),
                    }), flush=True)
            except (IntelligenceAPIError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                # Avoid dumping request objects, headers, response bodies, or credentials.
                print(json.dumps({"event": "snapshot_import_failed", "errorType": type(exc).__name__}), flush=True)
            if not stopping:
                time.sleep(interval)
    finally:
        storage.cleanup()
        api.session.close()


if __name__ == "__main__":
    run_forever()
