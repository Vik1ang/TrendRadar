import SwiftUI

@main
struct TrendRadarApp: App {
  private let api: any TrendRadarAPI
  private let credentials: KeychainDeviceCredentials
  private let pairingBaseURL: URL?

  init() {
    let keychain = KeychainDeviceCredentials()
    credentials = keychain
    let configuration = APIConfiguration.from(ProcessInfo.processInfo.arguments)
    pairingBaseURL = configuration.lanBaseURL
    let scenario = MockScenario.launchArguments(ProcessInfo.processInfo.arguments)
    if configuration.isLive, let lanBaseURL = configuration.lanBaseURL {
      let lan = HTTPTrendRadarAPI(baseURL: lanBaseURL, credentials: keychain)
      let relay = configuration.relayBaseURL.flatMap { relayBaseURL -> HTTPTrendRadarAPI? in
        HTTPTrendRadarAPI(baseURL: relayBaseURL, credentials: keychain)
      }
      api = RoutedTrendRadarAPI(lan: lan, relay: relay)
    } else {
      api = MockTrendRadarAPI(scenario: scenario)
    }
  }

  var body: some Scene {
    WindowGroup {
      RootTabView(api: api, pairingBaseURL: pairingBaseURL, credentials: credentials)
    }
  }
}

struct APIConfiguration: Equatable {
  let isLive: Bool
  let lanBaseURL: URL?
  let relayBaseURL: URL?

  static func from(_ arguments: [String]) -> APIConfiguration {
    guard arguments.contains("--live-api"),
      let lan = value(after: "--lan-base-url", in: arguments),
      let lanURL = URL(string: lan),
      lanURL.scheme?.lowercased() == "https"
    else {
      return APIConfiguration(isLive: false, lanBaseURL: nil, relayBaseURL: nil)
    }
    let relayURL = value(after: "--relay-base-url", in: arguments).flatMap(URL.init)
    let safeRelay = relayURL?.scheme?.lowercased() == "https" ? relayURL : nil
    return APIConfiguration(isLive: true, lanBaseURL: lanURL, relayBaseURL: safeRelay)
  }

  private static func value(after flag: String, in arguments: [String]) -> String? {
    guard let index = arguments.firstIndex(of: flag), arguments.indices.contains(index + 1)
    else { return nil }
    return arguments[index + 1]
  }
}

struct RootTabView: View {
  let api: any TrendRadarAPI
  let pairingBaseURL: URL?
  let credentials: any DeviceCredentialStoring
  @State private var isShowingDeviceAuth = false
  @State private var requiresReauthentication = false

  init(
    api: any TrendRadarAPI,
    pairingBaseURL: URL? = nil,
    credentials: any DeviceCredentialStoring = KeychainDeviceCredentials()
  ) {
    self.api = api
    self.pairingBaseURL = pairingBaseURL
    self.credentials = credentials
  }

  var body: some View {
    TabView {
      NewsFeedView(api: api).tabItem { Label("新闻", systemImage: "newspaper") }
      SearchView(api: api).tabItem { Label("搜索", systemImage: "magnifyingglass") }
    }
    .tint(.primary)
    .toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Button {
          isShowingDeviceAuth = true
        } label: {
          Label("设备认证", systemImage: "person.badge.key")
        }
        .accessibilityIdentifier("device-auth-button")
      }
    }
    .sheet(isPresented: $isShowingDeviceAuth) {
      DevicePairingView(
        baseURL: pairingBaseURL,
        credentials: credentials,
        requiresReauthentication: requiresReauthentication)
    }
    .onReceive(NotificationCenter.default.publisher(for: .deviceReauthenticationRequired)) { _ in
      requiresReauthentication = true
      isShowingDeviceAuth = true
    }
    .onReceive(NotificationCenter.default.publisher(for: .deviceAuthenticationRestored)) { _ in
      requiresReauthentication = false
    }
  }
}
