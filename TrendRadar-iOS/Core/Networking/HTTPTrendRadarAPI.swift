import Foundation

/// Authenticated V1 client. Callers provide verified HTTPS base URLs and future device credentials.
/// Never retries over an alternate/insecure scheme after TLS or authorization failures.
struct HTTPTrendRadarAPI: TrendRadarAPI {
  let baseURL: URL
  let credentials: any DeviceCredentialProviding
  let session: URLSession

  init(
    baseURL: URL, credentials: any DeviceCredentialProviding = KeychainDeviceCredentials(),
    session: URLSession = .shared
  ) {
    self.baseURL = baseURL
    self.credentials = credentials
    self.session = session
  }

  func categories() async throws -> [NewsCategory] { try await get("/categories") }
  func news(_ query: NewsQuery) async throws -> NewsPage {
    try await page(path: "/news", query: query)
  }
  func search(_ query: NewsQuery) async throws -> NewsPage {
    try await page(path: "/search", query: query)
  }
  func article(id: String) async throws -> NewsArticle { try await get("/news/\(id)") }
  func analysis(id: String) async throws -> ArticleAnalysis {
    try await get("/news/\(id)/analysis")
  }

  private func page(path: String, query: NewsQuery) async throws -> NewsPage {
    var items: [(String, String)] = [
      ("limit", String(query.limit)), ("offset", String(query.offset)),
    ]
    if let category = query.category { items.append(("category", category)) }
    if let text = query.text, !text.isEmpty { items.append(("q", text)) }
    return try await get(path, query: items)
  }

  private func get<T: Decodable>(_ path: String, query: [(String, String)] = []) async throws -> T {
    guard baseURL.scheme?.lowercased() == "https" else { throw APIClientError.invalidResponse }
    var components = URLComponents(
      url: baseURL.appending(path: "/api/v1\(path)"), resolvingAgainstBaseURL: false)
    if !query.isEmpty { components?.queryItems = query.map(URLQueryItem.init(name:value:)) }
    guard let url = components?.url else { throw APIClientError.invalidResponse }
    var request = URLRequest(url: url)
    request.httpMethod = "GET"
    let token: String?
    do {
      token = try await credentials.bearerToken()
    } catch APIClientError.credentialExpired {
      NotificationCenter.default.post(name: .deviceReauthenticationRequired, object: nil)
      throw APIClientError.credentialExpired
    }
    if let token, !token.isEmpty {
      request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }
    let (data, response) = try await session.data(for: request)
    guard let response = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
    guard (200..<300).contains(response.statusCode) else {
      if response.statusCode == 401 {
        NotificationCenter.default.post(name: .deviceReauthenticationRequired, object: nil)
      }
      throw decodeError(data, status: response.statusCode)
    }
    do {
      let decoder = JSONDecoder()
      decoder.dateDecodingStrategy = .custom { decoder in
        let container = try decoder.singleValueContainer()
        let value = try container.decode(String.self)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: value) { return date }
        throw DecodingError.dataCorruptedError(
          in: container, debugDescription: "Invalid RFC 3339 timestamp")
      }
      return try decoder.decode(T.self, from: data)
    } catch { throw APIClientError.invalidResponse }
  }

  private func decodeError(_ data: Data, status: Int) -> APIClientError {
    let body = try? JSONDecoder().decode(APIErrorBody.self, from: data)
    switch status {
    case 400:
      return .validation(
        message: body?.error.message ?? "请求参数无效。", requestID: body?.requestId)
    case 401:
      return .unauthorized
    case 403:
      return .forbidden
    case 404:
      if body?.error.code == "NOT_FOUND" {
        return .notFound(requestID: body?.requestId)
      }
      let route = try? JSONDecoder().decode(FastifyRouteError.self, from: data)
      return .unknownRoute(route?.message)
    case 503:
      return .serviceUnavailable(message: body?.error.message, requestID: body?.requestId)
    case 500...599:
      return .server(
        status: status, message: body?.error.message, requestID: body?.requestId)
    default:
      return .unexpectedStatus(status)
    }
  }
}
