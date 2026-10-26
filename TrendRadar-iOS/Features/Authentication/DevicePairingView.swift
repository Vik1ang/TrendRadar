import SwiftUI

enum DeviceAuthState: Equatable {
  case loading
  case missing
  case valid(DeviceCredential)
  case expired
  case reauthenticationRequired
  case pairing
  case error(String)
}

@MainActor
@Observable
final class DeviceAuthModel {
  private let credentials: any DeviceCredentialStoring
  private let pairingClient: DevicePairingClient?
  private var requiresReauthentication: Bool
  var state: DeviceAuthState = .loading
  var pairingCode = ""
  var deviceName = "TrendRadar iPhone"

  init(baseURL: URL?, credentials: any DeviceCredentialStoring, requiresReauthentication: Bool = false) {
    self.credentials = credentials
    pairingClient = baseURL.map { DevicePairingClient(baseURL: $0) }
    self.requiresReauthentication = requiresReauthentication
  }

  func load() async {
    do {
      guard let credential = try await credentials.credential() else {
        state = requiresReauthentication ? .reauthenticationRequired : .missing
        return
      }
      if credential.expiresAt <= Date() {
        state = .expired
      } else {
        state = requiresReauthentication ? .reauthenticationRequired : .valid(credential)
      }
    } catch {
      state = .error(error.localizedDescription)
    }
  }

  func pair() async {
    guard let pairingClient else {
      state = .error("未配置 LAN HTTPS 配对地址。")
      return
    }
    state = .pairing
    do {
      let credential = try await pairingClient.pair(code: pairingCode, deviceName: deviceName)
      try await credentials.store(credential)
      pairingCode = ""
      requiresReauthentication = false
      state = .valid(credential)
      NotificationCenter.default.post(name: .deviceAuthenticationRestored, object: nil)
    } catch let error as APIClientError {
      if case .unauthorized = error {
        state = .reauthenticationRequired
      } else {
        state = .error(error.localizedDescription)
      }
    } catch {
      state = .error(error.localizedDescription)
    }
  }

  func logout() async {
    do {
      try await credentials.revoke()
      requiresReauthentication = false
      state = .missing
    } catch {
      state = .error(error.localizedDescription)
    }
  }

  func markReauthenticationRequired() {
    requiresReauthentication = true
    state = .reauthenticationRequired
  }

  var canPair: Bool {
    pairingClient != nil
      && !pairingCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      && !deviceName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      && state != .pairing
  }
}

struct DevicePairingView: View {
  @Environment(\.dismiss) private var dismiss
  private let requiresReauthentication: Bool
  @State private var model: DeviceAuthModel

  init(
    baseURL: URL?,
    credentials: any DeviceCredentialStoring = KeychainDeviceCredentials(),
    requiresReauthentication: Bool = false
  ) {
    self.requiresReauthentication = requiresReauthentication
    _model = State(initialValue: DeviceAuthModel(
      baseURL: baseURL, credentials: credentials,
      requiresReauthentication: requiresReauthentication))
  }

  var body: some View {
    NavigationStack {
      Form {
        statusSection
        pairingSection
      }
      .navigationTitle("设备认证")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button("完成") { dismiss() }
            .accessibilityIdentifier("auth-dismiss")
        }
      }
      .task { await model.load() }
      .onReceive(
        NotificationCenter.default.publisher(for: .deviceReauthenticationRequired)
          .receive(on: DispatchQueue.main)
      ) { _ in
        model.markReauthenticationRequired()
      }
    }
  }

  @ViewBuilder
  private var statusSection: some View {
    Section("当前设备") {
      switch model.state {
      case .loading:
        ProgressView("读取设备凭据…")
      case .missing:
        Label("未配对", systemImage: "iphone.slash")
          .accessibilityIdentifier("auth-status-missing")
      case .expired:
        Label("凭据已过期，需要重新认证", systemImage: "exclamationmark.shield")
          .foregroundStyle(.orange)
          .accessibilityIdentifier("auth-status-expired")
      case .reauthenticationRequired:
        Label("认证失效，需要重新配对", systemImage: "lock.trianglebadge.exclamationmark")
          .foregroundStyle(.orange)
          .accessibilityIdentifier("auth-status-reauth")
        Button("删除本机凭据", role: .destructive) {
          Task { await model.logout() }
        }
        .accessibilityIdentifier("auth-logout")
      case .pairing:
        ProgressView("正在配对…")
      case .error(let message):
        Label(message, systemImage: "exclamationmark.triangle")
          .foregroundStyle(.red)
          .accessibilityIdentifier("auth-status-error")
      case .valid(let credential):
        VStack(alignment: .leading, spacing: 6) {
          Label(credential.deviceName ?? "已配对设备", systemImage: "checkmark.shield")
          Text("有效期至 \(credential.expiresAt.formatted(date: .abbreviated, time: .shortened))")
            .font(.footnote).foregroundStyle(.secondary)
          if !credential.scopes.isEmpty {
            Text("权限：\(credential.scopes.joined(separator: ", "))")
              .font(.footnote).foregroundStyle(.secondary)
          }
        }
        .accessibilityIdentifier("auth-status-valid")
        Button("退出并删除本机凭据", role: .destructive) {
          Task { await model.logout() }
        }
        .accessibilityIdentifier("auth-logout")
      }
    }
  }

  private var pairingSection: some View {
    Section {
      SecureField("一次性配对码", text: $model.pairingCode)
        .textContentType(.oneTimeCode)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .accessibilityIdentifier("pairing-code")
      TextField("设备名称", text: $model.deviceName)
        .textInputAutocapitalization(.words)
        .accessibilityIdentifier("device-name")
      Button {
        Task { await model.pair() }
      } label: {
        if model.state == .pairing {
          ProgressView()
        } else {
          Text("使用配对码")
        }
      }
      .disabled(!model.canPair)
      .accessibilityIdentifier("pair-device")
      Text("配对码只通过已配置的 LAN HTTPS 地址发送，不会写入 URL、日志或截图。")
        .font(.footnote).foregroundStyle(.secondary)
    } header: {
      Text("一次性设备配对")
    } footer: {
      Text("设备凭据仅用于新闻读取；没有刷新接口。凭据失效后请重新配对。")
    }
  }
}
