import json

import pytest

from trendradar.intelligence.analyzer import (
    AnalysisError,
    analyze_article,
    parse_analysis,
)

GOOD_RESPONSE = json.dumps({
    "categories": [{"id": "ai", "confidence": 0.94}],
    "summary": "模型发布了新功能。",
    "keyFacts": ["标题称模型发布新功能"],
    "background": "仅由标题可知。",
    "potentialImpacts": ["可能影响 AI 软件竞争，尚待验证。"],
    "uncertainties": ["具体指标未给出"],
}, ensure_ascii=False)


def test_structured_analysis_validates_categories_and_fact_fields():
    categories, analysis = parse_analysis(GOOD_RESPONSE)
    assert categories == [{"id": "ai", "confidence": 0.94, "assignedBy": "model"}]
    assert analysis["keyFacts"] == ["标题称模型发布新功能"]


def test_structured_analysis_rejects_unknown_category():
    payload = json.loads(GOOD_RESPONSE)
    payload["categories"] = [{"id": "other", "confidence": 0.6}]
    with pytest.raises(AnalysisError):
        parse_analysis(json.dumps(payload))


def test_analyzer_records_scope_and_version_without_claiming_full_text():
    class FakeClient:
        model = "test/model"

        def chat(self, messages, **kwargs):
            assert "标题" in messages[-1]["content"]
            return GOOD_RESPONSE

    result = analyze_article(FakeClient(), {"title": "AI 发布", "summary": ""}, "测试源")
    assert result["analysisStatus"] == "completed"
    assert result["analysis"]["evidenceScope"] == "title"
    assert result["analysis"]["promptVersion"] == "article-analysis-v1"
