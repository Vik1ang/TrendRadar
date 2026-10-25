# TrendRadar iOS MVP

Native SwiftUI reader targeting iOS 17+, built with Xcode 27 / Swift 6.4 (Swift language mode 5). No third-party runtime dependencies.

## Open and run

Open `TrendRadar.xcodeproj`, select the shared `TrendRadar` scheme, then run on an iOS Simulator. The app is intentionally wired to `MockTrendRadarAPI` until trusted LAN HTTPS and revocable device credentials are provisioned.

CLI build and tests:

```sh
xcodebuild test -project TrendRadar.xcodeproj -scheme TrendRadar \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' \
  -derivedDataPath /tmp/TrendRadarDerived CODE_SIGNING_ALLOWED=NO
```

## Included

- News feed with server-provided categories, pull-to-refresh, offset pagination, and publication-date fallback to `firstSeenAt` only for display.
- Server-backed search endpoint model, debounced/cancellable query changes, pagination, and no-results UI.
- Article details and persisted AI-analysis status; opening a detail only performs GET reads and never requests inference.
- V1 Codable models, typed 400/401/404/500/503 error handling, HTTPS-only URLSession client, Keychain device-credential slot, and constrained LAN-first / HTTPS relay fallback routing.
- Mock data covers null publication/summary, multiple categories, empty categories, analysis statuses, and sample generated analysis.
- Frozen API contract documents and OpenAPI snapshot are included under `Resources/docs/ios/`.

## Safety and remaining integration work

The app bundles no shared server token or production endpoint. HTTP endpoints are rejected. The routed client retries through the relay only on reachability failures; authorization (401/403) and TLS errors are not fallback conditions. The backend pairing contract and local issue/list/revoke CLI are implemented but not deployed; a trusted LAN certificate/proxy, coordinated backend/Worker rollout, isolated staging lifecycle tests, and real-device network-switch verification are still required. The app runs Mock mode unless explicitly configured with launch arguments.

## Explicit staging/LAN mode

Mock mode remains the default. To opt into the device-authenticated client, pass only trusted HTTPS endpoints at launch:

```sh
--live-api \
--lan-base-url https://lan.example.test \
--relay-base-url https://relay.example.test
```

Open **设备认证** in the app to enter a one-time pairing code. The pairing request is `POST /api/v2/devices/pair` with `Authorization: Pairing <code>`; the code is not put in a URL or logged. A successful `trd_dev_` credential is stored in the device-only Keychain, and local logout deletes it. API v2 deliberately has no credential refresh endpoint; operator revocation uses the backend CLI and the client must pair again after expiry/revocation.
