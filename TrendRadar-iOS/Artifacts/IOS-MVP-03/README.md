# IOS-MVP-03 Simulator screenshots

Captured on iPhone 17 / iOS 27.0 using staging-only XCTest UI flows and real Staging news data. The pairing-code value and Bearer credential are not present in these images. The fixture intentionally has no publication time, no summary, and no categories; it reports `not_requested` analysis.

- `home-lan.png`, `search-lan.png`, `detail-lan.png`: LAN HTTPS.
- `home-relay-fallback.png`, `detail-relay.png`: automatic route after the configured LAN URL was unreachable.
- `home-relay-forced.png`, `detail-relay-forced.png`: explicit forced Relay route (A), separate from automatic fallback (B).
- `home-lan-recovered.png`: LAN preferred again after restoring LAN endpoint.
- `auth-paired.png`: Keychain-backed paired state after successful UI pairing.
- `auth-reauth.png`: reauthentication UI after server-side device revocation (401).

The Simulator shares the Mac network; this evidence does not prove Wi-Fi-to-cellular transitions on a physical iPhone.
