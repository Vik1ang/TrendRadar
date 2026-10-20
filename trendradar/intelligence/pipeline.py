from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from trendradar.intelligence.analyzer import (
    ANALYSIS_VERSION,
    PROMPT_VERSION,
    analyze_article,
)
from trendradar.intelligence.api import IngestionAPI, IntelligenceAPIError
from trendradar.intelligence.normalize import build_batch
from trendradar.storage.base import NewsData, RSSData

if TYPE_CHECKING:
    from trendradar.ai.client import AIClient


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _final_run(run: dict[str, Any], status: str, error: str | None = None) -> dict[str, Any]:
    return {
        **run,
        "finishedAt": _now(),
        "status": status,
        **({"errorSummary": error[:1000]} if error else {}),
    }


def _failed_source_count(news_data: NewsData | None, rss_data: RSSData | None) -> int:
    return sum(
        len(set(data.failed_ids))
        for data in (news_data, rss_data)
        if data is not None
    )


def run_intelligence_pipeline(
    news_data: NewsData | None,
    rss_data: RSSData | None,
    config: dict[str, Any],
    *,
    api: IngestionAPI | None = None,
    client: AIClient | None = None,
) -> dict[str, Any] | None:
    """Persist one crawl batch, then analyze only articles missing this model/prompt version."""
    if not _env_bool("INTELLIGENCE_ENABLED", False):
        return None

    timezone_name = str(config.get("TIMEZONE", "Asia/Shanghai"))
    sources, articles = build_batch(news_data, rss_data, timezone_name)
    if news_data is None and rss_data is None:
        return None

    if api is None:
        api = IngestionAPI()
    now = _now()
    failed_sources = _failed_source_count(news_data, rss_data)
    source_count = len(sources) + failed_sources
    run = {
        "id": str(uuid4()),
        "startedAt": now,
        "finishedAt": now,
        "status": "running",
        "sourceCount": source_count,
    }
    crawl_errors = f"{failed_sources} source(s) failed" if failed_sources else None

    if not articles:
        status = "partial" if failed_sources else "completed"
        body = {
            "run": _final_run(run, status, crawl_errors),
            "sources": sources,
            "articles": [],
        }
        return api.import_batch(body)

    ai_enabled = _env_bool("INTELLIGENCE_ANALYSIS_ENABLED", True)
    ai_config = config.get("AI", {}) if isinstance(config.get("AI", {}), dict) else {}
    model_name = str(ai_config.get("MODEL") or "unconfigured")
    model_valid = False
    if ai_enabled:
        if client is None:
            from trendradar.ai.client import AIClient

            client = AIClient(ai_config)
        model_valid, _ = client.validate_config()

    if not ai_enabled:
        for article in articles:
            article["analysisStatus"] = "not_requested"
        status = "partial" if failed_sources else "completed"
        body = {"run": _final_run(run, status, crawl_errors), "sources": sources, "articles": articles}
        return api.import_batch(body)

    if not model_valid:
        failed_at = _now()
        for article in articles:
            article["analysisStatus"] = "failed"
            article["analysis"] = {
                "keyFacts": [], "potentialImpacts": [], "uncertainties": [],
                "evidenceScope": "summary" if article.get("summary") else "title",
                "modelName": model_name,
                "promptVersion": PROMPT_VERSION,
                "analysisVersion": ANALYSIS_VERSION,
                "status": "failed",
                "errorCode": "MODEL_NOT_CONFIGURED",
                "analyzedAt": failed_at,
            }
        errors = [error for error in (crawl_errors, "MODEL_NOT_CONFIGURED") if error]
        body = {"run": _final_run(run, "partial", "; ".join(errors)), "sources": sources, "articles": articles}
        return api.import_batch(body)

    initial_body = {"run": run, "sources": sources, "articles": articles}
    initial_result = api.import_batch(initial_body)

    try:
        candidates = api.analysis_candidates({
            "articleIds": [article["id"] for article in articles],
            "modelName": model_name,
            "promptVersion": PROMPT_VERSION,
            "analysisVersion": ANALYSIS_VERSION,
        })
    except IntelligenceAPIError:
        api.import_batch({"run": _final_run(run, "partial", "ANALYSIS_CANDIDATE_LOOKUP_FAILED"), "sources": [], "articles": []})
        raise

    try:
        max_per_run = max(1, min(int(os.environ.get("INTELLIGENCE_MAX_ANALYSES_PER_RUN", "100")), 1000))
    except ValueError:
        max_per_run = 100
    deferred_count = max(0, len(candidates) - max_per_run)
    candidates = candidates[:max_per_run]
    article_by_id = {article["id"]: article for article in articles}
    source_names = {source["id"]: source["name"] for source in sources}
    results: list[dict[str, Any]] = []
    failures = 0
    for article_id in candidates:
        candidate_article = article_by_id.get(article_id)
        if candidate_article is None or client is None:
            continue
        analysis_result = analyze_article(client, candidate_article, source_names.get(candidate_article["sourceId"], candidate_article["sourceId"]))
        candidate_article.update(analysis_result)
        results.append(candidate_article)
        if candidate_article["analysisStatus"] != "completed":
            failures += 1

    status = "partial" if failures or deferred_count or failed_sources else "completed"
    error_parts = [crawl_errors] if crawl_errors else []
    if failures or deferred_count:
        error_parts.append(f"{failures} article analysis job(s) failed; {deferred_count} deferred by the per-run limit")
    error_summary = "; ".join(error_parts) or None
    final_result = api.import_batch({
        "run": _final_run(run, status, error_summary),
        "sources": [],
        "articles": results,
    })
    return {
        **final_result,
        "articleCount": initial_result.get("articleCount", len(articles)),
        "insertedCount": initial_result.get("insertedCount", 0),
        "updatedCount": initial_result.get("updatedCount", 0),
    }
