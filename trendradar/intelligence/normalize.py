from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from trendradar.intelligence.classification import classify_article
from trendradar.storage.base import NewsData, RSSData

_TRACKING_KEYS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "spm"}


def normalize_url(url: str) -> str | None:
    value = url.strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        hostname_value = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname_value:
        return None
    scheme = parsed.scheme.lower()
    hostname = hostname_value.lower()
    netloc = hostname if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f"{hostname}:{port}"
    query = [
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(sorted(query)), ""))


def _normalized_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _identity(source_id: str, title: str, normalized_url: str | None, guid: str = "") -> tuple[str, str]:
    if normalized_url:
        identity = f"url:{normalized_url}"
    elif guid.strip():
        identity = f"rss-guid:{source_id}:{guid.strip()}"
    else:
        identity = f"title:{source_id}:{_normalized_title(title)}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return str(uuid5(NAMESPACE_URL, f"trendradar:{digest}")), f"sha256:{digest}"


def _iso_datetime(value: str | None, timezone_name: str) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None:
            from zoneinfo import ZoneInfo
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, KeyError):
        return None


def _first_seen_at(value: str, crawl_date: str, crawl_time: str, timezone_name: str) -> str | None:
    parsed = _iso_datetime(value, timezone_name)
    if parsed:
        return parsed
    for candidate_time in (value, crawl_time):
        if candidate_time and crawl_date:
            parsed = _iso_datetime(f"{crawl_date} {candidate_time.strip()}", timezone_name)
            if parsed:
                return parsed
    return _iso_datetime(crawl_time, timezone_name)


def build_batch(news_data: NewsData | None, rss_data: RSSData | None, timezone_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources: dict[str, dict[str, str]] = {}
    articles: dict[str, dict[str, Any]] = {}
    seen_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    if news_data:
        for source, news_entries in news_data.items.items():
            source_id = f"hotlist:{source}"
            sources[source_id] = {"id": source_id, "name": news_data.id_to_name.get(source, source), "type": "hotlist"}
            for news_item in news_entries:
                normalized_url = normalize_url(news_item.url)
                article_id, dedupe_key = _identity(source_id, news_item.title, normalized_url)
                first_seen_at = _first_seen_at(
                    news_item.first_time, news_data.date, news_data.crawl_time, timezone_name
                ) or seen_at
                articles[dedupe_key] = {
                    "id": article_id,
                    "sourceId": source_id,
                    "title": news_item.title.strip(),
                    "url": news_item.url or None,
                    "normalizedUrl": normalized_url,
                    "dedupeKey": dedupe_key,
                    "firstSeenAt": first_seen_at,
                    "contentStatus": "title_only",
                    "analysisStatus": "pending",
                    "categories": classify_article(news_item.title),
                }

    if rss_data:
        for feed_id, rss_entries in rss_data.items.items():
            source_id = f"rss:{feed_id}"
            sources[source_id] = {"id": source_id, "name": rss_data.id_to_name.get(feed_id, feed_id), "type": "rss"}
            for rss_item in rss_entries:
                normalized_url = normalize_url(rss_item.url)
                article_id, dedupe_key = _identity(source_id, rss_item.title, normalized_url, rss_item.guid)
                summary = (rss_item.summary or "").strip()[:1000]
                first_seen_at = _first_seen_at(
                    rss_item.first_time, rss_data.date, rss_data.crawl_time, timezone_name
                ) or seen_at
                articles[dedupe_key] = {
                    "id": article_id,
                    "sourceId": source_id,
                    "title": rss_item.title.strip(),
                    "url": rss_item.url or None,
                    "normalizedUrl": normalized_url,
                    "dedupeKey": dedupe_key,
                    "summary": summary or None,
                    "publishedAt": _iso_datetime(rss_item.published_at, timezone_name),
                    "firstSeenAt": first_seen_at,
                    "contentStatus": "summary_available" if summary else "title_only",
                    "analysisStatus": "pending",
                    "categories": classify_article(rss_item.title, summary),
                }

    return list(sources.values()), list(articles.values())
