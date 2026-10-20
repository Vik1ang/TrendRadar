from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from trendradar.core.loader import load_config
from trendradar.intelligence.api import IntelligenceAPIError
from trendradar.intelligence.pipeline import run_intelligence_pipeline
from trendradar.storage.base import NewsData, RSSData
from trendradar.storage.manager import get_storage_manager_from_config

MAX_BACKFILL_DAYS = 90


class SQLiteNewsStorage(Protocol):
    def get_today_all_data(self, date: str) -> NewsData | None: ...

    def get_rss_data(self, date: str) -> RSSData | None: ...


def backfill_sqlite_days(
    storage: SQLiteNewsStorage,
    config: dict[str, Any],
    days: int,
    *,
    analyze: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    if not 1 <= days <= MAX_BACKFILL_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_BACKFILL_DAYS}")

    timezone_name = str(config.get("TIMEZONE", "Asia/Shanghai"))
    current_date = today or datetime.now(ZoneInfo(timezone_name)).date()
    previous_values = {
        key: os.environ.get(key)
        for key in ("INTELLIGENCE_ENABLED", "INTELLIGENCE_ANALYSIS_ENABLED")
    }
    os.environ["INTELLIGENCE_ENABLED"] = "true"
    os.environ["INTELLIGENCE_ANALYSIS_ENABLED"] = "true" if analyze else "false"

    result: dict[str, Any] = {
        "requestedDays": days,
        "importedDays": 0,
        "skippedDays": 0,
        "articleCount": 0,
        "insertedCount": 0,
        "updatedCount": 0,
        "failedDates": [],
    }
    try:
        for offset in range(days):
            day = (current_date - timedelta(days=offset)).isoformat()
            try:
                news_data = storage.get_today_all_data(day)
                rss_data = storage.get_rss_data(day)
                if news_data is None and rss_data is None:
                    result["skippedDays"] += 1
                    continue

                imported = run_intelligence_pipeline(news_data, rss_data, config)
                if imported is None:
                    result["skippedDays"] += 1
                    continue
                result["importedDays"] += 1
                for key in ("articleCount", "insertedCount", "updatedCount"):
                    result[key] += int(imported.get(key, 0))
            except IntelligenceAPIError as exc:
                result["failedDates"].append({
                    "date": day,
                    "errorType": type(exc).__name__,
                    "error": str(exc)[:160],
                })
                break
            except (OSError, ValueError, sqlite3.Error) as exc:
                result["failedDates"].append({"date": day, "errorType": type(exc).__name__})
    finally:
        for key, value in previous_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-import retained TrendRadar SQLite news into the Intelligence PostgreSQL API.")
    parser.add_argument("--days", type=int, required=True, help=f"Number of most recent SQLite dates to replay (1-{MAX_BACKFILL_DAYS})")
    parser.add_argument("--analyze", action="store_true", help="Run missing article analyses; may use existing model quota or incur cost")
    args = parser.parse_args()

    config = load_config()
    storage = get_storage_manager_from_config(config)
    timezone_name = str(config.get("TIMEZONE", "Asia/Shanghai"))
    today = datetime.now(ZoneInfo(timezone_name)).date()
    try:
        summary = backfill_sqlite_days(storage, config, args.days, analyze=args.analyze, today=today)
    finally:
        storage.cleanup()
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["failedDates"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
