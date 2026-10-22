import copy
import json
from typing import Any

from trendradar.intelligence.pipeline import run_intelligence_pipeline
from trendradar.storage.base import NewsData, NewsItem


class FakeAPI:
    def __init__(self, candidate_mode: bool):
        self.batches: list[dict[str, Any]] = []
        self.candidate_mode = candidate_mode
        self.candidates: list[dict[str, Any]] = []
        self.started: list[dict[str, Any]] = []
        self.start_result = {"started": True}

    def import_batch(self, body):
        self.batches.append(copy.deepcopy(body))
        return {
            "runId": body["run"]["id"],
            "articleCount": len(body["articles"]),
            "insertedCount": len(body["articles"]),
            "updatedCount": 0,
        }

    def analysis_candidates(self, body):
        return self.candidates if self.candidate_mode else []

    def start_analysis(self, body):
        self.started.append(copy.deepcopy(body))
        return self.start_result


class FakeClient:
    model = "test/model"

    def __init__(self):
        self.calls = 0

    def validate_config(self):
        return True, ""

    def chat(self, messages, **kwargs):
        self.calls += 1
        return json.dumps({
            "categories": [{"id": "technology", "confidence": 0.9}],
            "summary": "根据标题总结。", "keyFacts": ["标题中提及产品"],
            "background": "信息有限。", "potentialImpacts": ["可能产生影响"],
            "uncertainties": ["未提供正文"],
        }, ensure_ascii=False)


class InvalidConfigClient(FakeClient):
    def validate_config(self):
        return False, "credentials unavailable"


def test_pipeline_persists_news_without_analysis_when_disabled(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "false")
    api = FakeAPI(candidate_mode=False)
    data = NewsData("2026-10-20", "10:00", {"src": [NewsItem(title="AI news", source_id="src")]}, {"src": "Source"})

    result = run_intelligence_pipeline(data, None, {}, api=api)

    assert result is not None
    assert len(api.batches) == 2
    assert api.batches[0]["articles"][0]["analysisStatus"] == "not_requested"
    assert api.batches[-1]["run"]["status"] == "completed"


def test_pipeline_persists_raw_first_then_uploads_validated_analysis(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "true")
    api = FakeAPI(candidate_mode=True)
    data = NewsData("2026-10-20", "10:00", {"src": [NewsItem(title="AI news", source_id="src")]}, {"src": "Source"})
    article = {
        "id": "11111111-1111-4111-8111-111111111111", "sourceId": "hotlist:src",
        "sourceName": "Source", "title": "AI news", "summary": None, "categories": [],
    }
    api.candidates = [copy.deepcopy(article)]
    monkeypatch.setenv("INTELLIGENCE_MAX_ANALYSES_PER_RUN", "1")

    result = run_intelligence_pipeline(data, None, {"AI": {"MODEL": "test/model"}}, api=api, client=FakeClient())

    assert result is not None
    assert result["articleCount"] == 1
    assert len(api.batches) == 2
    assert api.batches[0]["articles"][0]["analysisStatus"] == "pending"
    assert api.batches[0]["run"]["status"] == "running"
    assert api.batches[1]["articles"][0]["analysisStatus"] == "completed"
    assert api.batches[1]["articles"][0]["analysis"]["evidenceScope"] == "title"
    assert api.batches[1]["run"]["status"] == "completed"
    assert len(api.started) == 1


