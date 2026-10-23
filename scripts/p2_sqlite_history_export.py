"""Read-only per-record TrendRadar SQLite exporter.

The script is streamed to the original TrendRadar container with ``python -``.
It never creates files, changes SQLite, or installs dependencies in that
container. News record membership is reconstructed from rank_history by the
unique crawl_time value. RSS has no item-to-record relation, so it exports the
items whose stored last_crawl_time equals a record time as an observation
delta; this is not claimed to be an exact RSS snapshot membership list.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

OUTPUT = Path("/app/output")
MAX_RECORDS_PER_DATABASE = 5000
MAX_TOTAL_DISCOVERY_RECORDS = 100_000
MAX_ITEMS_PER_RECORD = 2000
MAX_TEXT_CHARS = 10000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def connect(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=0.5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=500")
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def clip(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return str(value or "")[:limit]


def day_from_path(path: Path) -> str | None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
        return None
    try:
        return date.fromisoformat(path.stem).isoformat()
    except ValueError:
        return None


def files_for(source: str) -> list[tuple[str, Path]]:
    directory = OUTPUT / ("news" if source == "news" else "rss")
    dates_filter = {part.strip() for part in os.environ.get("P2_HISTORY_DATES", "").split(",") if part.strip()}
    result: list[tuple[str, Path]] = []
    for path in directory.glob("*.db"):
        day = day_from_path(path)
        if day and (not dates_filter or day in dates_filter):
            result.append((day, path))
    return sorted(result)


def discover() -> dict[str, Any]:
    databases: list[dict[str, Any]] = []
    for source, table in (("news", "crawl_records"), ("rss", "rss_crawl_records")):
        for day, path in files_for(source):
            db = connect(path)
            try:
                rows = db.execute(
                    f"SELECT id,crawl_time,created_at,total_items FROM {table} ORDER BY id"
                ).fetchall()
                if len(rows) > MAX_RECORDS_PER_DATABASE:
                    raise RuntimeError("crawl record count exceeds safety limit")
                records = [
                    {"recordId": int(row["id"]), "crawlTime": row["crawl_time"],
                     "createdAt": row["created_at"], "totalItems": int(row["total_items"] or 0)}
                    for row in rows
                ]
                unmatched_observations = 0
                if source == "rss":
                    unmatched_observations = int(db.execute(
                        """SELECT count(*) FROM rss_items i
                           WHERE NOT EXISTS (
                             SELECT 1 FROM rss_crawl_records r WHERE r.crawl_time=i.last_crawl_time
                           )"""
                    ).fetchone()[0])
                if sum(len(item["records"]) for item in databases) + len(records) > MAX_TOTAL_DISCOVERY_RECORDS:
                    raise RuntimeError("total crawl record count exceeds safety limit")
                databases.append({
                    "source": source,
                    "date": day,
                    "minRecordId": records[0]["recordId"] if records else None,
                    "upperRecordId": records[-1]["recordId"] if records else 0,
                    "records": records,
                    "unmatchedObservationCount": unmatched_observations,
                })
            finally:
                db.rollback()
                db.close()
    return {"type": "history_discovery", "version": 1, "databases": databases}


def emit(value: dict[str, Any], counter: list[int]) -> None:
    line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    counter[0] += len(line.encode("utf-8"))
    if counter[0] > MAX_OUTPUT_BYTES:
        raise RuntimeError("record export exceeds size safety limit")
    sys.stdout.write(line)


def export_record(source: str, day: str, record_id: int) -> None:
    if source not in {"news", "rss"} or day_from_path(Path(day + ".db")) != day or record_id < 1:
        raise ValueError("invalid record request")
    path = OUTPUT / ("news" if source == "news" else "rss") / f"{day}.db"
    if not path.is_file():
        raise FileNotFoundError
    db = connect(path)
    try:
        table = "crawl_records" if source == "news" else "rss_crawl_records"
        record = db.execute(
            f"SELECT id,crawl_time,created_at,total_items FROM {table} WHERE id=?", (record_id,)
        ).fetchone()
        if record is None:
            raise LookupError("capture record no longer exists")
        if source == "news":
            orphan_rows = db.execute(
                """SELECT count(*) FROM rank_history rh
                   LEFT JOIN news_items n ON n.id=rh.news_item_id
                   WHERE rh.crawl_time=? AND rh.rank>0 AND n.id IS NULL""",
                (record["crawl_time"],),
            ).fetchone()[0]
            if orphan_rows:
                raise RuntimeError("record contains unlinked positive rank rows")
            items = db.execute(
                """SELECT n.title,n.platform_id,COALESCE(p.name,n.platform_id) AS source_name,
                          rh.rank AS capture_rank,n.url,n.first_crawl_time,n.last_crawl_time,n.crawl_count
                   FROM rank_history rh JOIN news_items n ON n.id=rh.news_item_id
                   LEFT JOIN platforms p ON p.id=n.platform_id
                   WHERE rh.crawl_time=? AND rh.rank>0
                   ORDER BY n.platform_id,n.id""",
                (record["crawl_time"],),
            ).fetchall()
            status_rows = db.execute(
                "SELECT platform_id,status FROM crawl_source_status WHERE crawl_record_id=? ORDER BY platform_id",
                (record_id,),
            ).fetchall()
            status_key = "platform_id"
        else:
            items = db.execute(
                """SELECT i.title,i.feed_id,COALESCE(f.name,i.feed_id) AS source_name,
                          i.url,i.guid,i.published_at,i.summary,i.author,
                          i.first_crawl_time,i.last_crawl_time,i.crawl_count
                   FROM rss_items i LEFT JOIN rss_feeds f ON f.id=i.feed_id
                   WHERE i.last_crawl_time=? ORDER BY i.feed_id,i.id""",
                (record["crawl_time"],),
            ).fetchall()
            status_rows = db.execute(
                "SELECT feed_id,status FROM rss_crawl_status WHERE crawl_record_id=? ORDER BY feed_id",
                (record_id,),
            ).fetchall()
            status_key = "feed_id"
        if len(items) > MAX_ITEMS_PER_RECORD:
            raise RuntimeError("record item count exceeds safety limit")
        failed_ids = [row[status_key] for row in status_rows if row["status"] == "failed"]
        header = {
            "type": "stream", "version": 1,
        }
        counter = [0]
        emit(header, counter)
        emit({
            "type": "snapshot", "source": source, "date": day,
            "recordId": int(record["id"]), "crawlTime": record["crawl_time"],
            "createdAt": record["created_at"], "totalItems": int(record["total_items"] or 0),
            "failedIds": failed_ids, "itemCount": len(items),
            "membershipMode": "rank_history_exact_time" if source == "news" else "rss_last_observation_delta",
        }, counter)
        for row in items:
            if source == "news":
                emit({
                    "type": "item", "source": source, "sourceId": row["platform_id"],
                    "sourceName": clip(row["source_name"], 1000), "title": clip(row["title"], 4000),
                    "rank": int(row["capture_rank"]), "url": clip(row["url"], 4000),
                    "crawlTime": clip(record["crawl_time"], 80),
                    "firstTime": clip(row["first_crawl_time"], 80),
                    "lastTime": clip(row["last_crawl_time"], 80), "count": int(row["crawl_count"] or 1),
                }, counter)
            else:
                emit({
                    "type": "item", "source": source, "sourceId": row["feed_id"],
                    "sourceName": clip(row["source_name"], 1000), "title": clip(row["title"], 4000),
                    "url": clip(row["url"], 4000), "guid": clip(row["guid"], 4000),
                    "publishedAt": clip(row["published_at"], 200),
                    "summary": clip(row["summary"], 4000), "author": clip(row["author"], 1000),
                    "crawlTime": clip(record["crawl_time"], 80),
                    "firstTime": clip(row["first_crawl_time"], 80),
                    "lastTime": clip(row["last_crawl_time"], 80), "count": int(row["crawl_count"] or 1),
                }, counter)
        emit({"type": "end", "source": source, "date": day, "itemCount": len(items)}, counter)
        emit({"type": "stream_end", "version": 1}, counter)
    finally:
        db.rollback()
        db.close()


def main() -> None:
    mode = os.environ.get("P2_HISTORY_EXPORT_MODE", "")
    if mode == "discover":
        output = json.dumps(discover(), ensure_ascii=False, separators=(",", ":"))
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise RuntimeError("history discovery exceeds output size safety limit")
        print(output)
        return
    if mode == "record":
        export_record(
            os.environ.get("P2_HISTORY_SOURCE", ""),
            os.environ.get("P2_HISTORY_DATE", ""),
            int(os.environ.get("P2_HISTORY_RECORD_ID", "0")),
        )
        return
    raise ValueError("invalid export mode")


try:
    main()
except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, LookupError) as exc:
    # Error bodies intentionally omit paths, SQL values, article text and config.
    print(json.dumps({"errorType": type(exc).__name__}), file=sys.stderr)
    raise SystemExit(2)
