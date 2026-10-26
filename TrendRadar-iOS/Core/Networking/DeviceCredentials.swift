import Foundation
import Security

struct DeviceCredential: Codable, Equatable, Sendable {
  let accessToken: String
  let deviceID: String?
  let deviceName: String?
  let expiresAt: Date
  let scopes: [String]

  init(
    accessToken: String,
    expiresAt: Date,
    deviceID: String? = nil,
    deviceName: String? = nil,
    scopes: [String] = []
  ) {
    self.accessToken = accessToken
    self.deviceID = deviceID
    self.deviceName = deviceName
    self.expiresAt = expiresAt
    self.scopes = scopes
  }

  private enum CodingKeys: String, CodingKey {
    case accessToken
    case deviceID = "deviceId"
    case deviceName
    case expiresAt
    case scopes
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    accessToken = try container.decode(String.self, forKey: .accessToken)
    deviceID = try container.decodeIfPresent(String.self, forKey: .deviceID)
    deviceName = try container.decodeIfPresent(String.self, forKey: .deviceName)
    expiresAt = try container.decode(Date.self, forKey: .expiresAt)
    scopes = try container.decodeIfPresent([String].self, forKey: .scopes) ?? []
  }
}

protocol DeviceCredentialProviding: Sendable {
  func bearerToken() async throws -> String?
}

protocol DeviceCredentialStoring: DeviceCredentialProviding {
  func store(_ credential: DeviceCredential) async throws
  func credential() async throws -> DeviceCredential?
  func revoke() async throws
}

/// Device-scoped Keychain slot. It stores no shared backend token and does not provision credentials.
struct KeychainDeviceCredentials: DeviceCredentialStoring {
  private let service: String
  private let account = "device-access-credential"

  init(service: String = "com.trendradar.ios.device-credential") {
    self.service = service
  }

  func store(_ credential: DeviceCredential) async throws {
    let data = try JSONEncoder().encode(credential)
    let query = keychainQuery
    let update: [String: Any] = [
      kSecValueData as String: data,
      kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
    ]
    let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
    if status == errSecItemNotFound {
      var insertion = query
      insertion[kSecValueData as String] = data
      insertion[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
      let addStatus = SecItemAdd(insertion as CFDictionary, nil)
      guard addStatus == errSecSuccess else {
        throw NSError(domain: "TrendRadar.Keychain", code: Int(addStatus))
      }
    } else if status != errSecSuccess {
      throw NSError(domain: "TrendRadar.Keychain", code: Int(status))
    }
  }

  func revoke() async throws {
    let status = SecItemDelete(keychainQuery as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw NSError(domain: "TrendRadar.Keychain", code: Int(status))
    }
  }

  func bearerToken() async throws -> String? {
    guard let credential = try await credential() else { return nil }
    guard credential.expiresAt > Date() else { throw APIClientError.credentialExpired }
    return credential.accessToken
  }

  func credential() async throws -> DeviceCredential? {
    var query = keychainQuery
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess, let data = result as? Data else {
      throw NSError(domain: "TrendRadar.Keychain", code: Int(status))
    }
    return try JSONDecoder().decode(DeviceCredential.self, from: data)
  }

  private var keychainQuery: [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }
}
