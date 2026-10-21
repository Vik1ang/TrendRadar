from __future__ import annotations

import re

# Deliberately conservative, explainable baseline categories for new ingestion.
# Deep analysis may add labels, but collection must not require a model call.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ai", re.compile(
        r"人工智能|大模型|生成式(?:人工智能|AI)|机器学习|深度学习|神经网络|智能体|AI Agent|LLM|"
        r"(?<![a-z0-9])(?:AI|OpenAI|ChatGPT|DeepSeek|Claude|Gemini)(?![a-z0-9])",
        re.IGNORECASE,
    )),
    ("technology", re.compile(
        r"科技|技术|芯片|半导体|软件|硬件|互联网|云计算|开源|操作系统|机器人|自动驾驶|无人驾驶|"
        r"手机|电脑|GPU|CPU|模型|航天|卫星|量子|脑机|基因|光刻机|智能汽车|新能源",
        re.IGNORECASE,
    )),
    ("finance", re.compile(
        r"国债|债券|股票|股市|A股|港股|美股|金融|财经|央行|美联储|利率|降息|加息|通胀|GDP|"
        r"汇率|外汇|财报|营收|利润|上市|IPO|基金|期货|房价|经济增长|失业率",
        re.IGNORECASE,
    )),
    ("society", re.compile(
        r"事故|地震|洪水|暴雨|台风|火灾|爆炸|伤亡|警方|法院|刑事|教育|学校|医疗|医院|交通事故|"
        r"民生|社保|就业|公共安全|食品安全|失踪|救援|公共卫生",
        re.IGNORECASE,
    )),
)


def classify_article(title: str, summary: str | None = None) -> list[dict[str, str]]:
    """Assign deterministic baseline channels from available text, without model calls."""
    text = f"{title}\n{summary or ''}"
    return [
        {"id": category_id, "assignedBy": "rule"}
        for category_id, pattern in _RULES
        if pattern.search(text)
    ]
