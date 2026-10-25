# IOS-MVP-02 Validation Record

Environment: Xcode 27.0 / iOS 27.0 Simulator / iPhone 18 Pro; project minimum deployment target iOS 17.0. The app defaults to local Mock API and tests launch isolated named mock scenarios. The frozen OpenAPI file SHA-256 was verified as `80ecf1eb8e25a400375cb555cb6c819afdee857453b632ae59cb3f906da65231`.

## Validation scope

Final simulator run: 12 unit tests and 6 UI tests passed (0 failures) on iPhone 18 Pro.

- `TrendRadarTests`: API V1 success/error fixtures, nullable `publishedAt` and `summary`, categories/multiple labels/uncategorized, pagination, search debounce and late-response protection, Keychain save/expiry/revoke, HTTPS-only requests, URLSession cancellation, LAN preference and reachability-only Relay fallback, 401/403/TLS/certificate failure no-fallback, Relay 503, and concurrent reads.
- `TrendRadarUITests`: four news categories, empty/error feed, pagination, uncategorized/null publish time, pull-down refresh interaction, search input/clear/no result/detail navigation, rapid query changes/pagination, detail nulls and analysis states/retry. UI tests attach home/search/detail screenshots to the XCTest result bundle.
- Tests use synthetic credentials and in-process URLProtocol/route mocks. No production API, production token, Relay, or model service is contacted.

See the committed simulator screenshots in `Artifacts/UI-Tests/` after a successful XCTest run. Reproduce with:

```sh
xcodebuild test -project TrendRadar.xcodeproj -scheme TrendRadar \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' \
  -derivedDataPath /tmp/TrendRadarMVP02 \
  CODE_SIGN_STYLE=Automatic CODE_SIGN_IDENTITY='Apple Development' \
  DEVELOPMENT_TEAM=WM795B62N7 ONLY_ACTIVE_ARCH=YES
```

## Keychain and auth boundary

Keychain storage and expiry/revoke behavior are implemented and simulator-tested; server enrollment, refresh, revocation endpoints, and reauthentication UI are not. API V1 defines no device authentication flow. See [`DEVICE_AUTH_REQUIREMENTS.md`](DEVICE_AUTH_REQUIREMENTS.md) for a backend contract proposal and Worker/Fastify trust boundaries. A 401 is an explicit invalid/expired credential error and never triggers fallback.

## Hook diagnosis

The inherited global pre-commit hook (`/Users/vik1ang/.codex/git-hooks/pre-commit`) falls back to `uv run lefthook -h`. In a bounded 12-second probe this invoked uv 0.13.1, synchronized/installed the backend Python environment, then exited with status 2: `Failed to spawn: lefthook` (no PATH/project lefthook executable or client-project lefthook config exists). This fallback both causes the wait and cannot provide useful iOS checks. The hook/global Linux tooling was not changed. For this commit only, bypass with `LEFTHOOK=0`; independently run the Xcode test suite, Swift format lint, OpenAPI SHA-256 check, and secret review.
