"""Read-only JSONL exporter executed via ``docker exec`` in TrendRadar.

This script is streamed to the existing container's Python interpreter on stdin;
it does not create files or install/modify anything inside that container.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

OUTPUT = Path("/app/output")
MAX_ITEMS_PER_SNAPSHOT = 2000
MAX_TEXT_CHARS = 10000
MAX_TOTAL_ITEMS = 3000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
output_bytes = 0
output_items = 0


def emit(value: dict[str, Any]) -> None:
    global output_bytes, output_items
    line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    output_bytes += len(line.encode("utf-8"))
    if output_bytes > MAX_OUTPUT_BYTES:
        raise RuntimeError("export exceeds 16 MiB safety limit")
    if value.get("type") == "item":
        output_items += 1
        if output_items > MAX_TOTAL_ITEMS:
            raise RuntimeError("export exceeds 3000 item safety limit")
    sys.stdout.write(line)
    sys.stdout.flush()


def clipped(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return str(value or "")[:limit]


def connection_for(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    connection.execute("PRAGMA busy_timeout=500")
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def dates_to_read() -> list[str]:
    raw = os.environ.get("P2_SYNC_DATES", "")
    if raw:
        values = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        today = date.today()
        values = [(today - timedelta(days=1)).isoformat(), today.isoformat()]
    dates: list[str] = []
    for value in values:
        parsed = date.fromisoformat(value)
        normalized = parsed.isoformat()
        if normalized not in dates:
            dates.append(normalized)
    return dates[-2:]


def load_cursors() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(os.environ.get("P2_SYNC_CURSOR_JSON", "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid sync cursor JSON") from exc
    return value if isinstance(value, dict) else {}


def export_news(day: str, cursor: dict[str, Any] | None) -> None:
    path = OUTPUT / "news" / f"{day}.db"
    if not path.is_file():
        return
    con = connection_for(path)
    try:
        latest = con.execute(
            "SELECT id,crawl_time,created_at,total_items FROM crawl_records ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return
        record_id, crawl_time, created_at, total = latest
        if cursor and int(cursor.get("recordId", -1)) >= int(record_id):
            return
        since = cursor.get("crawlTime") if cursor else None
        if since:
            query = """SELECT n.title,n.platform_id,COALESCE(p.name,n.platform_id),n.rank,n.url,
                       n.first_crawl_time,n.last_crawl_time,n.crawl_count
                       FROM news_items n LEFT JOIN platforms p ON p.id=n.platform_id
                       WHERE n.last_crawl_time >= ? ORDER BY n.platform_id,n.last_crawl_time,n.id"""
            params: tuple[Any, ...] = (since,)
        else:
            query = """SELECT n.title,n.platform_id,COALESCE(p.name,n.platform_id),n.rank,n.url,
                       n.first_crawl_time,n.last_crawl_time,n.crawl_count
                       FROM news_items n LEFT JOIN platforms p ON p.id=n.platform_id
                       WHERE n.last_crawl_time = ? ORDER BY n.platform_id,n.last_crawl_time,n.id"""
            params = (crawl_time,)
        rows = con.execute(query, params).fetchmany(MAX_ITEMS_PER_SNAPSHOT + 1)
        if len(rows) > MAX_ITEMS_PER_SNAPSHOT:
            raise RuntimeError(f"news snapshot exceeds {MAX_ITEMS_PER_SNAPSHOT} item safety limit")
        failed_ids = [r[0] for r in con.execute(
            """SELECT css.platform_id FROM crawl_source_status css
               JOIN crawl_records cr ON cr.id=css.crawl_record_id
               WHERE cr.id=? AND css.status='failed'""", (record_id,)
        )]
        emit({"type": "snapshot", "source": "news", "date": day,
              "recordId": int(record_id), "crawlTime": crawl_time,
              "createdAt": created_at, "totalItems": int(total or 0),
              "failedIds": failed_ids, "itemCount": len(rows)})
        for row in rows:
            title, source_id, source_name, rank, url, first_time, last_time, count = row
            emit({"type": "item", "source": "news", "sourceId": source_id,
                  "sourceName": clipped(source_name, 1000), "title": clipped(title, 4000),
                  "rank": int(rank or 0), "url": clipped(url, 4000),
                  "crawlTime": clipped(last_time, 80), "firstTime": clipped(first_time, 80),
                  "count": int(count or 1)})
        emit({"type": "end", "source": "news", "date": day, "itemCount": len(rows)})
    finally:
        con.rollback()
        con.close()


def export_rss(day: str, cursor: dict[str, Any] | None) -> None:
    path = OUTPUT / "rss" / f"{day}.db"
    if not path.is_file():
        return
    con = connection_for(path)
    try:
        latest = con.execute(
            "SELECT id,crawl_time,created_at,total_items FROM rss_crawl_records ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return
        record_id, crawl_time, created_at, total = latest
        if cursor and int(cursor.get("recordId", -1)) >= int(record_id):
            return
        since = cursor.get("crawlTime") if cursor else None
        condition = "i.last_crawl_time >= ?" if since else "i.last_crawl_time = ?"
        boundary = since or crawl_time
        query = f"""SELECT i.title,i.feed_id,COALESCE(f.name,i.feed_id),i.url,i.guid,
                    i.published_at,i.summary,i.author,i.first_crawl_time,i.last_crawl_time,i.crawl_count
                    FROM rss_items i LEFT JOIN rss_feeds f ON f.id=i.feed_id
                    WHERE {condition} ORDER BY i.feed_id,i.last_crawl_time,i.id"""
        rows = con.execute(query, (boundary,)).fetchmany(MAX_ITEMS_PER_SNAPSHOT + 1)
        if len(rows) > MAX_ITEMS_PER_SNAPSHOT:
            raise RuntimeError(f"rss snapshot exceeds {MAX_ITEMS_PER_SNAPSHOT} item safety limit")
        failed_ids = [r[0] for r in con.execute(
            """SELECT cs.feed_id FROM rss_crawl_status cs
               JOIN rss_crawl_records cr ON cr.id=cs.crawl_record_id
               WHERE cr.id=? AND cs.status='failed'""", (record_id,)
        )]
        emit({"type": "snapshot", "source": "rss", "date": day,
              "recordId": int(record_id), "crawlTime": crawl_time,
              "createdAt": created_at, "totalItems": int(total or 0),
              "failedIds": failed_ids, "itemCount": len(rows)})
        for row in rows:
            title, feed_id, feed_name, url, guid, published, summary, author, first_time, last_time, count = row
            emit({"type": "item", "source": "rss", "sourceId": feed_id,
                  "sourceName": clipped(feed_name, 1000), "title": clipped(title, 4000),
                  "url": clipped(url, 4000), "guid": clipped(guid, 4000),
                  "publishedAt": clipped(published, 200), "summary": clipped(summary, 4000),
                  "author": clipped(author, 1000), "crawlTime": clipped(last_time, 80),
                  "firstTime": clipped(first_time, 80), "count": int(count or 1)})
        emit({"type": "end", "source": "rss", "date": day, "itemCount": len(rows)})
    finally:
        con.rollback()
        con.close()


def main() -> None:
    cursors = load_cursors()
    emit({"type": "stream", "version": 1})
    for day in dates_to_read():
        for source, export in (("news", export_news), ("rss", export_rss)):
            cursor = cursors.get(f"{source}:{day}")
            export(day, cursor if isinstance(cursor, dict) else None)
    emit({"type": "stream_end", "version": 1})


try:
    main()
except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError) as exc:
    # Never print database contents, paths beyond the fixed source, or credentials.
    print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
    raise SystemExit(2)
