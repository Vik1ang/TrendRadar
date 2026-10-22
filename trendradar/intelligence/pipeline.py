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


def _bounded_env_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return min(max(value, 0), maximum)


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
    run_metadata: dict[str, Any] | None = None,
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
        **({"metadata": run_metadata} if run_metadata else {}),
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

    # Collection/classification is independent from model use. Both switches
    # must be explicitly enabled before any production model call can happen.
    ai_enabled = _env_bool("INTELLIGENCE_ANALYSIS_ENABLED", False)
    for article in articles:
        article["analysisStatus"] = "pending" if ai_enabled else "not_requested"

    initial_result = api.import_batch({"run": run, "sources": sources, "articles": articles})

    if not ai_enabled:
        status = "partial" if failed_sources else "completed"
        return api.import_batch({
            "run": _final_run(run, status, crawl_errors),
            "sources": [],
            "articles": [],
        })

    ai_config = config.get("AI", {}) if isinstance(config.get("AI", {}), dict) else {}
    model_name = str(ai_config.get("MODEL") or "unconfigured")
    model_valid = False
    if ai_enabled:
        if client is None:
            from trendradar.ai.client import AIClient

            client = AIClient(ai_config)
        model_valid, _ = client.validate_config()

    if not model_valid:
        errors = [error for error in (crawl_errors, "MODEL_NOT_CONFIGURED") if error]
        return api.import_batch({"run": _final_run(run, "partial", "; ".join(errors)), "sources": [], "articles": []})

    try:
        candidates = api.analysis_candidates({
            "modelName": model_name,
            "promptVersion": PROMPT_VERSION,
            "analysisVersion": ANALYSIS_VERSION,
        })
    except IntelligenceAPIError:
        api.import_batch({"run": _final_run(run, "partial", "ANALYSIS_CANDIDATE_LOOKUP_FAILED"), "sources": [], "articles": []})
        raise

    max_per_run = _bounded_env_int("INTELLIGENCE_MAX_ANALYSES_PER_RUN", 1, 100)
    results: list[dict[str, Any]] = []
    failures = 0
    deferred_count = 0
    deferred_reason: str | None = None
    for index, candidate in enumerate(candidates):
        if len(results) >= max_per_run:
            deferred_count += len(candidates) - index
            deferred_reason = "PER_RUN_BUDGET"
            break
        article_id = candidate.get("id")
        if not isinstance(article_id, str) or client is None:
            deferred_count += 1
            continue
        try:
            reservation = api.start_analysis({
                "runId": run["id"],
                "articleId": article_id,
                "modelName": model_name,
                "promptVersion": PROMPT_VERSION,
                "analysisVersion": ANALYSIS_VERSION,
            })
        except IntelligenceAPIError:
            api.import_batch({
                "run": _final_run(run, "partial", "ANALYSIS_RESERVATION_FAILED"),
                "sources": [],
                "articles": results,
            })
            raise
        if reservation.get("started") is not True:
            deferred_count += len(candidates) - index
            deferred_reason = str(reservation.get("reason") or "BUDGET_OR_CONCURRENCY_LIMIT")
            break
        source_name = candidate.get("sourceName")
        if not isinstance(source_name, str) or not source_name:
            source_name = str(candidate.get("sourceId") or "unknown")
        analysis_result = analyze_article(client, candidate, source_name)
        candidate.update(analysis_result)
        results.append(candidate)
        if candidate["analysisStatus"] != "completed":
            failures += 1

    status = "partial" if failures or deferred_count or failed_sources else "completed"
    error_parts = [crawl_errors] if crawl_errors else []
    if failures or deferred_count:
        reason = f" ({deferred_reason})" if deferred_reason else ""
        error_parts.append(f"{failures} article analysis job(s) failed; {deferred_count} deferred{reason}")
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
