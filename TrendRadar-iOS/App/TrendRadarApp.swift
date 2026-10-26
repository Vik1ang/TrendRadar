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
      if configuration.forceRelay, let relayBaseURL = configuration.relayBaseURL {
        api = HTTPTrendRadarAPI(
          baseURL: relayBaseURL, credentials: keychain, requestTimeout: 20,
          proxyURL: configuration.relayProxyURL)
      } else {
        let lan = HTTPTrendRadarAPI(
          baseURL: lanBaseURL, credentials: keychain, requestTimeout: 5)
        let relay = configuration.relayBaseURL.flatMap { relayBaseURL -> HTTPTrendRadarAPI? in
          HTTPTrendRadarAPI(
            baseURL: relayBaseURL, credentials: keychain, requestTimeout: 20,
            proxyURL: configuration.relayProxyURL)
        }
        api = RoutedTrendRadarAPI(lan: lan, relay: relay)
      }
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
  let relayProxyURL: URL?
  let forceRelay: Bool

  static func from(_ arguments: [String]) -> APIConfiguration {
    guard arguments.contains("--live-api"),
      let lan = value(after: "--lan-base-url", in: arguments),
      let lanURL = URL(string: lan),
      lanURL.scheme?.lowercased() == "https"
    else {
      return APIConfiguration(
        isLive: false, lanBaseURL: nil, relayBaseURL: nil, relayProxyURL: nil, forceRelay: false)
    }
    let relayURL = value(after: "--relay-base-url", in: arguments).flatMap(URL.init)
    let safeRelay = relayURL?.scheme?.lowercased() == "https" ? relayURL : nil
    let proxyURL = value(after: "--relay-proxy-url", in: arguments).flatMap(URL.init)
    let safeProxy = proxyURL.flatMap { proxy in
      proxy.scheme?.lowercased() == "http" && proxy.host != nil && proxy.user == nil
        && proxy.password == nil ? proxy : nil
    }
    return APIConfiguration(
      isLive: true, lanBaseURL: lanURL, relayBaseURL: safeRelay,
      relayProxyURL: safeProxy, forceRelay: arguments.contains("--force-relay") && safeRelay != nil)
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
  @State private var deviceAuthPresentation: DeviceAuthPresentation?

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
      NewsFeedView(api: api, deviceAuthPresentation: $deviceAuthPresentation)
        .tabItem { Label("新闻", systemImage: "newspaper") }
      SearchView(api: api).tabItem { Label("搜索", systemImage: "magnifyingglass") }
    }
    .tint(.primary)
    .sheet(item: $deviceAuthPresentation) { presentation in
      DevicePairingView(
        baseURL: pairingBaseURL,
        credentials: credentials,
        requiresReauthentication: presentation == .reauthentication)
    }
    .onReceive(
      NotificationCenter.default.publisher(for: .deviceReauthenticationRequired)
        .receive(on: DispatchQueue.main)
    ) { _ in
      deviceAuthPresentation = .reauthentication
    }
  }
}

enum DeviceAuthPresentation: String, Identifiable {
  case manual
  case reauthentication

  var id: String { rawValue }
}
