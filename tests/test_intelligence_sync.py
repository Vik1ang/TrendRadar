from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from trendradar.intelligence.api import IngestionAPI
from trendradar.intelligence.stream import ExportedSnapshot
from trendradar.intelligence.sync import (
    ReadOnlySnapshotStorage,
    sync_exported_snapshots,
    sync_latest_once,
)
from trendradar.storage.base import NewsData, RSSData


class SnapshotStorage:
    def __init__(self, news: NewsData | None = None, rss: RSSData | None = None) -> None:
        self.news = news
        self.rss = rss

    def get_latest_crawl_data(self, date: str | None = None) -> NewsData | None:
        return self.news

    def get_latest_rss_data(self, date: str | None = None) -> RSSData | None:
        return self.rss


def test_sync_imports_only_new_latest_snapshots_and_persists_cursor(tmp_path: Path) -> None:
    news = NewsData(date="2026-10-22", crawl_time="11:02", items={"baidu": []})
    rss = RSSData(date="2026-10-22", crawl_time="11:02", items={"feed": []})
    calls: list[tuple[NewsData | None, RSSData | None]] = []

    def pipeline(news_data: NewsData | None, rss_data: RSSData | None, _config: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append((news_data, rss_data))
        return {"articleCount": 2, "insertedCount": 2, "updatedCount": 0}

    storage = SnapshotStorage(news, rss)
    cursor_path = tmp_path / "state" / "cursors.json"
    result = sync_latest_once(storage, cast(IngestionAPI, object()), cursor_path, today=date(2026, 10, 22), pipeline=pipeline)

    assert result == {"articleCount": 2, "insertedCount": 2, "updatedCount": 0}
    assert calls == [(news, rss)]
    assert json.loads(cursor_path.read_text()) == {"news": "2026-10-22:11:02", "rss": "2026-10-22:11:02"}
    assert cursor_path.stat().st_mode & 0o777 == 0o600
    assert sync_latest_once(storage, cast(IngestionAPI, object()), cursor_path, today=date(2026, 10, 22), pipeline=pipeline) is None
    assert len(calls) == 1


def test_sync_retries_failed_import_without_advancing_cursor(tmp_path: Path) -> None:
    news = NewsData(date="2026-10-22", crawl_time="11:32", items={"baidu": []})
    storage = SnapshotStorage(news=news)
    cursor_path = tmp_path / "cursors.json"

    def failed_pipeline(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("temporary failure")

    with pytest.raises(RuntimeError, match="temporary failure"):
        sync_latest_once(storage, cast(IngestionAPI, object()), cursor_path, today=date(2026, 10, 22), pipeline=failed_pipeline)
    assert not cursor_path.exists()

    calls = 0

    def successful_pipeline(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"articleCount": 1, "insertedCount": 1, "updatedCount": 0}

    sync_latest_once(storage, cast(IngestionAPI, object()), cursor_path, today=date(2026, 10, 22), pipeline=successful_pipeline)
    assert calls == 1
    assert json.loads(cursor_path.read_text()) == {"news": "2026-10-22:11:32"}


def test_sync_does_not_resend_unchanged_source_when_other_source_changes(tmp_path: Path) -> None:
    news = NewsData(date="2026-10-22", crawl_time="11:02", items={"baidu": []})
    rss = RSSData(date="2026-10-22", crawl_time="11:32", items={"feed": []})
    cursor_path = tmp_path / "cursors.json"
    cursor_path.write_text(json.dumps({"news": "2026-10-22:11:02"}))
    calls: list[tuple[NewsData | None, RSSData | None]] = []

    def pipeline(news_data: NewsData | None, rss_data: RSSData | None, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append((news_data, rss_data))
        return {"articleCount": 1}

    sync_latest_once(SnapshotStorage(news, rss), cast(IngestionAPI, object()), cursor_path, today=date(2026, 10, 22), pipeline=pipeline)
    assert calls == [(None, rss)]


def test_snapshot_storage_opens_sqlite_read_only_without_creating_schema(tmp_path: Path) -> None:
    database = tmp_path / "news" / "2026-10-22.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_table (id INTEGER PRIMARY KEY)")

    storage = ReadOnlySnapshotStorage(data_dir=str(tmp_path), enable_txt=False, enable_html=False)
    connection = storage._get_connection("2026-10-22")
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert [row[0] for row in tables] == ["existing_table"]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("CREATE TABLE forbidden_table (id INTEGER)")
    storage.cleanup()


def test_stream_sync_advances_date_scoped_cursors_only_after_persist_and_is_idempotent(tmp_path: Path) -> None:
    snapshots = [
        ExportedSnapshot(
            "news", day, {"recordId": record_id, "crawlTime": crawl_time, "createdAt": "created"},
            NewsData(date=day, crawl_time=crawl_time, items={"source": []}), 0,
        )
        for day, record_id, crawl_time in (
            ("2026-10-21", 4, "23-32"), ("2026-10-22", 1, "00-02"),
        )
    ]
    cursor_path = tmp_path / "state" / "cursors.json"
    calls: list[dict[str, Any]] = []

    def pipeline(_news: NewsData | None, _rss: RSSData | None, _config: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["run_metadata"])
        return {"runId": f"run-{len(calls)}", "insertedCount": 0, "updatedCount": 0}

    first = sync_exported_snapshots(snapshots, cast(IngestionAPI, object()), cursor_path, pipeline=pipeline)
    assert first["runCount"] == 2
    assert first["sourcesAdvanced"] == ["news:2026-10-21", "news:2026-10-22"]
    assert [item["sqliteSync"]["sourceDatabaseDate"] for item in calls] == ["2026-10-21", "2026-10-22"]
    persisted = json.loads(cursor_path.read_text())
    assert persisted["news:2026-10-21"]["recordId"] == 4
    assert persisted["news:2026-10-22"]["recordId"] == 1

    second = sync_exported_snapshots(snapshots, cast(IngestionAPI, object()), cursor_path, pipeline=pipeline)
    assert second["runCount"] == 0
    assert len(calls) == 2


def test_stream_sync_keeps_cursor_when_ingestion_fails_and_records_partial_source_failure(tmp_path: Path) -> None:
    data = NewsData(date="2026-10-22", crawl_time="10-02", items={"source": []}, failed_ids=["broken-source"])
    snapshot = ExportedSnapshot(
        "news", "2026-10-22", {"recordId": 9, "crawlTime": "10-02", "createdAt": "created"}, data, 0,
    )
    cursor_path = tmp_path / "cursors.json"

    def failed_pipeline(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("temporary ingestion failure")

    with pytest.raises(RuntimeError, match="temporary ingestion failure"):
        sync_exported_snapshots([snapshot], cast(IngestionAPI, object()), cursor_path, pipeline=failed_pipeline)
    assert not cursor_path.exists()

    metadata: dict[str, Any] = {}

    def partial_pipeline(_news: NewsData | None, _rss: RSSData | None, _config: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        metadata.update(kwargs["run_metadata"])
        return {"runId": "partial-run", "insertedCount": 0, "updatedCount": 0}

    result = sync_exported_snapshots([snapshot], cast(IngestionAPI, object()), cursor_path, pipeline=partial_pipeline)
    assert result["failedCount"] == 1
    assert metadata["sqliteSync"]["failureCount"] == 1
    assert json.loads(cursor_path.read_text())["news:2026-10-22"]["recordId"] == 9
