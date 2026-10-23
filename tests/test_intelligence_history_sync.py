from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from trendradar.intelligence.api import IngestionAPI
from trendradar.intelligence.history_sync import (
    HistoryDiscoveryError,
    sync_history_records,
)
from trendradar.intelligence.stream import ExportedSnapshot
from trendradar.storage.base import NewsData, NewsItem, RSSData, RSSItem


def _news(day: str, record_id: int, crawl_time: str, items: list[NewsItem]) -> ExportedSnapshot:
    return ExportedSnapshot(
        "news", day,
        {"recordId": record_id, "crawlTime": crawl_time, "createdAt": f"{day} {crawl_time}"},
        NewsData(day, crawl_time, {"baidu": items}), len(items),
    )


class IdempotentAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.persisted: dict[str, dict[str, Any]] = {}
        self.lose_ack_once = False

    def import_batch(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(body)
        new = 0
        for article in body["articles"]:
            if article["id"] not in self.persisted:
                new += 1
            self.persisted[article["id"]] = article
        if self.lose_ack_once:
            self.lose_ack_once = False
            raise RuntimeError("response lost after commit")
        return {"runId": body["run"]["id"], "insertedCount": new,
                "updatedCount": len(body["articles"]) - new}


def _inventory(snapshots: list[ExportedSnapshot]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in snapshots:
        grouped.setdefault((item.source, item.date), []).append({
            "recordId": item.marker["recordId"], "crawlTime": item.marker["crawlTime"],
            "createdAt": item.marker["createdAt"], "totalItems": item.item_count,
        })
    return [
        {"source": source, "date": day, "records": sorted(records, key=lambda r: r["recordId"]),
         "upperRecordId": max((r["recordId"] for r in records), default=0)}
        for (source, day), records in grouped.items()
    ]


def test_history_catches_every_record_across_dates_and_keeps_transient_items(tmp_path: Path) -> None:
    transient = NewsItem(title="brief trend", source_id="baidu", url="https://example.test/transient")
    repeated = NewsItem(title="repeated", source_id="baidu", url="https://example.test/repeated")
    snapshots = [
        _news("2026-10-21", 1, "08:00", [transient, repeated]),
        _news("2026-10-21", 2, "08:30", [repeated]),
        _news("2026-10-23", 1, "09:00", [repeated]),
    ]
    by_key = {(s.source, s.date, s.marker["recordId"]): s for s in snapshots}
    api = IdempotentAPI()
    path = tmp_path / "history.json"
    first = sync_history_records(_inventory(snapshots), cast(IngestionAPI, api), path,
                                 lambda source, day, record_id: by_key[source, day, record_id],
                                 max_articles=20)

    assert first["complete"] is True
    assert first["recordCount"] == 3
    assert len(api.persisted) == 2
    assert {item["title"] for item in api.persisted.values()} == {"brief trend", "repeated"}
    assert json.loads(path.read_text())["cursors"]["news:2026-10-21"]["recordId"] == 2
    assert json.loads(path.read_text())["cursors"]["news:2026-10-23"]["recordId"] == 1
    # Reappearance in later captures produces deterministic IDs and no duplicate article.
    assert len({article["id"] for call in api.calls for article in call["articles"]}) == 2


def test_history_resumes_mid_record_and_replays_lost_ack_idempotently(tmp_path: Path) -> None:
    articles = [NewsItem(title=f"article {n}", source_id="baidu", url=f"https://example.test/{n}")
                for n in range(45)]
    snapshot = _news("2026-10-20", 11, "23:58", articles)
    inventory = _inventory([snapshot])
    api = IdempotentAPI()
    api.lose_ack_once = True
    state_path = tmp_path / "history.json"
    exporter = lambda *_: snapshot

    with pytest.raises(RuntimeError, match="response lost"):
        sync_history_records(inventory, cast(IngestionAPI, api), state_path, exporter,
                             max_articles=20, batch_size=20)
    state = json.loads(state_path.read_text())
    assert state["pending"]["nextIndex"] == 0
    assert len(api.persisted) == 20

    resumed = sync_history_records(inventory, cast(IngestionAPI, api), state_path, exporter,
                                   max_articles=20, batch_size=20)
    assert resumed["processedCount"] == 20
    assert len(api.persisted) == 20
    assert api.calls[0]["run"]["id"] == api.calls[1]["run"]["id"]
    second = sync_history_records(inventory, cast(IngestionAPI, api), state_path, exporter,
                                  max_articles=20, batch_size=20)
    assert second["processedCount"] == 20
    assert second["complete"] is False
    assert json.loads(state_path.read_text())["pending"]["nextIndex"] == 40
    last = sync_history_records(inventory, cast(IngestionAPI, api), state_path, exporter,
                                max_articles=20, batch_size=20)
    assert last["processedCount"] == 5
    assert last["complete"] is True
    assert len(api.persisted) == 45
    assert all(call["run"]["metadata"]["sqliteHistory"]["membershipMode"] == "rank_history_exact_time"
               for call in api.calls)


def test_frozen_upper_excludes_newer_capture_until_next_cycle(tmp_path: Path) -> None:
    old = _news("2026-10-19", 1, "10:00", [NewsItem(title="old", source_id="baidu", url="https://e.test/old")])
    new = _news("2026-10-19", 2, "10:30", [NewsItem(title="new", source_id="baidu", url="https://e.test/new")])
    old_inventory = _inventory([old])
    api = IdempotentAPI()
    state_path = tmp_path / "history.json"
    exporter = lambda _source, _day, record_id: {1: old, 2: new}[record_id]
    one = sync_history_records(old_inventory, cast(IngestionAPI, api), state_path, exporter,
                               max_articles=1)
    assert one["complete"] is True
    # Next invocation discovers the appended capture and starts a new frozen cycle.
    two = sync_history_records(_inventory([old, new]), cast(IngestionAPI, api), state_path, exporter,
                               max_articles=10)
    assert two["complete"] is True
    assert len(api.persisted) == 2


def test_total_candidate_limit_persists_across_runs_and_requires_explicit_increase(tmp_path: Path) -> None:
    snapshot = _news("2026-10-21", 1, "08:00", [
        NewsItem(title=f"article {n}", source_id="baidu", url=f"https://e.test/{n}")
        for n in range(2)
    ])
    api = IdempotentAPI()
    api.lose_ack_once = True
    state_path = tmp_path / "history.json"
    exporter = lambda *_: snapshot
    with pytest.raises(RuntimeError, match="response lost"):
        sync_history_records(
            _inventory([snapshot]), cast(IngestionAPI, api), state_path, exporter,
            max_articles=20, max_total_articles=1,
        )
    assert json.loads(state_path.read_text())["cycleArticleCount"] == 0
    first = sync_history_records(
        _inventory([snapshot]), cast(IngestionAPI, api), state_path, exporter,
        max_articles=20, max_total_articles=1,
    )
    assert first["processedCount"] == 1
    assert first["cycleLimitReached"] is True
    assert json.loads(state_path.read_text())["cursors"] == {}

    stopped = sync_history_records(
        _inventory([snapshot]), cast(IngestionAPI, api), state_path, exporter,
        max_articles=20, max_total_articles=1,
    )
    assert stopped["processedCount"] == 0
    assert stopped["complete"] is False
    assert len(api.persisted) == 1

    extended = sync_history_records(
        _inventory([snapshot]), cast(IngestionAPI, api), state_path, exporter,
        max_articles=20, max_total_articles=2,
    )
    assert extended["processedCount"] == 1
    assert extended["complete"] is True
    assert len(api.persisted) == 2
    assert json.loads(state_path.read_text())["cursors"]["news:2026-10-21"]["recordId"] == 1


def test_missing_unprocessed_capture_fails_closed_and_rss_delta_is_explicit(tmp_path: Path) -> None:
    rss = ExportedSnapshot(
        "rss", "2026-10-23", {"recordId": 4, "crawlTime": "11:00", "createdAt": "stamp"},
        RSSData("2026-10-23", "11:00", {"feed": [RSSItem(title="RSS", feed_id="feed", guid="g-1",
                                                                  url="https://e.test/rss")]}), 1,
    )
    api = IdempotentAPI()
    state_path = tmp_path / "history.json"
    result = sync_history_records(_inventory([rss]), cast(IngestionAPI, api), state_path,
                                  lambda *_: rss, max_articles=1)
    assert result["complete"] is True
    assert api.calls[0]["run"]["metadata"]["sqliteHistory"]["membershipMode"] == "rss_last_observation_delta"
    state = json.loads(state_path.read_text())
    assert "_path" not in state
    assert state["records"]["rss:2026-10-23:4"]["membershipMode"] == "rss_last_observation_delta"


def test_source_failures_are_terminally_visible_as_partial_not_silently_successful(tmp_path: Path) -> None:
    partial = ExportedSnapshot(
        "news", "2026-10-22", {"recordId": 5, "crawlTime": "10:00", "createdAt": "stamp"},
        NewsData("2026-10-22", "10:00", {"baidu": []}, failed_ids=["failed-source"]), 0,
    )
    state_path = tmp_path / "history.json"
    result = sync_history_records(_inventory([partial]), cast(IngestionAPI, IdempotentAPI()), state_path,
                                  lambda *_: partial, max_articles=1)
    state = json.loads(state_path.read_text())
    record = state["records"]["news:2026-10-22:5"]
    assert result["partialRecordCount"] == 1
    assert record["status"] == "partial"
    assert record["failedSourceIds"] == ["failed-source"]


def test_unmatched_rss_last_observation_fails_closed(tmp_path: Path) -> None:
    inventory = _inventory([])
    inventory.append({
        "source": "rss", "date": "2026-10-23", "records": [], "upperRecordId": 0,
        "unmatchedObservationCount": 2,
    })
    with pytest.raises(HistoryDiscoveryError, match="unmatched last-observation items"):
        sync_history_records(inventory, cast(IngestionAPI, IdempotentAPI()), tmp_path / "history.json",
                             lambda *_: pytest.fail("unmatched data cannot be exported as completed"))


def test_disappearing_frozen_record_is_not_silently_skipped(tmp_path: Path) -> None:
    first = _news("2026-10-21", 1, "08:00", [NewsItem(title="one", source_id="baidu", url="https://e.test/1")])
    second = _news("2026-10-21", 2, "08:30", [NewsItem(title="two", source_id="baidu", url="https://e.test/2")])
    state_path = tmp_path / "history.json"
    api = IdempotentAPI()
    records = _inventory([first, second])
    result = sync_history_records(records, cast(IngestionAPI, api), state_path,
                                 lambda _s, _d, record_id: {1: first, 2: second}[record_id],
                                 max_articles=1)
    assert result["complete"] is False
    with pytest.raises(HistoryDiscoveryError, match="SQLite history upper bound regressed"):
        sync_history_records(_inventory([first]), cast(IngestionAPI, api), state_path,
                             lambda *_: first, max_articles=10)
