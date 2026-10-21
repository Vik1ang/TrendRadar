from trendradar.intelligence.normalize import build_batch, normalize_url
from trendradar.storage.base import NewsData, NewsItem, RSSData, RSSItem


def test_normalized_url_removes_tracking_and_fragment():
    assert normalize_url("HTTPS://Example.com/a/?utm_source=x&b=2#section") == "https://example.com/a?b=2"


def test_article_identity_is_stable_and_rss_guid_is_used_when_url_is_missing():
    news = NewsData(
        date="2026-10-20",
        crawl_time="10:00",
        items={"source-a": [NewsItem(title="ＡＩ 新闻", source_id="source-a", url="https://example.com/story?utm_campaign=one")]},
        id_to_name={"source-a": "Source A"},
    )
    first_sources, first_articles = build_batch(news, None, "Asia/Shanghai")
    second_sources, second_articles = build_batch(news, None, "Asia/Shanghai")
    assert first_sources == second_sources
    assert first_articles[0]["id"] == second_articles[0]["id"]
    assert first_articles[0]["contentStatus"] == "title_only"

    rss_a = RSSData(
        date="2026-10-20", crawl_time="10:00",
        items={"feed": [RSSItem(title="Title", feed_id="feed", url="", guid="stable-guid", summary="brief")]},
        id_to_name={"feed": "Feed"},
    )
    rss_b = RSSData(
        date="2026-10-20", crawl_time="10:10",
        items={"feed": [RSSItem(title="Title", feed_id="feed", url="", guid="stable-guid", summary="brief")]},
        id_to_name={"feed": "Feed"},
    )
    assert build_batch(None, rss_a, "Asia/Shanghai")[1][0]["id"] == build_batch(None, rss_b, "Asia/Shanghai")[1][0]["id"]


def test_build_batch_preserves_historical_first_seen_time():
    data = NewsData(
        date="2026-10-20",
        crawl_time="10:00",
        items={"src": [NewsItem(title="AI news", source_id="src", first_time="2026-10-19 09:30:00")]},
        id_to_name={"src": "Source"},
    )

    _sources, articles = build_batch(data, None, "Asia/Shanghai")

    assert articles[0]["firstSeenAt"] == "2026-10-19T01:30:00Z"


def test_build_batch_assigns_baseline_categories_without_ai_analysis():
    news = NewsData(
        date="2026-10-20",
        crawl_time="10:00",
        items={"source-a": [
            NewsItem(title="DeepSeek 发布新模型", source_id="source-a"),
            NewsItem(title="国债收益率变化", source_id="source-a"),
            NewsItem(title="地震后救援持续", source_id="source-a"),
            NewsItem(title="普通热点标题", source_id="source-a"),
        ]},
        id_to_name={"source-a": "Source A"},
    )

    _sources, articles = build_batch(news, None, "Asia/Shanghai")

    assert articles[0]["categories"] == [
        {"id": "ai", "assignedBy": "rule"},
        {"id": "technology", "assignedBy": "rule"},
    ]
    assert articles[1]["categories"] == [{"id": "finance", "assignedBy": "rule"}]
    assert articles[2]["categories"] == [{"id": "society", "assignedBy": "rule"}]
    assert articles[3]["categories"] == []
    assert all(article["analysisStatus"] == "pending" for article in articles)
