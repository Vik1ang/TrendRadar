from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Protocol

from json_repair import repair_json

PROMPT_VERSION = "article-analysis-v1"
ANALYSIS_VERSION = 1
ALLOWED_CATEGORIES = {"ai", "technology", "finance", "society"}


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str: ...


class AnalysisError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _strings(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AnalysisError("ANALYSIS_INVALID_SCHEMA")
    return [item.strip()[:3000] for item in value if item.strip()][:30]


def parse_analysis(response: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        parsed = repair_json(response, return_objects=True)
    except Exception as exc:
        raise AnalysisError("ANALYSIS_INVALID_JSON") from exc
    if not isinstance(parsed, dict):
        raise AnalysisError("ANALYSIS_INVALID_SCHEMA")

    categories_raw = parsed.get("categories")
    if not isinstance(categories_raw, list):
        raise AnalysisError("ANALYSIS_INVALID_SCHEMA")
    categories: list[dict[str, Any]] = []
    for candidate in categories_raw[:3]:
        if not isinstance(candidate, dict):
            raise AnalysisError("ANALYSIS_INVALID_SCHEMA")
        category_id = candidate.get("id")
        confidence = candidate.get("confidence")
        if category_id not in ALLOWED_CATEGORIES or not isinstance(confidence, (int, float)) or not isfinite(confidence) or not 0 <= confidence <= 1:
            raise AnalysisError("ANALYSIS_INVALID_SCHEMA")
        categories.append({"id": category_id, "confidence": float(confidence), "assignedBy": "model"})

    summary = parsed.get("summary")
    background = parsed.get("background")
    if not isinstance(summary, str) or not isinstance(background, str):
        raise AnalysisError("ANALYSIS_INVALID_SCHEMA")
    return categories, {
        "summary": summary.strip()[:2000],
        "keyFacts": _strings(parsed.get("keyFacts"), "keyFacts"),
        "background": background.strip()[:10000],
        "potentialImpacts": _strings(parsed.get("potentialImpacts"), "potentialImpacts"),
        "uncertainties": _strings(parsed.get("uncertainties"), "uncertainties"),
    }


def analyze_article(client: ChatClient, article: dict[str, Any], source_name: str) -> dict[str, Any]:
    prompt_path = Path(__file__).resolve().parents[2] / "config" / "ai_article_analysis_prompt.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")
    title = article["title"][:2000]
    summary = (article.get("summary") or "")[:6000]
    user_prompt = f"来源：{source_name}\n标题：{title}\n采集到的摘要（可能为空）：{summary}"
    try:
        response = client.chat(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.2,
            max_tokens=2200,
        )
        categories, result = parse_analysis(response)
        return {
            "categories": categories,
            "replaceCategories": True,
            "analysis": {
                **result,
                "evidenceScope": "summary" if summary else "title",
                "modelName": client.model,
                "promptVersion": PROMPT_VERSION,
                "analysisVersion": ANALYSIS_VERSION,
                "status": "completed",
                "analyzedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            "analysisStatus": "completed",
        }
    except AnalysisError as exc:
        code = exc.code
    except Exception:  # noqa: BLE001 — isolate provider-specific failures to one article.
        code = "MODEL_REQUEST_FAILED"
    return {
        "categories": [],
        "replaceCategories": True,
        "analysis": {
            "keyFacts": [], "potentialImpacts": [], "uncertainties": [],
            "evidenceScope": "summary" if summary else "title",
            "modelName": client.model,
            "promptVersion": PROMPT_VERSION,
            "analysisVersion": ANALYSIS_VERSION,
            "status": "failed",
            "errorCode": code,
            "analyzedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "analysisStatus": "failed",
    }