def test_analysis_is_off_by_default_even_when_ingestion_is_enabled(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.delenv("INTELLIGENCE_ANALYSIS_ENABLED", raising=False)
    api = FakeAPI(candidate_mode=True)
    data = NewsData("2026-10-20", "10:00", {"src": [NewsItem(title="人工智能快讯", source_id="src")]}, {"src": "Source"})

    result = run_intelligence_pipeline(data, None, {}, api=api)

    assert result is not None
    assert len(api.batches) == 2
    assert api.batches[0]["articles"][0]["analysisStatus"] == "not_requested"
    assert api.batches[0]["articles"][0]["categories"] == [{"id": "ai", "assignedBy": "rule"}]
    assert api.started == []


def test_per_run_budget_defers_queue_without_calling_extra_articles(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_MAX_ANALYSES_PER_RUN", "1")
    api = FakeAPI(candidate_mode=True)
    api.candidates = [
        {"id": f"11111111-1111-4111-8111-81111111111{i}", "sourceId": "hotlist:src", "sourceName": "Source", "title": f"AI item {i}", "summary": None, "categories": []}
        for i in range(1, 4)
    ]
    data = NewsData("2026-10-20", "10:00", {"src": [NewsItem(title="AI news", source_id="src")]}, {"src": "Source"})

    client = FakeClient()
    result = run_intelligence_pipeline(data, None, {"AI": {"MODEL": "test/model"}}, api=api, client=client)

    assert result is not None
    assert len(api.started) == 1
    assert client.calls == 1
    assert len(api.batches[-1]["articles"]) == 1
    assert api.batches[-1]["run"]["status"] == "partial"
    assert "2 deferred (PER_RUN_BUDGET)" in api.batches[-1]["run"]["errorSummary"]


def test_server_budget_denial_defers_without_a_model_request(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_MAX_ANALYSES_PER_RUN", "2")
    api = FakeAPI(candidate_mode=True)
    api.candidates = [{
        "id": "11111111-1111-4111-8111-111111111111", "sourceId": "hotlist:src",
        "sourceName": "Source", "title": "AI article", "summary": None, "categories": [],
    }]
    api.start_result = {"started": False, "reason": "DAILY_BUDGET"}
    data = NewsData("2026-10-20", "10:00", {"src": [NewsItem(title="AI news", source_id="src")]}, {"src": "Source"})
    client = FakeClient()

    result = run_intelligence_pipeline(data, None, {"AI": {"MODEL": "test/model"}}, api=api, client=client)

    assert result is not None
    assert client.calls == 0
    assert api.batches[-1]["articles"] == []
    assert "DAILY_BUDGET" in api.batches[-1]["run"]["errorSummary"]


def test_pipeline_records_empty_crawl_run_and_failed_sources(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "false")
    api = FakeAPI(candidate_mode=False)
    data = NewsData("2026-10-20", "10:00", {}, {}, ["source-a", "source-b"])

    result = run_intelligence_pipeline(data, None, {}, api=api)

    assert result is not None
    assert len(api.batches) == 1
    assert api.batches[0]["articles"] == []
    assert api.batches[0]["run"]["status"] == "partial"
    assert api.batches[0]["run"]["sourceCount"] == 2
    assert api.batches[0]["run"]["errorSummary"] == "2 source(s) failed"


def test_pipeline_marks_partial_when_some_sources_fail(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "false")
    api = FakeAPI(candidate_mode=False)
    data = NewsData(
        "2026-10-20", "10:00",
        {"src": [NewsItem(title="AI news", source_id="src")]},
        {"src": "Source"},
        ["failed-source"],
    )

    result = run_intelligence_pipeline(data, None, {}, api=api)

    assert result is not None
    assert api.batches[-1]["run"]["status"] == "partial"
    assert api.batches[-1]["run"]["sourceCount"] == 2
    assert api.batches[-1]["run"]["errorSummary"] == "1 source(s) failed"


def test_pipeline_keeps_article_when_model_is_not_configured(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
    monkeypatch.setenv("INTELLIGENCE_ANALYSIS_ENABLED", "true")
    api = FakeAPI(candidate_mode=False)
    data = NewsData("2026-10-20", "10:00", {"src": [NewsItem(title="AI news", source_id="src")]}, {"src": "Source"})

    result = run_intelligence_pipeline(
        data,
        None,
        {"AI": {"MODEL": "test/model"}},
        api=api,
        client=InvalidConfigClient(),
    )

    assert result is not None
    assert len(api.batches) == 2
    assert api.batches[0]["articles"][0]["title"] == "AI news"
    assert api.batches[0]["articles"][0]["analysisStatus"] == "pending"
    assert api.batches[-1]["run"]["status"] == "partial"
