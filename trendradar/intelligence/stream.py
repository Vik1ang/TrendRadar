"""Bounded JSONL protocol shared by the host exporter and importer runner."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from trendradar.storage.base import NewsData, NewsItem, RSSData, RSSItem

MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_ITEMS_PER_SNAPSHOT = 2000
MAX_TOTAL_ITEMS = 3000


@dataclass(frozen=True)
class ExportedSnapshot:
    source: str
    date: str
    marker: dict[str, Any]
    data: NewsData | RSSData
    item_count: int


def parse_export_jsonl(lines: Iterable[str]) -> list[ExportedSnapshot]:
    snapshots: list[ExportedSnapshot] = []
    markers: dict[tuple[str, str], dict[str, Any]] = {}
    items: dict[tuple[str, str], dict[str, list[NewsItem | RSSItem]]] = {}
    metadata: dict[tuple[str, str], dict[str, Any]] = {}
    item_counts: dict[tuple[str, str], int] = {}
    total_bytes = 0
    total_items = 0
    saw_header = False
    saw_end = False
    open_key: tuple[str, str] | None = None

    for raw_line in lines:
        encoded = raw_line.encode("utf-8")
        total_bytes += len(encoded)
        if len(encoded) > MAX_LINE_BYTES or total_bytes > MAX_STREAM_BYTES:
            raise ValueError("export stream exceeds size safety limit")
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSONL export record") from exc
        if not isinstance(value, dict):
            raise TypeError("export record must be an object")
        kind = value.get("type")
        if kind == "stream":
            if saw_header or value.get("version") != 1:
                raise ValueError("unsupported or repeated export stream header")
            saw_header = True
            continue
        if not saw_header or saw_end:
            raise ValueError("export record outside stream boundaries")
        if kind == "snapshot":
            if open_key is not None:
                raise ValueError("nested export snapshots are not allowed")
            source, day = value.get("source"), value.get("date")
            if source not in {"news", "rss"} or not isinstance(day, str):
                raise ValueError("invalid snapshot metadata")
            key = (source, day)
            if key in markers:
                raise ValueError("duplicate source/date snapshot")
            expected = value.get("itemCount")
            if not isinstance(expected, int) or not 0 <= expected <= MAX_ITEMS_PER_SNAPSHOT:
                raise ValueError("snapshot item count exceeds safety limit")
            marker = {name: value.get(name) for name in ("recordId", "crawlTime", "createdAt")}
            if not isinstance(marker["recordId"], int) or not isinstance(marker["crawlTime"], str):
                raise ValueError("snapshot marker is incomplete")
            markers[key] = marker
            metadata[key] = {
                "date": day,
                "crawl_time": marker["crawlTime"],
                "id_to_name": {},
                "failed_ids": value.get("failedIds", []),
            }
            items[key] = {}
            item_counts[key] = expected
            open_key = key
            continue
        if kind == "item":
            source = value.get("source")
            if open_key is None or open_key[0] != source:
                raise ValueError("item does not belong to exactly one open snapshot")
            key = open_key
            source_id = value.get("sourceId")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("item source ID is invalid")
            source_name_value = value.get("sourceName")
            source_name: str = source_name_value if isinstance(source_name_value, str) else source_id
            item: NewsItem | RSSItem
            metadata[key]["id_to_name"][source_id] = source_name
            if source == "news":
                item = NewsItem(
                    title=str(value.get("title") or ""), source_id=source_id,
                    source_name=source_name, rank=int(value.get("rank") or 0),
                    url=str(value.get("url") or ""), crawl_time=str(value.get("crawlTime") or ""),
                    first_time=str(value.get("firstTime") or ""), count=int(value.get("count") or 1),
                )
            else:
                item = RSSItem(
                    title=str(value.get("title") or ""), feed_id=source_id,
                    feed_name=source_name, url=str(value.get("url") or ""),
                    guid=str(value.get("guid") or ""), published_at=str(value.get("publishedAt") or ""),
                    summary=str(value.get("summary") or ""), author=str(value.get("author") or ""),
                    crawl_time=str(value.get("crawlTime") or ""),
                    first_time=str(value.get("firstTime") or ""), count=int(value.get("count") or 1),
                )
            items[key].setdefault(source_id, []).append(item)
            total_items += 1
            if total_items > MAX_TOTAL_ITEMS:
                raise ValueError("export exceeds total item safety limit")
            continue
        if kind == "end":
            source, day = value.get("source"), value.get("date")
            if not isinstance(source, str) or not isinstance(day, str):
                raise ValueError("snapshot end metadata is invalid")
            key = (source, day)
            if key not in markers:
                raise ValueError("snapshot end without matching header")
            if key != open_key:
                raise ValueError("snapshot end does not match active snapshot")
            actual = sum(map(len, items[key].values()))
            if actual != item_counts[key] or value.get("itemCount") != actual:
                raise ValueError("snapshot item count mismatch")
            meta = metadata[key]
            if source == "news":
                data: NewsData | RSSData = NewsData(items=cast(dict[str, list[NewsItem]], items[key]), **meta)
            else:
                data = RSSData(items=cast(dict[str, list[RSSItem]], items[key]), **meta)
            snapshots.append(ExportedSnapshot(source, day, markers[key], data, actual))
            open_key = None
            continue
        if kind == "stream_end":
            if value.get("version") != 1 or open_key is not None:
                raise ValueError("unsupported export stream end")
            saw_end = True
            continue
        raise ValueError("unknown export record type")

    if not saw_header or not saw_end or len(snapshots) != len(markers):
        raise ValueError("export stream is incomplete")
    return snapshots
