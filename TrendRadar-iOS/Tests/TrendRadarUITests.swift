import XCTest

final class TrendRadarUITests: XCTestCase {
  override func setUpWithError() throws { continueAfterFailure = false }

  @MainActor
  func testNewsChannelsRefreshAndUncategorizedArticle() throws {
    let app = launch("standard")
    XCTAssertTrue(app.buttons["channel-all"].waitForExistence(timeout: 8))

    for (channel, articleID) in [
      ("channel-ai", "00000000-0000-0000-0000-000000000001"),
      ("channel-technology", "00000000-0000-0000-0000-000000000002"),
      ("channel-finance", "00000000-0000-0000-0000-000000000003"),
      ("channel-society", "00000000-0000-0000-0000-000000000004"),
    ] {
      app.buttons[channel].tap()
      XCTAssertTrue(app.buttons["article-\(articleID)"].waitForExistence(timeout: 4), channel)
    }

    app.buttons["channel-technology"].tap()
    XCTAssertTrue(
      app.staticTexts["published-time-00000000-0000-0000-0000-000000000002"].waitForExistence(
        timeout: 4))
    XCTAssertTrue(app.staticTexts["未提供发布时间"].exists)

    app.buttons["channel-all"].tap()
    let uncategorizedID = "00000000-0000-0000-0000-000000000007"
    let uncategorized = app.buttons["article-\(uncategorizedID)"]
    for _ in 0..<5 where !uncategorized.exists { app.swipeUp() }
    XCTAssertTrue(uncategorized.waitForExistence(timeout: 4))
    XCTAssertTrue(uncategorized.label.contains("未分类"))

    uncategorized.swipeDown()
    let firstArticle = app.buttons["article-00000000-0000-0000-0000-000000000001"]
    XCTAssertTrue(
      firstArticle.waitForExistence(timeout: 5), "Pull to refresh should keep the feed usable")
    attachScreenshot(of: app, named: "home")
  }

  @MainActor
  func testFeedPaginationAndEmptyState() throws {
    let app = launch("pagination")
    XCTAssertTrue(app.buttons["channel-all"].waitForExistence(timeout: 8))
    let lastItem = app.buttons["article-00000000-0000-0000-0000-000000000035"]
    for _ in 0..<8 where !lastItem.exists { app.swipeUp() }
    XCTAssertTrue(
      lastItem.waitForExistence(timeout: 4), "Scrolling should request the next offset page")

    let empty = launch("empty")
    XCTAssertTrue(empty.staticTexts["暂无新闻"].waitForExistence(timeout: 8))
  }

  @MainActor
  func testFeedNetworkErrorAndRetryControl() throws {
    let app = launch("feedError")
    XCTAssertTrue(app.staticTexts["服务暂时不可用，请稍后重试。"].waitForExistence(timeout: 8))
    XCTAssertTrue(app.buttons["retry-button"].exists)
  }

  @MainActor
  func testSearchInputClearNoResultsAndSearchToDetail() throws {
    let app = launch("standard")
    openSearch(in: app)
    let field = app.searchFields.firstMatch
    field.tap()
    field.typeText("大模型")
    let result = app.buttons["article-00000000-0000-0000-0000-000000000001"]
    XCTAssertTrue(result.waitForExistence(timeout: 8))
    result.tap()
    XCTAssertTrue(app.staticTexts["detail-title"].waitForExistence(timeout: 5))
    app.navigationBars.buttons.firstMatch.tap()
    XCTAssertTrue(field.waitForExistence(timeout: 5))
    field.tap()
    clearSearchField(field)
    field.typeText("毫无匹配")
    XCTAssertTrue(
      app.descendants(matching: .any)["search-empty-state"].waitForExistence(timeout: 8))
    field.tap()
    clearSearchField(field)
    XCTAssertTrue(app.staticTexts["搜索新闻"].waitForExistence(timeout: 5))
    attachScreenshot(of: app, named: "search")
  }

