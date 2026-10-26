import Foundation

enum MockScenario: String {
  case standard
  case empty
  case feedError
  case searchError
  case pagination
  case staleSearch
  case detailFailsOnce

  static func launchArguments(_ arguments: [String]) -> MockScenario {
    guard let index = arguments.firstIndex(of: "--mock-scenario"),
      arguments.indices.contains(index + 1),
      let scenario = MockScenario(rawValue: arguments[index + 1])
    else { return .standard }
    return scenario
  }
}

actor MockTrendRadarAPI: TrendRadarAPI {
  private let calendar = Calendar(identifier: .gregorian)
  private(set) var searchRequestCount = 0
  private(set) var newsRequestCount = 0
  private var detailFailuresRemaining: Int
  let scenario: MockScenario

  init(scenario: MockScenario = .standard) {
    self.scenario = scenario
    self.detailFailuresRemaining = scenario == .detailFailsOnce ? 1 : 0
  }

  private var allArticles: [NewsArticle] {
    let now = Date()
    let source = NewsSource(id: "mock-news", name: "TrendRadar 编辑部", type: "rss")
    let entries: [(String, String?, String?, Int, AnalysisStatus)] = [
      ("大模型推理效率持续提升", "ai", "新一代推理技术让复杂任务响应更快。", 1, .completed),
      ("全球科技企业加大研发投入", "technology", nil, 2, .notRequested),
      ("主要市场今日走势分化", "finance", "市场关注最新宏观数据。", 3, .pending),
      ("城市公共服务数字化升级", "society", "多地推进便民服务平台整合。", 4, .failed),
      ("多标签新闻的示例", "ai", "同时涉及人工智能与产业发展。", 5, .notRequested),
      ("开源模型社区发布新工具", "ai", "开发者工具链持续完善，降低模型部署门槛。", 6, .processing),
      ("生成式 AI 应用进入新阶段", nil, "更多团队开始关注产品体验与数据治理。", 7, .notRequested),
      ("企业加快智能化改造步伐", "technology", "人工智能与云计算基础设施协同发展。", 8, .completed),
      ("全球市场关注最新利率信号", "finance", nil, 9, .notRequested),
      ("公共交通推出便民新服务", "society", "面向市民的数字化服务进一步优化。", 10, .pending),
      ("科技产业链迎来合作机会", "technology", "跨领域合作成为行业关注方向。", 11, .failed),
    ]
    let baseline = entries.enumerated().map { index, item in
      makeArticle(
        index: index + 1, title: item.0, category: item.1, summary: item.2,
        hoursAgo: item.3, status: item.4, source: source, now: now)
    }
    if scenario == .empty || scenario == .feedError { return [] }
    if scenario == .staleSearch {
      return baseline + [
        makeArticle(
          index: 50, title: "慢请求结果", category: "ai", summary: "延迟返回的旧查询。",
          hoursAgo: 12, status: .notRequested, source: source, now: now),
        makeArticle(
          index: 51, title: "快速新结果", category: "ai", summary: "新查询应保持在页面上。",
          hoursAgo: 13, status: .notRequested, source: source, now: now),
      ]
    }
    if scenario == .pagination {
      let extra = (12...35).map { index in
        makeArticle(
          index: index, title: "分页新闻 \(index)", category: "ai",
          summary: "用于验证 offset 分页的本地夹具。", hoursAgo: index,
          status: .notRequested, source: source, now: now)
      }
      return baseline + extra
    }
    return baseline
  }

  func categories() async throws -> [NewsCategory] {
    [
      NewsCategory(id: "ai", name: "AI", sortOrder: 1),
      NewsCategory(id: "technology", name: "科技", sortOrder: 2),
      NewsCategory(id: "finance", name: "财经", sortOrder: 3),
      NewsCategory(id: "society", name: "社会", sortOrder: 4),
    ]
  }

  func news(_ query: NewsQuery) async throws -> NewsPage {
    newsRequestCount += 1
    if scenario == .feedError {
      throw APIClientError.serviceUnavailable(message: nil, requestID: nil)
    }
    return page(query)
  }

  func search(_ query: NewsQuery) async throws -> NewsPage {
    searchRequestCount += 1
    if scenario == .searchError {
      throw APIClientError.serviceUnavailable(message: nil, requestID: nil)
    }
    if scenario == .staleSearch {
      let duration: Duration =
        query.text?.contains("慢") == true ? .milliseconds(900) : .milliseconds(60)
      // This fixture ignores cancellation to exercise stale-response guards.
      try? await Task.sleep(for: duration)
    }
    return page(query)
  }

  func article(id: String) async throws -> NewsArticle {
    if detailFailuresRemaining > 0 {
      detailFailuresRemaining -= 1
      throw APIClientError.serviceUnavailable(message: nil, requestID: nil)
    }
    guard let article = allArticles.first(where: { $0.id == id }) else {
      throw APIClientError.notFound(requestID: "mock-missing")
    }
    return article
  }

  func analysis(id: String) async throws -> ArticleAnalysis {
    guard let article = allArticles.first(where: { $0.id == id }) else {
      throw APIClientError.notFound(requestID: "mock-missing")
    }
    return ArticleAnalysis(
      status: article.analysisStatus, errorCode: nil, summary: article.analysisSummary,
      keyFacts: article.analysisStatus == .completed ? ["此内容仅为本地演示数据。"] : nil,
      background: nil, potentialImpacts: nil, uncertainties: nil, evidenceScope: nil,
      modelName: nil, promptVersion: nil, analysisVersion: nil, analyzedAt: nil)
  }

  func searchCount() -> Int { searchRequestCount }
  func newsCount() -> Int { newsRequestCount }

  private func page(_ query: NewsQuery) -> NewsPage {
    let filtered = allArticles.filter { article in
      (query.category == nil || article.categories.contains { $0.id == query.category })
        && (query.text?.isEmpty != false
          || article.title.localizedCaseInsensitiveContains(query.text!)
          || (article.summary?.localizedCaseInsensitiveContains(query.text!) ?? false)
          || (article.analysisSummary?.localizedCaseInsensitiveContains(query.text!) ?? false))
    }
    let items = Array(filtered.dropFirst(query.offset).prefix(query.limit))
    return NewsPage(
      data: items,
      pagination: Pagination(
        limit: query.limit, offset: query.offset, total: filtered.count,
        hasMore: query.offset + items.count < filtered.count))
  }

  private func makeArticle(
    index: Int, title: String, category: String?, summary: String?, hoursAgo: Int,
    status: AnalysisStatus, source: NewsSource, now: Date
  ) -> NewsArticle {
    NewsArticle(
      id: "00000000-0000-0000-0000-\(String(format: "%012d", index))", title: title,
      source: source,
      publishedAt: index == 2 ? nil : calendar.date(byAdding: .hour, value: -hoursAgo, to: now),
      firstSeenAt: calendar.date(byAdding: .hour, value: -hoursAgo, to: now) ?? now,
      url: URL(string: "https://example.com/mock/\(index)"), summary: summary,
      analysisSummary: status == .completed ? "已生成的 Mock AI 分析：该变化值得持续关注。" : nil,
      contentStatus: summary == nil ? "title_only" : "summary_available", analysisStatus: status,
      categories: category.map { [ArticleCategory(id: $0, name: Self.categoryName($0))] } ?? [])
  }

  private static func categoryName(_ id: String) -> String {
    ["ai": "AI", "technology": "科技", "finance": "财经", "society": "社会"][id] ?? id
  }
}
