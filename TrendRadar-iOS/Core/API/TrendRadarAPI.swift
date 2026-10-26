import Foundation

extension Notification.Name {
  static let deviceReauthenticationRequired = Notification.Name("deviceReauthenticationRequired")
  static let deviceAuthenticationRestored = Notification.Name("deviceAuthenticationRestored")
}

struct NewsQuery: Hashable {
  var category: String? = nil
  var text: String? = nil
  var limit: Int = 30
  var offset: Int = 0
}

protocol TrendRadarAPI: Sendable {
  func categories() async throws -> [NewsCategory]
  func news(_ query: NewsQuery) async throws -> NewsPage
  func search(_ query: NewsQuery) async throws -> NewsPage
  func article(id: String) async throws -> NewsArticle
  func analysis(id: String) async throws -> ArticleAnalysis
}

enum APIClientError: LocalizedError {
  case unauthorized
  case forbidden
  case credentialExpired
  case notFound(requestID: String?)
  case unknownRoute(String?)
  case validation(message: String, requestID: String?)
  case serviceUnavailable(message: String?, requestID: String?)
  case server(status: Int, message: String?, requestID: String?)
  case unexpectedStatus(Int)
  case invalidResponse

  var errorDescription: String? {
    switch self {
    case .unauthorized: "设备凭据无效或已失效，请重新认证。"
    case .forbidden: "当前设备无权访问此内容。"
    case .credentialExpired: "设备凭据已过期，需要重新认证。"
    case .notFound: "这条新闻已不存在。"
    case .unknownRoute(let message): message ?? "API 路径不存在。"
    case .validation(let message, _): message
    case .serviceUnavailable(let message, _): message ?? "服务暂时不可用，请稍后重试。"
    case .server(_, let message, _): message ?? "服务内部错误。"
    case .unexpectedStatus(let status): "服务返回错误（\(status)）。"
    case .invalidResponse: "无法读取服务响应。"
    }
  }
}
