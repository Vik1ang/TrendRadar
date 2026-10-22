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
    sync_exported_snapshots_batched,
    sync_latest_once,
)
from trendradar.storage.base import NewsData, NewsItem, RSSData, RSSItem


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


def test_batched_sync_replays_lost_ack_and_advances_cursor_only_after_all_pages(tmp_path: Path) -> None:
    day1, day2 = "2026-10-21", "2026-10-22"
    news = ExportedSnapshot(
        "news", day1, {"recordId": 7, "crawlTime": "23:32", "createdAt": "created"},
        NewsData(day1, "23:32", {"baidu": [
            NewsItem(title=f"headline {index}", source_id="baidu", url=f"https://example.com/n/{index}")
            for index in range(20)
        ]}),
        20,
    )
    rss = ExportedSnapshot(
        "rss", day2, {"recordId": 3, "crawlTime": "00:02", "createdAt": "created"},
        RSSData(day2, "00:02", {"feed": [
            RSSItem(title=f"feed item {index}", feed_id="feed", url=f"https://example.com/r/{index}")
            for index in range(20)
        ]}),
        20,
    )
    snapshots = [news, rss]
    cursor_path = tmp_path / "state" / "cursors.json"
    pending_path = cursor_path.with_name("pending-sync.json")
    calls: list[dict[str, Any]] = []
    lose_first_ack = True

    class API:
        def import_batch(self, body: dict[str, Any]) -> dict[str, Any]:
            nonlocal lose_first_ack
            calls.append(body)
            result = {"runId": body["run"]["id"], "insertedCount": len(body["articles"]), "updatedCount": 0}
            if lose_first_ack:
                lose_first_ack = False
                raise RuntimeError("simulated lost response after server commit")
            return result

    api = cast(IngestionAPI, API())
    with pytest.raises(RuntimeError, match="lost response"):
        sync_exported_snapshots_batched(snapshots, api, cursor_path, max_articles=20)

    assert len(calls[0]["articles"]) == 20
    assert not cursor_path.exists()
    assert json.loads(pending_path.read_text())["nextIndex"] == 0
    assert pending_path.stat().st_mode & 0o777 == 0o600

    # A committed batch with a lost response is replayed with the same IDs/run key.
    first = sync_exported_snapshots_batched(snapshots, api, cursor_path, max_articles=20)
    assert first["processedCount"] == 20
    assert first["complete"] is False
    assert calls[1]["run"]["id"] == calls[0]["run"]["id"]
    assert [item["id"] for item in calls[1]["articles"]] == [item["id"] for item in calls[0]["articles"]]
    assert not cursor_path.exists()
    assert json.loads(pending_path.read_text())["nextIndex"] == 20

    # Live SQLite can advance while the persisted spool safely finishes first.
    changed_news = ExportedSnapshot(
        "news", day1, {"recordId": 8, "crawlTime": "23:58", "createdAt": "newer"},
        NewsData(day1, "23:58", {"baidu": [NewsItem(title="new arrival", source_id="baidu", url="https://example.com/new")]}),
        1,
    )
    second = sync_exported_snapshots_batched([changed_news, rss], api, cursor_path, max_articles=20)
    assert second["processedCount"] == 20
    assert second["complete"] is True
    assert len(calls[2]["articles"]) == 20
    assert len({item["id"] for item in calls[2]["articles"]}) == 20
    assert {item["analysisStatus"] for item in calls[2]["articles"]} == {"not_requested"}
    assert json.loads(cursor_path.read_text()) == {
        "news:2026-10-21": news.marker,
        "rss:2026-10-22": rss.marker,
    }
    assert not pending_path.exists()

    # Once the old frozen crawl is fully acknowledged, the newer live marker
    # is exported on the next invocation instead of being skipped.
    third = sync_exported_snapshots_batched([changed_news, rss], api, cursor_path, max_articles=20)
    assert third["processedCount"] == 1
    assert calls[3]["articles"][0]["title"] == "new arrival"
    assert json.loads(cursor_path.read_text())["news:2026-10-21"] == changed_news.marker


def test_batched_sync_refuses_corrupt_pending_state_instead_of_overwriting(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursors.json"
    pending_path = cursor_path.with_name("pending-sync.json")
    pending_path.write_text("{invalid", encoding="utf-8")
    snapshot = ExportedSnapshot(
        "news", "2026-10-22", {"recordId": 1, "crawlTime": "10:00", "createdAt": "created"},
        NewsData("2026-10-22", "10:00", {"baidu": [NewsItem(title="headline", source_id="baidu")]}), 1,
    )

    with pytest.raises(RuntimeError, match="unreadable"):
        sync_exported_snapshots_batched([snapshot], cast(IngestionAPI, object()), cursor_path, max_articles=20)
    assert pending_path.read_text() == "{invalid"


def test_batched_sync_never_exceeds_a_smaller_limit_when_resuming(tmp_path: Path) -> None:
    day = "2026-10-22"
    snapshot = ExportedSnapshot(
        "news", day, {"recordId": 11, "crawlTime": "12:00", "createdAt": "created"},
        NewsData(day, "12:00", {"baidu": [
            NewsItem(title=f"story {index}", source_id="baidu", url=f"https://example.com/{index}")
            for index in range(25)
        ]}),
        25,
    )
    calls: list[dict[str, Any]] = []

    class API:
        def import_batch(self, body: dict[str, Any]) -> dict[str, Any]:
            calls.append(body)
            return {"runId": body["run"]["id"]}

    api = cast(IngestionAPI, API())
    cursor_path = tmp_path / "cursors.json"
    first = sync_exported_snapshots_batched([snapshot], api, cursor_path, max_articles=20)
    resumed = sync_exported_snapshots_batched([snapshot], api, cursor_path, max_articles=5)

    assert first["processedCount"] == 20
    assert resumed["processedCount"] == 5
    assert len(calls[0]["articles"]) == 20
    assert len(calls[1]["articles"]) == 5
    assert resumed["complete"] is True
