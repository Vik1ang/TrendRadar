import Foundation

/// Prefers the verified LAN HTTPS endpoint and uses the HTTPS relay only for ordinary
/// reachability failures. Authorization and TLS failures never trigger fallback.
struct RoutedTrendRadarAPI: TrendRadarAPI {
  let lan: any TrendRadarAPI
  let relay: (any TrendRadarAPI)?

  func categories() async throws -> [NewsCategory] { try await route { try await $0.categories() } }
  func news(_ query: NewsQuery) async throws -> NewsPage {
    try await route { try await $0.news(query) }
  }
  func search(_ query: NewsQuery) async throws -> NewsPage {
    try await route { try await $0.search(query) }
  }
  func article(id: String) async throws -> NewsArticle {
    try await route { try await $0.article(id: id) }
  }
  func analysis(id: String) async throws -> ArticleAnalysis {
    try await route { try await $0.analysis(id: id) }
  }

  private func route<Value: Sendable>(_ request: (any TrendRadarAPI) async throws -> Value)
    async throws -> Value
  {
    do { return try await request(lan) } catch let error as URLError {
      guard Self.isReachabilityFailure(error), let relay else { throw error }
      return try await request(relay)
    }
  }

  private static func isReachabilityFailure(_ error: URLError) -> Bool {
    switch error.code {
    case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost, .dnsLookupFailed,
      .timedOut:
      true
    default:
      false
    }
  }
}
