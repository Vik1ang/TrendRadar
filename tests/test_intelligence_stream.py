from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from trendradar.intelligence.api import IngestionAPI
from trendradar.intelligence.stream import parse_export_jsonl
from trendradar.intelligence.sync import sync_exported_snapshots


def _stream(failed: list[str] | None = None) -> list[str]:
    values: list[dict[str, Any]] = [
        {"type": "stream", "version": 1},
        {"type": "snapshot", "source": "news", "date": "2026-10-22", "recordId": 29,
         "crawlTime": "14-02", "createdAt": "2026-10-22 14:02:40", "itemCount": 1,
         "failedIds": failed or []},
        {"type": "item", "source": "news", "sourceId": "baidu", "sourceName": "百度热搜",
         "title": "示例标题", "url": "https://example.test/story", "crawlTime": "14-02",
         "firstTime": "14-02", "count": 1, "rank": 1},
        {"type": "end", "source": "news", "date": "2026-10-22", "itemCount": 1},
        {"type": "stream_end", "version": 1},
    ]
    return [json.dumps(value, ensure_ascii=False) for value in values]


def test_jsonl_export_parses_bounded_news_snapshot() -> None:
    snapshots = parse_export_jsonl(_stream())
    assert len(snapshots) == 1
    assert snapshots[0].marker["recordId"] == 29
    assert snapshots[0].data.items["baidu"][0].title == "示例标题"  # type: ignore[union-attr]


def test_jsonl_export_rejects_truncated_stream() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        parse_export_jsonl(_stream()[:-1])


def test_sync_export_advances_cursor_after_success_and_is_idempotent(tmp_path: Path) -> None:
    snapshot = parse_export_jsonl(_stream())[0]
    cursor_path = tmp_path / "cursor.json"
    calls: list[Any] = []

    def pipeline(news: Any, rss: Any, _config: Any, **_kwargs: Any) -> dict[str, int]:
        calls.append((news, rss))
        return {"insertedCount": 1, "updatedCount": 0}

    result = sync_exported_snapshots([snapshot], cast(IngestionAPI, object()), cursor_path, pipeline=pipeline)
    assert result["insertedCount"] == 1
    assert json.loads(cursor_path.read_text())["news:2026-10-22"] == snapshot.marker
    assert sync_exported_snapshots([snapshot], cast(IngestionAPI, object()), cursor_path, pipeline=pipeline)["runCount"] == 0
    assert len(calls) == 1
    assert cursor_path.stat().st_mode & 0o777 == 0o600


def test_sync_export_records_partial_source_failure_and_advances_committed_snapshot(tmp_path: Path) -> None:
    snapshot = parse_export_jsonl(_stream(failed=["baidu"]))[0]
    cursor_path = tmp_path / "cursor.json"
    result = sync_exported_snapshots(
        [snapshot], cast(IngestionAPI, object()), cursor_path,
        pipeline=lambda *_args, **_kwargs: {"insertedCount": 1},
    )
    assert result["runCount"] == 1
    assert result["failedCount"] == 1
    assert json.loads(cursor_path.read_text())["news:2026-10-22"] == snapshot.marker


def test_sync_export_does_not_advance_cursor_when_import_fails(tmp_path: Path) -> None:
    snapshot = parse_export_jsonl(_stream())[0]
    cursor_path = tmp_path / "cursor.json"
    with pytest.raises(RuntimeError, match="database unavailable"):
        sync_exported_snapshots(
            [snapshot], cast(IngestionAPI, object()), cursor_path,
            pipeline=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")),
        )
    assert not cursor_path.exists()
