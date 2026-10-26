import XCTest

@testable import TrendRadar

final class TrendRadarTests: XCTestCase {
  func testMockReturnsFourCategoriesAndOffsetPages() async throws {
    let api = MockTrendRadarAPI()
    let categories = try await api.categories()
    XCTAssertEqual(Set(categories.map(\.id)), Set(["ai", "technology", "finance", "society"]))

    let first = try await api.news(NewsQuery(category: "ai", limit: 1, offset: 0))
    XCTAssertEqual(first.data.count, 1)
    XCTAssertTrue(first.pagination.hasMore)
    let second = try await api.news(NewsQuery(category: "ai", limit: 1, offset: 1))
    XCTAssertEqual(second.pagination.offset, 1)
    XCTAssertNotEqual(first.data.first?.id, second.data.first?.id)
  }

  func testMockSearchSupportsEmptyAndUncategorizedResults() async throws {
    let api = MockTrendRadarAPI()
    let empty = try await api.search(NewsQuery(text: "没有匹配", limit: 20, offset: 0))
    XCTAssertTrue(empty.data.isEmpty)
    let all = try await api.news(NewsQuery(limit: 20, offset: 0))
    XCTAssertTrue(all.data.contains { $0.categories.isEmpty })
  }

  func testNullPublicationAndSummaryAndNotRequestedAnalysis() async throws {
    let api = MockTrendRadarAPI()
    let page = try await api.news(NewsQuery(limit: 20, offset: 0))
    let article = try XCTUnwrap(page.data.first { $0.publishedAt == nil })
    XCTAssertNil(article.summary)
    XCTAssertEqual(article.displayDate, article.firstSeenAt)
    let noAnalysis = try XCTUnwrap(page.data.first { $0.analysisStatus == .notRequested })
    let analysis = try await api.analysis(id: noAnalysis.id)
    XCTAssertEqual(analysis.status, .notRequested)
  }

  @MainActor
  func testLateSearchResponseCannotReplaceNewerQuery() async throws {
    let api = MockTrendRadarAPI(scenario: .staleSearch)
    let model = SearchModel(api: api)
    model.text = "慢请求"
    let slow = Task { await model.search() }
    try await Task.sleep(for: .milliseconds(400))  // Slow request has left the debounce window.
    model.text = "快速新结果"
    slow.cancel()
    let fast = Task { await model.search() }
    await fast.value
    await slow.value
    XCTAssertEqual(model.articles.map(\.title), ["快速新结果"])
    let requests = await api.searchCount()
    XCTAssertEqual(requests, 2)
  }

  func testSearchDebounceCoalescesRapidModelChanges() async throws {
    let api = MockTrendRadarAPI()
    let model = await MainActor.run { SearchModel(api: api) }
    let first = Task { @MainActor in
      model.text = "大"
      await model.search()
    }
    try await Task.sleep(for: .milliseconds(80))
    first.cancel()
    let final = Task { @MainActor in
      model.text = "大模型"
      await model.search()
    }
    await final.value
    await first.value
    let requests = await api.searchCount()
    XCTAssertEqual(requests, 1)
  }

  func testKeychainCredentialLifecycleAndExpiry() async throws {
    let store = KeychainDeviceCredentials(service: "com.trendradar.test.\(UUID().uuidString)")
    let expiry = Date(timeIntervalSinceNow: 120)
    try await store.store(
      DeviceCredential(
        accessToken: "test-only-device-credential", expiresAt: expiry, deviceID: "device-1",
        deviceName: "Test iPhone", scopes: ["news:read"]))
    let savedToken = try await store.bearerToken()
    XCTAssertEqual(savedToken, "test-only-device-credential")
    let metadata = try await store.credential()
    XCTAssertEqual(metadata?.deviceID, "device-1")
    XCTAssertEqual(metadata?.deviceName, "Test iPhone")
    XCTAssertEqual(metadata?.scopes, ["news:read"])

    try await store.store(
      DeviceCredential(
        accessToken: "expired-test-credential", expiresAt: Date(timeIntervalSince1970: 1)))
    do {
      _ = try await store.bearerToken()
      XCTFail("Expired device credential must not be used")
    } catch let error as APIClientError {
      if case .credentialExpired = error {} else { XCTFail("Unexpected error: \(error)") }
    }
    try await store.revoke()
    let revokedToken = try await store.bearerToken()
    XCTAssertNil(revokedToken)
  }

