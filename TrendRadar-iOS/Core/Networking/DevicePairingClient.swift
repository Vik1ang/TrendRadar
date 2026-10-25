import Foundation

struct DevicePairingResponse: Codable, Equatable, Sendable {
  let deviceID: String
  let deviceName: String
  let accessToken: String
  let expiresAt: Date
  let scopes: [String]

  private enum CodingKeys: String, CodingKey {
    case deviceID = "deviceId"
    case deviceName
    case accessToken
    case expiresAt
    case scopes
  }
}

struct DevicePairingRequest: Encodable, Equatable, Sendable {
  let deviceName: String
}

/// Exchanges a one-time pairing code for a device-scoped credential.
/// The code is sent only in the HTTPS Authorization header and never becomes URL data.
struct DevicePairingClient: Sendable {
  let baseURL: URL
  let session: URLSession

  init(baseURL: URL, session: URLSession = .shared) {
    self.baseURL = baseURL
    self.session = session
  }

  func pair(code: String, deviceName: String) async throws -> DeviceCredential {
    guard baseURL.scheme?.lowercased() == "https",
      !code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
      !deviceName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else { throw APIClientError.invalidResponse }

    let url = baseURL.appending(path: "/api/v2/devices/pair")
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("Pairing " + code, forHTTPHeaderField: "Authorization")
    request.httpBody = try JSONEncoder().encode(
      DevicePairingRequest(deviceName: deviceName.trimmingCharacters(in: .whitespacesAndNewlines)))

    let (data, response) = try await session.data(for: request)
    guard let response = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
    guard (200..<300).contains(response.statusCode) else {
      throw decodePairingError(data, status: response.statusCode)
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
          in: container, debugDescription: "Invalid RFC 3339 expiration timestamp")
      }
      let pairing = try decoder.decode(DevicePairingResponse.self, from: data)
      let validToken = pairing.accessToken.range(
        of: #"^trd_dev_[A-Za-z0-9_-]{43}$"#, options: .regularExpression) != nil
      guard validToken,
        UUID(uuidString: pairing.deviceID) != nil,
        !pairing.deviceName.isEmpty,
        pairing.expiresAt > Date(),
        pairing.scopes == ["news:read"]
      else { throw APIClientError.invalidResponse }
      return DeviceCredential(
        accessToken: pairing.accessToken,
        expiresAt: pairing.expiresAt,
        deviceID: pairing.deviceID,
        deviceName: pairing.deviceName,
        scopes: pairing.scopes)
    } catch let error as APIClientError {
      throw error
    } catch {
      throw APIClientError.invalidResponse
    }
  }

  private func decodePairingError(_ data: Data, status: Int) -> APIClientError {
    let body = try? JSONDecoder().decode(APIErrorBody.self, from: data)
    switch status {
    case 400:
      return .validation(
        message: body?.error.message ?? "配对请求无效。", requestID: body?.requestId)
    case 401:
      return .unauthorized
    case 403:
      return .forbidden
    case 404:
      return .unknownRoute(nil)
    case 503:
      return .serviceUnavailable(message: body?.error.message, requestID: body?.requestId)
    case 500...599:
      return .server(status: status, message: body?.error.message, requestID: body?.requestId)
    default:
      return .unexpectedStatus(status)
    }
  }
}