  @MainActor
  func testRapidSearchEditsIgnoreLateResponseAndSearchPagination() throws {
    let app = launch("staleSearch")
    openSearch(in: app)
    let field = app.searchFields.firstMatch
    field.tap()
    field.typeText("慢请求")
    Thread.sleep(forTimeInterval: 0.48)
    field.tap()
    clearSearchField(field)
    field.typeText("快速新结果")
    let fastResult = app.buttons.matching(
      identifier: "article-00000000-0000-0000-0000-000000000051"
    ).firstMatch
    XCTAssertTrue(fastResult.waitForExistence(timeout: 8))
    Thread.sleep(forTimeInterval: 1.1)
    XCTAssertTrue(fastResult.exists)
    XCTAssertFalse(app.buttons["article-00000000-0000-0000-0000-000000000050"].exists)

    let paginated = launch("pagination")
    openSearch(in: paginated)
    let searchField = paginated.searchFields.firstMatch
    searchField.tap()
    searchField.typeText("分页")
    let lastResult = paginated.buttons["article-00000000-0000-0000-0000-000000000035"]
    for _ in 0..<8 where !lastResult.exists { paginated.swipeUp() }
    XCTAssertTrue(lastResult.waitForExistence(timeout: 6))
  }

  @MainActor
  func testDetailsNullsAnalysisStatesAndRetry() throws {
    let app = launch("standard")
    XCTAssertTrue(app.buttons["channel-all"].waitForExistence(timeout: 8))
    app.buttons["article-00000000-0000-0000-0000-000000000001"].tap()
    XCTAssertTrue(app.staticTexts["detail-title"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["detail-source"].exists)
    XCTAssertTrue(app.staticTexts["analysis-summary"].exists)
    attachScreenshot(of: app, named: "detail")
    app.navigationBars.buttons.firstMatch.tap()

    app.buttons["channel-technology"].tap()
    app.buttons["article-00000000-0000-0000-0000-000000000002"].tap()
    XCTAssertTrue(app.staticTexts["detail-publication-unavailable"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["detail-summary-unavailable"].exists)
    XCTAssertTrue(app.staticTexts["analysis-not-requested"].exists)
    app.navigationBars.buttons.firstMatch.tap()

    app.buttons["channel-society"].tap()
    app.buttons["article-00000000-0000-0000-0000-000000000004"].tap()
    XCTAssertTrue(app.staticTexts["分析失败：暂时无法获取"].waitForExistence(timeout: 5))

    let flaky = launch("detailFailsOnce")
    XCTAssertTrue(flaky.buttons["channel-all"].waitForExistence(timeout: 8))
    flaky.buttons["article-00000000-0000-0000-0000-000000000001"].tap()
    XCTAssertTrue(flaky.buttons["retry-button"].waitForExistence(timeout: 5))
    flaky.buttons["retry-button"].tap()
    XCTAssertTrue(flaky.staticTexts["detail-title"].waitForExistence(timeout: 5))
  }

  @MainActor
  func testStagingDevicePairingAndKeychainPersistence() throws {
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", "https://192.168.2.117:18443",
    ]
    app.launch()

    let pairingCode = app.secureTextFields["pairing-code"]
    if !pairingCode.waitForExistence(timeout: 3) {
      app.buttons["device-auth-button"].tap()
    }

    let authenticated = app.descendants(matching: .any)["auth-status-valid"]
    if !authenticated.waitForExistence(timeout: 5) {
      guard let code = ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_PAIRING_CODE"],
        !code.isEmpty
      else { throw XCTSkip("Pairing code is supplied only for an unpaired Staging simulator.") }
      XCTAssertTrue(pairingCode.waitForExistence(timeout: 5))
      pairingCode.tap()
      pairingCode.typeText(code)
      let deviceName = app.textFields["device-name"]
      deviceName.tap()
      deviceName.typeText(
        String(repeating: XCUIKeyboardKey.delete.rawValue, count: "TrendRadar iPhone".count))
      deviceName.typeText("Staging Auth Simulator")
      let pairButton = app.buttons["pair-device"]
      XCTAssertTrue(pairButton.isEnabled)
      pairButton.tap()
      XCTAssertTrue(authenticated.waitForExistence(timeout: 12))
    }
    XCTAssertTrue(app.staticTexts["Staging Auth Simulator"].exists)
    app.buttons["auth-dismiss"].tap()
    let article = app.buttons["article-b4e5510a-7cbd-480c-850e-1fe8e5cbb412"]
    XCTAssertTrue(article.waitForExistence(timeout: 12), "Device Bearer should read V1 news")
    XCTAssertTrue(article.label.contains("Staging device auth fixture"))
    article.tap()
    XCTAssertTrue(app.staticTexts["detail-title"].waitForExistence(timeout: 8))
    XCTAssertTrue(app.staticTexts["Staging device auth fixture"].exists)

    app.terminate()
    app.launchArguments = [
      "--live-api", "--lan-base-url", "https://192.168.2.117:18444",
      "--relay-base-url", "https://192.168.2.117:18443/relay",
    ]
    app.launch()
    let relayedArticle = app.buttons["article-b4e5510a-7cbd-480c-850e-1fe8e5cbb412"]
    XCTAssertTrue(relayedArticle.waitForExistence(timeout: 20), "Relay should serve the same V1 article")
    XCTAssertTrue(relayedArticle.label.contains("Staging device auth fixture"))
    relayedArticle.tap()
    XCTAssertTrue(app.staticTexts["detail-title"].waitForExistence(timeout: 8))
    XCTAssertTrue(app.staticTexts["Staging device auth fixture"].exists)

    app.terminate()
    app.launchArguments = [
      "--live-api", "--lan-base-url", "https://192.168.2.117:18443",
    ]
    app.launch()
    XCTAssertTrue(article.waitForExistence(timeout: 12), "Keychain credential should persist")
    app.buttons["device-auth-button"].tap()
    XCTAssertTrue(
      app.descendants(matching: .any)["auth-status-valid"].waitForExistence(timeout: 8))
  }

  @MainActor
  func testStagingRevokedCredentialRequiresReauthentication() throws {
    guard ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_EXPECT_REVOKED"] == "1" else {
      throw XCTSkip("Run only after the paired Staging device is revoked server-side.")
    }
    let app = XCUIApplication()
    if ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_USE_RELAY"] == "1" {
      app.launchArguments = [
        "--live-api", "--lan-base-url", "https://192.168.2.117:18444",
        "--relay-base-url", "https://192.168.2.117:18443/relay",
      ]
    } else {
      app.launchArguments = [
        "--live-api", "--lan-base-url", "https://192.168.2.117:18443",
      ]
    }
    if app.state != .notRunning { app.terminate() }
    app.launch()
    XCTAssertTrue(app.secureTextFields["pairing-code"].waitForExistence(timeout: 12))
    let statuses = ["reauth", "missing", "expired", "valid", "error"].filter {
      app.descendants(matching: .any)["auth-status-\($0)"].exists
    }
    XCTAssertTrue(statuses.contains("reauth"), "Visible device authentication states: \(statuses)")
    XCTAssertTrue(app.buttons["pair-device"].exists)
  }

  @MainActor
  func testStagingRelayOfflineShows503() throws {
    guard ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_EXPECT_RELAY_OFFLINE"] == "1" else {
      throw XCTSkip("Run only while the isolated Staging Relay Agent is stopped.")
    }
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", "https://192.168.2.117:18444",
      "--relay-base-url", "https://192.168.2.117:18443/relay",
    ]
    app.launch()
    XCTAssertTrue(app.staticTexts["Relay agent is not connected"].waitForExistence(timeout: 20))
    XCTAssertTrue(app.buttons["retry-button"].exists)
  }

  @MainActor
  func testStagingRelayScopeDenialShows403() throws {
    guard ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_EXPECT_SCOPE_DENIED"] == "1" else {
      throw XCTSkip("Run only with a read-only Staging device whose news:read scope was removed.")
    }
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", "https://192.168.2.117:18444",
      "--relay-base-url", "https://192.168.2.117:18443/relay",
    ]
    app.launch()
    XCTAssertTrue(app.staticTexts["当前设备无权访问此内容。"].waitForExistence(timeout: 15))
  }

  @MainActor
  private func launch(_ scenario: String) -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments = ["--mock-scenario", scenario]
    app.launch()
    return app
  }

  @MainActor
  private func openSearch(in app: XCUIApplication) {
    let searchTab = app.tabBars.buttons["搜索"]
    XCTAssertTrue(searchTab.waitForExistence(timeout: 5))
    searchTab.tap()
    XCTAssertTrue(app.searchFields.firstMatch.waitForExistence(timeout: 5))
  }

  @MainActor
  private func clearSearchField(_ field: XCUIElement) {
    let currentValue = (field.value as? String) ?? ""
    let length = currentValue == "搜索新闻" ? 0 : currentValue.count
    if length > 0 {
      field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: length))
    }
  }

  @MainActor
  private func attachScreenshot(of app: XCUIApplication, named name: String) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }
}
