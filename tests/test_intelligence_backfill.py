import os
import subprocess
import sys
from datetime import date

import pytest

from trendradar.intelligence import backfill
from trendradar.intelligence.pipeline import run_intelligence_pipeline
from trendradar.storage.base import NewsData, NewsItem
from trendradar.storage.local import LocalStorageBackend


class FakeStorage:
    def __init__(self, dates_with_data: set[str]):
        self.dates_with_data = dates_with_data
        self.requested_dates: list[str] = []

    def get_today_all_data(self, requested_date: str):
        self.requested_dates.append(requested_date)
        if requested_date not in self.dates_with_data:
            return None
        return NewsData(requested_date, "10:00", {}, {})

    def get_rss_data(self, requested_date: str):
        return None


def test_backfill_replays_selected_dates_without_analysis_by_default(monkeypatch):
    storage = FakeStorage({"2026-10-20", "2026-10-19"})
    calls: list[str] = []

    def fake_pipeline(news_data, rss_data, config):
        calls.append(news_data.date)
        assert os.environ["INTELLIGENCE_ENABLED"] == "true"
        assert os.environ["INTELLIGENCE_ANALYSIS_ENABLED"] == "false"
        return {"articleCount": 4, "insertedCount": 2, "updatedCount": 2}

    monkeypatch.setattr(backfill, "run_intelligence_pipeline", fake_pipeline)
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "false")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "true")

    result = backfill.backfill_sqlite_days(
        storage, {}, 3, today=date(2026, 10, 20)
    )

    assert storage.requested_dates == ["2026-10-20", "2026-10-19", "2026-10-18"]
    assert calls == ["2026-10-20", "2026-10-19"]
    assert result == {
        "requestedDays": 3, "importedDays": 2, "skippedDays": 1,
        "articleCount": 8, "insertedCount": 4, "updatedCount": 4,
        "failedDates": [],
    }
    assert os.environ["INTELLIGENCE_ENABLED"] == "false"
    assert os.environ["INTELLIGENCE_ANALYSIS_ENABLED"] == "true"


def test_backfill_rejects_unbounded_or_empty_date_ranges():
    with pytest.raises(ValueError, match="between 1 and 90"):
        backfill.backfill_sqlite_days(FakeStorage(set()), {}, 0, today=date(2026, 10, 20))


def test_importing_backfill_does_not_initialize_litellm():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import trendradar.intelligence.backfill; assert 'litellm' not in sys.modules",
        ],
        check=True,
        timeout=15,
        capture_output=True,
        text=True,
    )


def test_backfill_reads_real_sqlite_records_and_imports_without_analysis(monkeypatch, tmp_path):
    storage = LocalStorageBackend(
        data_dir=str(tmp_path), enable_txt=False, enable_html=False, timezone="Asia/Shanghai"
    )
    data = NewsData(
        date="2026-10-20", crawl_time="10:00",
        items={"source": [NewsItem(
            title="Retained SQLite news", source_id="source", url="https://example.com/story",
            first_time="2026-10-19 09:30:00",
        )]},
        id_to_name={"source": "Source"},
    )
    assert storage.save_news_data(data)

    class CaptureAPI:
        def __init__(self):
            self.batches = []

        def import_batch(self, body):
            self.batches.append(body)
            return {"articleCount": len(body["articles"]), "insertedCount": len(body["articles"]), "updatedCount": 0}

        def analysis_candidates(self, body):
            return []

    api = CaptureAPI()
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "true")
    monkeypatch.setattr(
        backfill,
        "run_intelligence_pipeline",
        lambda news, rss, config: run_intelligence_pipeline(news, rss, config, api=api),
    )
    try:
        summary = backfill.backfill_sqlite_days(
            storage, {"TIMEZONE": "Asia/Shanghai"}, 1, today=date(2026, 10, 20)
        )
    finally:
        storage.cleanup()

    assert summary["importedDays"] == 1
    assert api.batches[0]["articles"][0]["title"] == "Retained SQLite news"
    assert api.batches[0]["articles"][0]["firstSeenAt"] == "2026-10-20T02:00:00Z"
    assert api.batches[0]["articles"][0]["analysisStatus"] == "not_requested"