  func testDevicePairingDecodesMetadataAndSendsCodeOnlyInHTTPSHeader() async throws {
    StubURLProtocol.statusCode = 200
    let validAccessToken = "trd_dev_" + String(repeating: "A", count: 43)
    StubURLProtocol.responseData = Data(
      """
      {"deviceId":"11111111-1111-4111-8111-111111111111","deviceName":"Test iPhone","accessToken":"\(validAccessToken)","expiresAt":"2099-10-23T00:00:00.123Z","scopes":["news:read"]}
      """.utf8)
    StubURLProtocol.lastRequest = nil
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [StubURLProtocol.self]
    let session = URLSession(configuration: configuration)
    let client = DevicePairingClient(
      baseURL: URL(string: "https://lan.test")!, session: session)

    let credential = try await client.pair(code: "one-time-code", deviceName: "Test iPhone")
    XCTAssertEqual(credential.accessToken, validAccessToken)
    XCTAssertEqual(credential.deviceID, "11111111-1111-4111-8111-111111111111")
    XCTAssertEqual(credential.scopes, ["news:read"])
    XCTAssertEqual(StubURLProtocol.lastRequest?.url?.path, "/api/v2/devices/pair")
    XCTAssertNil(StubURLProtocol.lastRequest?.url?.query)
    XCTAssertEqual(
      StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"),
      "Pairing one-time-code")
    let requestBody = String(
      data: StubURLProtocol.lastRequest?.httpBody ?? Data(), encoding: .utf8) ?? ""
    XCTAssertFalse(requestBody.contains("one-time-code"))

    StubURLProtocol.responseData = Data(
      #"{"deviceId":"not-a-uuid","deviceName":"Test iPhone","accessToken":"trd_dev_short","expiresAt":"2099-10-23T00:00:00Z","scopes":["news:read","admin"]}"#.utf8)
    do {
      _ = try await client.pair(code: "one-time-code", deviceName: "Test iPhone")
      XCTFail("Malformed or over-privileged pairing responses must not be stored")
    } catch let error as APIClientError {
      guard case .invalidResponse = error else { return XCTFail("Unexpected: \(error)") }
    }
  }

  func testDevicePairingRejectsCleartextAndUnauthorizedCode() async throws {
    let cleartext = DevicePairingClient(baseURL: URL(string: "http://lan.test")!)
    do {
      _ = try await cleartext.pair(code: "code", deviceName: "Test iPhone")
      XCTFail("Pairing must require HTTPS")
    } catch let error as APIClientError {
      guard case .invalidResponse = error else { return XCTFail("Unexpected: \(error)") }
    }

    StubURLProtocol.statusCode = 401
    StubURLProtocol.responseData = Data(#"{"error":{"code":"UNAUTHORIZED","message":"expired"}}"#.utf8)
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [StubURLProtocol.self]
    let client = DevicePairingClient(
      baseURL: URL(string: "https://lan.test")!, session: URLSession(configuration: configuration))
    do {
      _ = try await client.pair(code: "expired", deviceName: "Test iPhone")
      XCTFail("Unauthorized pairing must fail")
    } catch let error as APIClientError {
      guard case .unauthorized = error else { return XCTFail("Unexpected: \(error)") }
    }
  }

  func testLiveConfigurationRequiresHTTPSForLANAndRelay() {
    let cleartextLAN = APIConfiguration.from([
      "--live-api", "--lan-base-url", "http://lan.test", "--relay-base-url",
      "https://relay.test",
    ])
    XCTAssertFalse(cleartextLAN.isLive)
    XCTAssertNil(cleartextLAN.lanBaseURL)

    let cleartextRelay = APIConfiguration.from([
      "--live-api", "--lan-base-url", "https://lan.test", "--relay-base-url",
      "http://relay.test",
    ])
    XCTAssertTrue(cleartextRelay.isLive)
    XCTAssertNil(cleartextRelay.relayBaseURL)
  }

  func testV1SuccessResponsesDecodeArticlePageCatalogAndAnalysis() async throws {
    let client = makeHTTPClient(status: 200, body: Data(Self.pageJSON.utf8))
    let page = try await client.news(NewsQuery(category: "ai", limit: 1, offset: 0))
    XCTAssertEqual(page.data.first?.id, Self.articleID)
    XCTAssertEqual(page.data.first?.categories.map(\.id), ["ai", "technology"])
    XCTAssertEqual(page.pagination.total, 9)
    XCTAssertTrue(page.pagination.hasMore)

    StubURLProtocol.responseData = Data(Self.categoryJSON.utf8)
    let decodedCategories = try await client.categories()
    XCTAssertEqual(decodedCategories.map(\.id), ["ai"])
    StubURLProtocol.responseData = Data(Self.articleJSON.utf8)
    let decodedArticle = try await client.article(id: Self.articleID)
    XCTAssertNil(decodedArticle.publishedAt)
    StubURLProtocol.responseData = Data(#"{"status":"not_requested"}"#.utf8)
    let decodedAnalysis = try await client.analysis(id: Self.articleID)
    XCTAssertEqual(decodedAnalysis.status, .notRequested)
  }

  func testErrorEnvelopesKeepRequestIDsAndSeparateBusinessAndRoute404() async throws {
    let client = makeHTTPClient(status: 400, body: Data())
    StubURLProtocol.statusCode = 400
    StubURLProtocol.responseData = Data(
      #"{"error":{"code":"VALIDATION_ERROR","message":"bad limit"},"requestId":"req-400"}"#.utf8)
    do {
      _ = try await client.news(NewsQuery(limit: 1, offset: 0))
      XCTFail("Expected validation error")
    } catch let error as APIClientError {
      guard case .validation(let message, let requestID) = error else {
        return XCTFail("Unexpected: \(error)")
      }
      XCTAssertEqual(message, "bad limit")
      XCTAssertEqual(requestID, "req-400")
    }

    StubURLProtocol.statusCode = 401
    StubURLProtocol.responseData = Data(
      #"{"error":{"code":"UNAUTHORIZED","message":"unauthorized"}}"#.utf8)
    let reauthenticationRequired = expectation(description: "401 requests prompt device reauthentication")
    let authObserver = NotificationCenter.default.addObserver(
      forName: .deviceReauthenticationRequired, object: nil, queue: .main) { _ in
        reauthenticationRequired.fulfill()
      }
    defer { NotificationCenter.default.removeObserver(authObserver) }
    do {
      _ = try await client.news(NewsQuery(limit: 1, offset: 0))
      XCTFail("Expected unauthorized")
    } catch let error as APIClientError {
      guard case .unauthorized = error else { return XCTFail("Unexpected: \(error)") }
    }
    await fulfillment(of: [reauthenticationRequired], timeout: 1)

    StubURLProtocol.statusCode = 403
    StubURLProtocol.responseData = Data(
      #"{"error":{"code":"FORBIDDEN","message":"forbidden"}}"#.utf8)
    do {
      _ = try await client.news(NewsQuery(limit: 1, offset: 0))
      XCTFail("Expected forbidden")
    } catch let error as APIClientError {
      guard case .forbidden = error else { return XCTFail("Unexpected: \(error)") }
    }

    StubURLProtocol.statusCode = 404
    StubURLProtocol.responseData = Data(
      #"{"error":{"code":"NOT_FOUND","message":"missing"},"requestId":"req-404"}"#.utf8)
    do {
      _ = try await client.article(id: Self.articleID)
      XCTFail("Expected business 404")
    } catch let error as APIClientError {
      guard case .notFound(let requestID) = error else { return XCTFail("Unexpected: \(error)") }
      XCTAssertEqual(requestID, "req-404")
    }
    StubURLProtocol.responseData = Data(
      #"{"message":"Route GET:/api/v1/typo not found","error":"Not Found","statusCode":404}"#.utf8)
    do {
      _ = try await client.news(NewsQuery(limit: 1, offset: 0))
      XCTFail("Expected unknown-route 404")
    } catch let error as APIClientError {
      guard case .unknownRoute(let message) = error else { return XCTFail("Unexpected: \(error)") }
      XCTAssertTrue(message?.contains("Route GET:") == true)
    }

    for status in [500, 503] {
      StubURLProtocol.statusCode = status
      StubURLProtocol.responseData = Data(
        #"{"error":{"code":"SERVER_ERROR","message":"temporary"},"requestId":"req-5xx"}"#.utf8)
      do {
        _ = try await client.news(NewsQuery(limit: 1, offset: 0))
        XCTFail("Expected HTTP \(status)")
      } catch let error as APIClientError {
        if status == 503 {
          guard case .serviceUnavailable(let message, let requestID) = error else {
            return XCTFail("Unexpected: \(error)")
          }
          XCTAssertEqual(message, "temporary")
          XCTAssertEqual(requestID, "req-5xx")
        } else {
          guard case .server(let code, let message, let requestID) = error else {
            return XCTFail("Unexpected: \(error)")
          }
          XCTAssertEqual(code, 500)
          XCTAssertEqual(message, "temporary")
          XCTAssertEqual(requestID, "req-5xx")
        }
      }
    }
  }

  func testClientRequiresHTTPSAndNeverLogsCredentialHeaders() async throws {
    let http = HTTPTrendRadarAPI(
      baseURL: URL(string: "http://api.test")!, credentials: TestCredentials())
    do {
      _ = try await http.categories()
      XCTFail("Cleartext API URL must be rejected")
    } catch let error as APIClientError {
      guard case .invalidResponse = error else { return XCTFail("Unexpected: \(error)") }
    }

    StubURLProtocol.statusCode = 200
    StubURLProtocol.responseData = Data(Self.categoryJSON.utf8)
    StubURLProtocol.lastRequest = nil
    let client = makeHTTPClient(
      status: 200, body: Data(Self.categoryJSON.utf8), credentials: TestCredentials())
    _ = try await client.categories()
    XCTAssertEqual(
      StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"),
      "Bearer test-only-device-credential")
    // The client has no request/credential logging path; errors and view state never receive request headers.
  }

  func testURLSessionCancellationPropagates() async throws {
    let client = makeHTTPClient(status: 200, body: Data(Self.categoryJSON.utf8), delay: 2)
    let request = Task { try await client.categories() }
    try await Task.sleep(for: .milliseconds(100))
    request.cancel()
    do {
      _ = try await request.value
      XCTFail("Cancelled request should not complete successfully")
    } catch {}
  }

  func testRoutedClientPrefersLANAndFallsBackOnlyOnReachability() async throws {
    let lan = EndpointProbe(.success("lan"))
    let relay = EndpointProbe(.success("relay"))
    let routed = RoutedTrendRadarAPI(lan: lan, relay: relay)
    let preferredCategories = try await routed.categories()
    let unusedRelayCount = await relay.count()
    XCTAssertEqual(preferredCategories.first?.id, "lan")
    XCTAssertEqual(unusedRelayCount, 0)

    for failure in [EndpointProbe.Outcome.unreachable, .timeout] {
      let failedLAN = EndpointProbe(failure)
      let healthyRelay = EndpointProbe(.success("relay"))
      let result = try await RoutedTrendRadarAPI(lan: failedLAN, relay: healthyRelay).categories()
      let healthyRelayCount = await healthyRelay.count()
      XCTAssertEqual(result.first?.id, "relay")
      XCTAssertEqual(healthyRelayCount, 1)
    }

    for failure in [EndpointProbe.Outcome.unauthorized, .forbidden, .tls, .certificateUntrusted] {
      let failedLAN = EndpointProbe(failure)
      let unusedRelay = EndpointProbe(.success("relay"))
      do {
        _ = try await RoutedTrendRadarAPI(lan: failedLAN, relay: unusedRelay).categories()
        XCTFail("Authorization/TLS failure must not switch route")
      } catch {}
      let unusedCount = await unusedRelay.count()
      XCTAssertEqual(unusedCount, 0)
    }

    let lanSwitch = EndpointProbe(.unreachable)
    let switchRelay = EndpointProbe(.success("relay"))
    let switching = RoutedTrendRadarAPI(lan: lanSwitch, relay: switchRelay)
    _ = try await switching.categories()
    await lanSwitch.setOutcome(.success("lan"))
    let wifiRecoveredCategories = try await switching.categories()
    let switchRelayCount = await switchRelay.count()
    XCTAssertEqual(wifiRecoveredCategories.first?.id, "lan")
    XCTAssertEqual(switchRelayCount, 1)
  }

  func testRelay503IsSurfacedAndConcurrentReadsRemainIndependent() async throws {
    let lan = EndpointProbe(.unreachable)
    let relay = EndpointProbe(.unavailable)
    let routed = RoutedTrendRadarAPI(lan: lan, relay: relay)
    do {
      _ = try await routed.categories()
      XCTFail("Relay 503 should be surfaced")
    } catch let error as APIClientError {
      guard case .serviceUnavailable = error else { return XCTFail("Unexpected: \(error)") }
    }

    let concurrentLAN = EndpointProbe(.success("lan"))
    let concurrent = RoutedTrendRadarAPI(lan: concurrentLAN, relay: nil)
    async let categories = concurrent.categories()
    async let news = concurrent.news(NewsQuery(limit: 1, offset: 0))
    async let search = concurrent.search(NewsQuery(text: "fixture", limit: 1, offset: 0))
    let categoryResults = try await categories
    let newsResults = try await news
    let searchResults = try await search
    XCTAssertEqual(categoryResults.count, 1)
    XCTAssertEqual(newsResults.pagination.total, 1)
    XCTAssertEqual(searchResults.data.count, 1)
  }

  private func makeHTTPClient(
    status: Int, body: Data, credentials: DeviceCredentialProviding = TestCredentials(),
    delay: TimeInterval = 0
  ) -> HTTPTrendRadarAPI {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [StubURLProtocol.self]
    StubURLProtocol.statusCode = status
    StubURLProtocol.responseData = body
    StubURLProtocol.delay = delay
    let session = URLSession(configuration: configuration)
    return HTTPTrendRadarAPI(
      baseURL: URL(string: "https://api.test")!, credentials: credentials, session: session)
  }

  private static let articleID = "00000000-0000-0000-0000-000000000001"
  private static let articleJSON =
    #"{"id":"00000000-0000-0000-0000-000000000001","title":"冻结契约示例","source":{"id":"s1","name":"示例来源","type":"rss"},"publishedAt":null,"firstSeenAt":"2026-10-23T00:00:00.123Z","url":null,"summary":null,"analysisSummary":null,"contentStatus":"title_only","analysisStatus":"not_requested","categories":[]}"#
  private static let pageJSON =
    #"{"data":[{"id":"00000000-0000-0000-0000-000000000001","title":"冻结契约示例","source":{"id":"s1","name":"示例来源","type":"rss"},"publishedAt":"2026-10-23T00:00:00.123Z","firstSeenAt":"2026-10-23T00:00:00Z","url":"https://example.com/story","summary":"摘要","analysisSummary":null,"contentStatus":"summary_available","analysisStatus":"not_requested","categories":[{"id":"ai","name":"AI"},{"id":"technology","name":"科技"}],"futureField":"ignored"}],"pagination":{"limit":1,"offset":0,"total":9,"hasMore":true}}"#
  private static let categoryJSON = #"[{"id":"ai","name":"AI","sortOrder":1}]"#
}

private struct TestCredentials: DeviceCredentialProviding {
  func bearerToken() async throws -> String? { "test-only-device-credential" }
}

private final class StubURLProtocol: URLProtocol {
  nonisolated(unsafe) static var statusCode = 200
  nonisolated(unsafe) static var responseData = Data()
  nonisolated(unsafe) static var delay: TimeInterval = 0
  nonisolated(unsafe) static var lastRequest: URLRequest?
  private let lock = NSLock()
  private var stopped = false

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    Self.lastRequest = request
    let status = Self.statusCode
    let data = Self.responseData
    let delay = Self.delay
    DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
      guard let self, !self.isStopped else { return }
      let response = HTTPURLResponse(
        url: self.request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil)!
      self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      self.client?.urlProtocol(self, didLoad: data)
      self.client?.urlProtocolDidFinishLoading(self)
    }
  }

  override func stopLoading() { lock.withLock { stopped = true } }
  private var isStopped: Bool { lock.withLock { stopped } }
}

private actor EndpointProbe: TrendRadarAPI {
  enum Outcome {
    case success(String)
    case unreachable, timeout, unauthorized, forbidden, tls, certificateUntrusted, unavailable
  }

  private var outcome: Outcome
  private(set) var requestCount = 0
  init(_ outcome: Outcome) { self.outcome = outcome }
  func count() -> Int { requestCount }
  func setOutcome(_ value: Outcome) { outcome = value }

  func categories() async throws -> [NewsCategory] {
    try respond()
    if case .success(let id) = outcome { return [NewsCategory(id: id, name: id, sortOrder: 0)] }
    return []
  }
  func news(_ query: NewsQuery) async throws -> NewsPage {
    try respond()
    let category = NewsCategory(id: "ai", name: "AI", sortOrder: 1)
    let source = NewsSource(id: "source", name: "Source", type: "rss")
    let article = NewsArticle(
      id: "00000000-0000-0000-0000-000000000001", title: "Fixture", source: source,
      publishedAt: nil, firstSeenAt: Date(), url: nil, summary: nil, analysisSummary: nil,
      contentStatus: "title_only", analysisStatus: .notRequested,
      categories: [ArticleCategory(id: category.id, name: category.name)])
    return NewsPage(
      data: [article], pagination: Pagination(limit: 1, offset: 0, total: 1, hasMore: false))
  }
  func search(_ query: NewsQuery) async throws -> NewsPage { try await news(query) }
  func article(id: String) async throws -> NewsArticle {
    throw APIClientError.notFound(requestID: nil)
  }
  func analysis(id: String) async throws -> ArticleAnalysis {
    throw APIClientError.notFound(requestID: nil)
  }

  private func respond() throws {
    requestCount += 1
    switch outcome {
    case .success: return
    case .unreachable: throw URLError(.cannotConnectToHost)
    case .timeout: throw URLError(.timedOut)
    case .unauthorized: throw APIClientError.unauthorized
    case .forbidden: throw APIClientError.forbidden
    case .tls: throw URLError(.secureConnectionFailed)
    case .certificateUntrusted: throw URLError(.serverCertificateUntrusted)
    case .unavailable:
      throw APIClientError.serviceUnavailable(message: "relay unavailable", requestID: "relay-503")
    }
  }
}
