import XCTest
import UIKit

final class TrendRadarUITests: XCTestCase {
  private static let stagingLANURL = "https://192.168.2.117:18443"
  private static let stagingRelayURL =
    "https://trendradar-intelligence-relay-staging.vik1ang.workers.dev"
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
  func testFeedRestoresAfterBackgroundWithoutDuplicatingItems() throws {
    let app = launch("standard")
    let firstArticle = app.buttons["article-00000000-0000-0000-0000-000000000001"]
    XCTAssertTrue(firstArticle.waitForExistence(timeout: 8))
    let tabBar = app.tabBars.firstMatch
    XCTAssertTrue(tabBar.exists)
    XCTAssertLessThanOrEqual(tabBar.frame.maxY, app.frame.maxY)
    XCTAssertTrue(app.tabBars.buttons["搜索"].isHittable)

    XCUIDevice.shared.press(.home)
    app.activate()
    XCTAssertTrue(firstArticle.waitForExistence(timeout: 8))
    XCTAssertEqual(
      app.buttons.matching(identifier: "article-00000000-0000-0000-0000-000000000001").count,
      1, "Returning from background must not duplicate feed rows")
  }

  @MainActor
  func testChineseLocaleDatesAndNullableArticleFields() throws {
    let app = XCUIApplication()
    app.launchArguments = ["--mock-scenario", "standard", "--enable-test-locale-injection",
      "--test-locale", "zh_CN"]
    app.launch()
    let article = app.buttons["article-00000000-0000-0000-0000-000000000001"]
    XCTAssertTrue(article.waitForExistence(timeout: 8))
    XCTAssertTrue(app.tabBars.buttons["搜索"].isHittable)
    XCTAssertTrue(app.buttons["channel-technology"].exists)
    app.buttons["article-00000000-0000-0000-0000-000000000002"].tap()
    XCTAssertTrue(app.staticTexts["detail-title"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["detail-publication-unavailable"].exists)
    XCTAssertTrue(app.staticTexts["detail-summary-unavailable"].exists)
    XCTAssertTrue(app.staticTexts["analysis-not-requested"].exists)
    let collectionDate = app.staticTexts["detail-first-seen-at"].label
    XCTAssertTrue(collectionDate.contains("月"), "Expected Chinese month marker in: \(collectionDate)")
  }

  @MainActor
  func testTestOnlyPairingPasteKeepsSecretMasked() throws {
    guard let path = ProcessInfo.processInfo.environment["IOS_AUTH_TEST_PAIRING_CODE_FILE"],
      let code = try? String(contentsOfFile: path, encoding: .utf8)
        .trimmingCharacters(in: .whitespacesAndNewlines), !code.isEmpty else {
      throw XCTSkip("Requires a protected local file path in the ephemeral test plan.")
    }
    let app = XCUIApplication()
    app.launchArguments = ["--live-api", "--lan-base-url", "https://127.0.0.1:1"]
    app.launch()
    XCTAssertTrue(app.buttons["device-auth-button"].waitForExistence(timeout: 8))
    app.buttons["device-auth-button"].tap()
    let codeField = app.secureTextFields["pairing-code"]
    XCTAssertTrue(codeField.waitForExistence(timeout: 5))
    UIPasteboard.general.string = code
    codeField.press(forDuration: 1.0)
    let pasteItem = app.menuItems.firstMatch
    XCTAssertTrue(pasteItem.waitForExistence(timeout: 5))
    pasteItem.tap()
    UIPasteboard.general.items = []
    let pairButton = app.buttons["pair-device"]
    XCTAssertTrue(pairButton.isEnabled)
    XCTAssertFalse((codeField.value as? String ?? "").isEmpty)
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
  func testStagingHostedRelayDirectRead() throws {
    try requireStaging()
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", Self.stagingLANURL, "--relay-base-url", Self.stagingRelayURL,
      "--relay-proxy-url", "http://127.0.0.1:10801", "--force-relay",
    ]
    app.launch()
    let article = app.buttons["article-b4e5510a-7cbd-480c-850e-1fe8e5cbb412"]
    if !article.waitForExistence(timeout: 20) {
      let visible = app.staticTexts.allElementsBoundByIndex.map(\.label).joined(separator: " | ")
      XCTFail("Forced hosted Relay read failed; visible UI: \(visible)")
    }
    XCTAssertTrue(article.label.contains("Staging device auth fixture"))
    attachScreenshot(of: app, named: "ios-mvp-03-home-hosted-relay-forced")
    article.tap()
    XCTAssertTrue(app.staticTexts["detail-title"].waitForExistence(timeout: 8))
    XCTAssertTrue(app.staticTexts["Staging device auth fixture"].exists)
    attachScreenshot(of: app, named: "ios-mvp-03-detail-hosted-relay-forced")
  }

  @MainActor
  func testStagingPairingUsesSecurePasteWithoutTypingSecret() throws {
    guard ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_PAIRING_TEST"] == "1",
      let path = ProcessInfo.processInfo.environment["IOS_AUTH_TEST_PAIRING_CODE_FILE"]
    else {
      throw XCTSkip("Requires a protected local file path in the ephemeral test plan.")
    }
    let code = try String(contentsOfFile: path, encoding: .utf8)
      .trimmingCharacters(in: .whitespacesAndNewlines)
    XCTAssertFalse(code.isEmpty)
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", Self.stagingLANURL,
      "--relay-base-url", Self.stagingRelayURL,
      "--relay-proxy-url", "http://127.0.0.1:10801",
    ]
    app.launch()
    let pairingCode = app.secureTextFields["pairing-code"]
    XCTAssertTrue(pairingCode.waitForExistence(timeout: 15))
    UIPasteboard.general.string = code
    pairingCode.press(forDuration: 1.0)
    let pasteItem = app.menuItems.firstMatch
    XCTAssertTrue(pasteItem.waitForExistence(timeout: 5))
    pasteItem.tap()
    UIPasteboard.general.items = []
    let deviceName = app.textFields["device-name"]
    XCTAssertTrue(deviceName.waitForExistence(timeout: 5))
    deviceName.tap()
    let nameLength = ((deviceName.value as? String) ?? "").count
    if nameLength > 0 {
      deviceName.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: nameLength))
    }
    deviceName.typeText("IOS-MVP-04 Simulator")
    let pairButton = app.buttons["pair-device"]
    XCTAssertTrue(pairButton.isEnabled)
    pairButton.tap()
    let fixture = app.buttons["article-b4e5510a-7cbd-480c-850e-1fe8e5cbb412"]
    if !fixture.waitForExistence(timeout: 25) {
      let visible = app.staticTexts.allElementsBoundByIndex.map(\.label).joined(separator: " | ")
      XCTFail("Pairing completed without the expected Staging article; visible UI: \(visible)")
    }
    app.terminate()
    app.launch()
    XCTAssertTrue(fixture.waitForExistence(timeout: 20), "Keychain credential should survive relaunch")
    fixture.tap()
    XCTAssertTrue(app.staticTexts["detail-title"].waitForExistence(timeout: 8))
    XCTAssertTrue(app.staticTexts["Staging device auth fixture"].exists)
  }

  @MainActor
  func testStagingRevokedCredentialRequiresReauthentication() throws {
    try requireStaging()
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", Self.stagingLANURL,
      "--relay-base-url", Self.stagingRelayURL,
      "--relay-proxy-url", "http://127.0.0.1:10801",
    ]
    if app.state != .notRunning { app.terminate() }
    app.launch()
    XCTAssertTrue(app.secureTextFields["pairing-code"].waitForExistence(timeout: 12))
    let statuses = ["reauth", "missing", "expired", "valid", "error"].filter {
      app.descendants(matching: .any)["auth-status-\($0)"].exists
    }
    XCTAssertTrue(statuses.contains("reauth"), "Visible device authentication states: \(statuses)")
    XCTAssertTrue(app.buttons["pair-device"].exists)
    attachScreenshot(of: app, named: "ios-mvp-03-auth-reauth")
    app.buttons["auth-logout"].tap()
    XCTAssertTrue(app.descendants(matching: .any)["auth-status-missing"].waitForExistence(timeout: 5))
  }

  @MainActor
  func testStagingRelayOfflineShows503() throws {
    try requireStaging()
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", Self.stagingLANURL,
      "--relay-base-url", Self.stagingRelayURL, "--relay-proxy-url", "http://127.0.0.1:10801",
    ]
    app.launch()
    XCTAssertTrue(app.staticTexts["Relay agent is not connected"].waitForExistence(timeout: 20))
    XCTAssertTrue(app.buttons["retry-button"].exists)
  }

  @MainActor
  func testStagingScopeDenialShows403() throws {
    guard ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_ENABLED"] == "1",
      ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_EXPECT_SCOPE_DENIED"] == "1" else {
      throw XCTSkip("Run only with a read-only Staging device whose news:read scope was removed.")
    }
    let app = XCUIApplication()
    app.launchArguments = [
      "--live-api", "--lan-base-url", Self.stagingLANURL,
      "--relay-base-url", Self.stagingRelayURL, "--relay-proxy-url", "http://127.0.0.1:10801",
    ]
    app.launch()
    let denied = app.staticTexts["当前设备无权访问此内容。"]
    if !denied.waitForExistence(timeout: 15) {
      let visible = app.staticTexts.allElementsBoundByIndex.map(\.label).joined(separator: " | ")
      XCTFail("Staging scope-denial UI did not appear; visible: \(visible)")
    }
  }

  private func requireStaging() throws {
    guard ProcessInfo.processInfo.environment["IOS_AUTH_STAGING_ENABLED"] == "1" else {
      throw XCTSkip("Staging network tests are opt-in; no staging credential is configured.")
    }
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
