"""Resumable, bounded catch-up over immutable SQLite crawl-record upper bounds."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from trendradar.intelligence.api import IngestionAPI
from trendradar.intelligence.normalize import build_batch
from trendradar.intelligence.stream import ExportedSnapshot
from trendradar.storage.base import NewsData, RSSData

STATE_VERSION = 1
PAGE_SIZE = 20
MAX_RECORD_ARTICLES = 2000


class HistoryDiscoveryError(RuntimeError):
    """The available SQLite history cannot safely satisfy persisted progress."""


class SnapshotExporter(Protocol):
    def __call__(self, source: str, day: str, record_id: int) -> ExportedSnapshot: ...


def _write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        os.chmod(temporary, 0o600)
        json.dump(value, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": STATE_VERSION, "cycleId": None, "upper": None, "cursors": {}, "records": {}, "pending": None}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("history state is unreadable; refusing to replace it") from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != STATE_VERSION
        or not isinstance(state.get("cursors"), dict)
        or not isinstance(state.get("records"), dict)
        or (state.get("upper") is not None and not isinstance(state.get("upper"), dict))
    ):
        raise RuntimeError("history state is invalid; refusing to replace it")
    for field in ("cycleArticleLimit", "cycleArticleCount"):
        value = state.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise RuntimeError("history cycle article limit state is invalid")
    if ("cycleArticleLimit" in state) != ("cycleArticleCount" in state):
        raise RuntimeError("history cycle article limit state is incomplete")
    if "cycleArticleLimit" in state and (
        state["cycleArticleLimit"] < 1
        or state["cycleArticleLimit"] > 100_000
        or state["cycleArticleCount"] > state["cycleArticleLimit"]
    ):
        raise RuntimeError("history cycle article limit state is inconsistent")
    pending = state.get("pending")
    if pending is not None and (
        not isinstance(pending, dict)
        or not isinstance(pending.get("articles"), list)
        or not isinstance(pending.get("sources"), dict)
        or not isinstance(pending.get("nextIndex"), int)
        or not 0 <= pending["nextIndex"] <= len(pending["articles"])
    ):
        raise RuntimeError("pending history record is invalid; refusing to replace it")
    return state


def _db_key(item: Mapping[str, Any]) -> str:
    return f"{item['source']}:{item['date']}"


def _record_key(source: str, day: str, record_id: int) -> str:
    return f"{source}:{day}:{record_id}"


def _validate_inventory(databases: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for database in databases:
        source, day = database.get("source"), database.get("date")
        if source not in {"news", "rss"} or not isinstance(day, str):
            raise HistoryDiscoveryError("history discovery contains invalid database identity")
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError as exc:
            raise HistoryDiscoveryError("history discovery contains invalid database date") from exc
        key = _db_key(database)
        if key in result:
            raise HistoryDiscoveryError("history discovery repeats a source/date database")
        unmatched = database.get("unmatchedObservationCount", 0)
        if not isinstance(unmatched, int) or unmatched < 0:
            raise HistoryDiscoveryError("history discovery observation gap count is invalid")
        if source == "rss" and unmatched:
            raise HistoryDiscoveryError(f"RSS database {day} has {unmatched} unmatched last-observation items")
        records = database.get("records")
        if not isinstance(records, list):
            raise HistoryDiscoveryError("history discovery record list is invalid")
        previous = 0
        for record in records:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("recordId"), int)
                or not isinstance(record.get("crawlTime"), str)
                or not record["crawlTime"]
                or (record.get("createdAt") is not None and not isinstance(record.get("createdAt"), str))
                or not isinstance(record.get("totalItems"), int)
                or record["totalItems"] < 0
            ):
                raise HistoryDiscoveryError("history discovery record ID is invalid")
            if record["recordId"] <= previous:
                raise HistoryDiscoveryError("history record IDs are not strictly ordered")
            previous = record["recordId"]
        upper = database.get("upperRecordId")
        if not isinstance(upper, int) or upper != (records[-1]["recordId"] if records else 0):
            raise HistoryDiscoveryError("history upper bound is invalid")
        result[key] = database
    return result


def _freeze_cycle(
    state: dict[str, Any], inventory: Mapping[str, Mapping[str, Any]], state_path: Path,
    max_total_articles: int | None,
) -> bool:
    cursors: dict[str, Any] = state["cursors"]
    for key, marker in cursors.items():
        database = inventory.get(key)
        if database is None:
            raise HistoryDiscoveryError(f"SQLite history database disappeared for {key}")
        record_ids = {item["recordId"] for item in database["records"]}
        cursor_id = marker.get("recordId") if isinstance(marker, dict) else None
        if not isinstance(cursor_id, int) or cursor_id not in record_ids:
            raise HistoryDiscoveryError(f"persisted history cursor anchor is missing for {key}")

    upper = {}
    for key, database in inventory.items():
        cursor_id = int(cursors.get(key, {}).get("recordId", 0))
        upper[key] = {
            "upperRecordId": database["upperRecordId"],
            "records": [record for record in database["records"] if record["recordId"] > cursor_id],
        }
    if state["upper"] is not None:
        stored_limit = state.get("cycleArticleLimit")
        if max_total_articles is not None:
            if not isinstance(stored_limit, int):
                raise HistoryDiscoveryError("active history cycle has no persisted total article limit")
            if max_total_articles < stored_limit:
                raise HistoryDiscoveryError("history total article limit cannot be reduced mid-cycle")
        frozen = state["upper"]
        if not isinstance(frozen, dict) or not set(frozen).issubset(upper):
            raise HistoryDiscoveryError("a frozen SQLite history database disappeared during catch-up")
        for key, frozen_db in frozen.items():
            live = inventory[key]
            if live["upperRecordId"] < frozen_db.get("upperRecordId", 0):
                raise HistoryDiscoveryError(f"SQLite history upper bound regressed for {key}")
            available = {item["recordId"] for item in live["records"]}
            required = {
                item["recordId"] for item in frozen_db.get("records", [])
                if item["recordId"] > state["cursors"].get(key, {}).get("recordId", 0)
            }
            if not required.issubset(available):
                raise HistoryDiscoveryError(f"unprocessed SQLite crawl records disappeared for {key}")
        if max_total_articles is not None:
            assert isinstance(stored_limit, int)
            if max_total_articles > stored_limit:
                state["cycleArticleLimit"] = max_total_articles
                _write_state(state_path, state)
        return False

    state["cycleId"] = f"{datetime.now(UTC).isoformat()}"
    state["upper"] = upper
    if max_total_articles is not None:
        state["cycleArticleLimit"] = max_total_articles
        state["cycleArticleCount"] = 0
    _write_state(state_path, state)
    return True


def _next_record(state: Mapping[str, Any]) -> dict[str, Any] | None:
    upper: Mapping[str, Mapping[str, Any]] = state.get("upper") or {}
    cursors: Mapping[str, Mapping[str, Any]] = state["cursors"]
    candidates: list[dict[str, Any]] = []
    for key, database in upper.items():
        cursor_id = int(cursors.get(key, {}).get("recordId", 0))
        source, day = key.split(":", 1)
        for record in database.get("records", []):
            if cursor_id < record["recordId"] <= database["upperRecordId"]:
                candidates.append({"source": source, "date": day, **record,
                                   "upperRecordId": database["upperRecordId"]})
    return min(candidates, key=lambda item: (item["date"], item.get("createdAt") or "", item["source"], item["recordId"])) if candidates else None


def _pending_for(snapshot: ExportedSnapshot, cycle_id: str) -> dict[str, Any]:
    source, day = snapshot.source, snapshot.date
    if snapshot.marker.get("recordId") is None:
        raise RuntimeError("exported capture record has no stable record ID")
    news = snapshot.data if isinstance(snapshot.data, NewsData) else None
    rss = snapshot.data if isinstance(snapshot.data, RSSData) else None
    sources, articles = build_batch(news, rss, "Asia/Shanghai")
    for article in articles:
        article["analysisStatus"] = "not_requested"
    if len(articles) > MAX_RECORD_ARTICLES:
        raise RuntimeError("normalized SQLite record exceeds safety limit")
    articles = sorted(articles, key=lambda item: item["dedupeKey"])
    return {
        "source": source,
        "date": day,
        "marker": dict(snapshot.marker),
        "recordId": int(snapshot.marker["recordId"]),
        "failedIds": list(snapshot.data.failed_ids),
        "itemCount": snapshot.item_count,
        "membershipMode": "rank_history_exact_time" if source == "news" else "rss_last_observation_delta",
        "sources": {source_item["id"]: source_item for source_item in sources},
        "articles": articles,
        "nextIndex": 0,
        "insertedCount": 0,
        "updatedCount": 0,
        "committedArticleCount": 0,
        "cycleId": cycle_id,
    }


def _finish_record(state: dict[str, Any], pending: dict[str, Any]) -> None:
    source, day, record_id = pending["source"], pending["date"], pending["recordId"]
    db_key = f"{source}:{day}"
    record_key = _record_key(source, day, record_id)
    failed_ids = pending["failedIds"]
    status = "partial" if failed_ids else ("empty" if not pending["articles"] else "completed")
    state["records"][record_key] = {
        "status": status,
        "recordId": record_id,
        "crawlTime": pending["marker"].get("crawlTime"),
        "createdAt": pending["marker"].get("createdAt"),
        "failedSourceIds": failed_ids,
        "membershipMode": pending["membershipMode"],
        "articleCount": len(pending["articles"]),
        "insertedCount": pending["insertedCount"],
        "updatedCount": pending["updatedCount"],
        "finishedAt": datetime.now(UTC).isoformat(),
    }
    state["cursors"][db_key] = {
        "recordId": record_id,
        "crawlTime": pending["marker"].get("crawlTime"),
        "createdAt": pending["marker"].get("createdAt"),
    }
    state["pending"] = None


def sync_history_records(
    databases: Sequence[Mapping[str, Any]],
    api: IngestionAPI,
    state_path: Path,
    exporter: SnapshotExporter,
    *,
    max_articles: int = 1000,
    max_records: int = 100,
    max_seconds: float = 240,
    batch_size: int = PAGE_SIZE,
    max_total_articles: int | None = None,
) -> dict[str, Any]:
    """Catch up ordered capture records to a durable, frozen per-database upper.

    Each record is staged as normalized immutable data before its first write.
    API pages are at most ``batch_size`` and use deterministic run IDs, so a
    lost acknowledgement can be retried without duplicate articles/jobs.
    Source/date cursors move only after every page of that record succeeds.
    """
    if not 1 <= max_articles <= 100_000:
        raise ValueError("max_articles must be between 1 and 100000")
    if max_total_articles is not None and not 1 <= max_total_articles <= 100_000:
        raise ValueError("max_total_articles must be between 1 and 100000")
    if not 1 <= max_records <= 100_000 or not 1 <= batch_size <= PAGE_SIZE:
        raise ValueError("record/page limits are invalid")
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    inventory = _validate_inventory(databases)
    state = _read_state(state_path)
    started = time.monotonic()
    processed_candidates = inserted = updated = completed_records = partial_records = 0
    upper_created = _freeze_cycle(state, inventory, state_path, max_total_articles)
    if not state["upper"]:
        state["upper"] = None
        state["cycleId"] = None
        state.pop("cycleArticleLimit", None)
        state.pop("cycleArticleCount", None)
        _write_state(state_path, state)
        return {"processedCount": 0, "insertedCount": 0, "updatedCount": 0,
                "recordCount": 0, "complete": True, "upperFrozen": upper_created}

    total_limit = state.get("cycleArticleLimit")
    total_processed = int(state.get("cycleArticleCount", 0))
    while (
        processed_candidates < max_articles
        and completed_records < max_records
        and (not isinstance(total_limit, int) or total_processed < total_limit)
    ):
        if time.monotonic() - started >= max_seconds:
            break
        pending = state.get("pending")
        if pending is None:
            record = _next_record(state)
            if record is None:
                state["upper"] = None
                state["cycleId"] = None
                break
            snapshot = exporter(record["source"], record["date"], record["recordId"])
            if (
                snapshot.source != record["source"]
                or snapshot.date != record["date"]
                or snapshot.marker.get("recordId") != record["recordId"]
            ):
                raise RuntimeError("exported record does not match frozen capture identity")
            pending = _pending_for(snapshot, str(state["cycleId"]))
            pending["upperRecordId"] = record["upperRecordId"]
            state["pending"] = pending
            _write_state(state_path, state)

        articles: list[dict[str, Any]] = pending["articles"]
        start = pending["nextIndex"]
        remaining_run = max_articles - processed_candidates
        remaining_total = total_limit - total_processed if isinstance(total_limit, int) else len(articles)
        end = min(start + batch_size, len(articles), start + remaining_run, start + remaining_total)
        if start < len(articles) and end == start:
            break
        batch = articles[start:end]
        article_sources = sorted({item["sourceId"] for item in batch})
        sources = [pending["sources"][source_id] for source_id in article_sources]
        record_id = pending["recordId"]
        source = pending["source"]
        day = pending["date"]
        run_id = str(uuid5(
            NAMESPACE_URL,
            f"trendradar-history:{source}:{day}:{record_id}:{start}:{end}",
        ))
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        batch_index = start // batch_size + 1
        metadata = {
            "sqliteHistory": {
                "algorithmVersion": 1,
                "cycleId": pending["cycleId"],
                "source": source,
                "sourceDatabaseDate": day,
                "recordId": record_id,
                "crawlTime": pending["marker"].get("crawlTime"),
                "createdAt": pending["marker"].get("createdAt"),
                "upperRecordId": pending["upperRecordId"],
                "itemCount": pending["itemCount"],
                "normalizedUnique": len(articles),
                "batchIndex": batch_index,
                "batchSize": len(batch),
                "failedSourceIds": pending["failedIds"],
                "membershipMode": pending["membershipMode"],
            }
        }
        if batch:
            result = api.import_batch({
                "run": {
                    "id": run_id,
                    "startedAt": now,
                    "finishedAt": now,
                    "status": "partial" if pending["failedIds"] else "completed",
                    "sourceCount": len(sources) + len(pending["failedIds"]),
                    "metadata": metadata,
                },
                "sources": sources,
                "articles": batch,
            })
            if not isinstance(result, dict) or not isinstance(result.get("runId"), str):
                raise TypeError("ingestion response is invalid; retaining history progress")
            page_inserted = int(result.get("insertedCount", 0))
            page_updated = int(result.get("updatedCount", 0))
            if page_inserted + page_updated != len(batch):
                raise RuntimeError("ingestion counts do not match submitted history page")
            pending["insertedCount"] += page_inserted
            pending["updatedCount"] += page_updated
            pending["committedArticleCount"] += len(batch)
            pending["nextIndex"] = end
            processed_candidates += len(batch)
            total_processed += len(batch)
            if isinstance(total_limit, int):
                state["cycleArticleCount"] = total_processed
            inserted += page_inserted
            updated += page_updated
        if pending["nextIndex"] == len(articles):
            _finish_record(state, pending)
            completed_records += 1
            if pending["failedIds"]:
                partial_records += 1
            if _next_record(state) is None:
                state["upper"] = None
                state["cycleId"] = None
                state.pop("cycleArticleLimit", None)
                state.pop("cycleArticleCount", None)
            _write_state(state_path, state)
        else:
            state["pending"] = pending
            _write_state(state_path, state)

    active_pending = state.get("pending")
    is_complete = active_pending is None and _next_record(state) is None
    _write_state(state_path, state)
    return {
        "processedCount": processed_candidates,
        "insertedCount": inserted,
        "updatedCount": updated,
        "recordCount": completed_records,
        "partialRecordCount": partial_records,
        "nextRecord": _next_record(state),
        "pendingRecordIndex": active_pending.get("nextIndex") if active_pending else None,
        "pendingRecordTotal": len(active_pending["articles"]) if active_pending else None,
        "cycleUpperFrozen": bool(state.get("upper")),
        "cycleProcessedCount": total_processed,
        "cycleArticleLimit": total_limit,
        "cycleLimitReached": isinstance(total_limit, int) and total_processed >= total_limit,
        "complete": is_complete,
        "upperFrozen": upper_created,
        "elapsedSeconds": round(time.monotonic() - started, 3),
    }


def inventory_summary(databases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    inventory = _validate_inventory(databases)
    return {
        "databaseCount": len(inventory),
        "captureRecordCount": sum(len(database["records"]) for database in inventory.values()),
        "unmatchedRssObservationCount": sum(
            int(database.get("unmatchedObservationCount", 0))
            for database in inventory.values() if database.get("source") == "rss"
        ),
        "upperBounds": {key: database["upperRecordId"] for key, database in sorted(inventory.items())},
    }
