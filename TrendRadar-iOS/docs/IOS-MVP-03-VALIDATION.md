# IOS-MVP-03 validation record

Date: 2026-10-26 (Asia/Shanghai)

## Baselines and isolation

- Mac client branch `dev`, starting commit `32241fbc5eb4377a64bf07062ed176c5bb84699b`.
- Linux Staging worktree `staging/ios-auth-01`, starting commit `734966af6289571e006d0fee90552da0904a65c3`.
- Frozen API V1 SHA-256: `80ecf1eb8e25a400375cb555cb6c819afdee857453b632ae59cb3f906da65231` (Linux source and Mac resource copy).
- Device Auth V2 SHA-256: `eedbb42574f7ced5114e6d84563820955edaf49e07b2035a9708d57ff32da1b3` (Linux Staging contract and Mac root docs copy).
- No production DB, service, Worker route, secret, token, or deployment was changed. The Staging Worker was not redeployed. Relay requests used the dedicated Staging Agent identity and an authorized outbound HTTP CONNECT proxy; certificate validation remained enabled.
- Default `TrendRadar` scheme remains Mock. `TrendRadar-Staging` is explicit live staging configuration. Simulator-only proxy parameters are passed only in the UI test launch arguments, not in the physical-device staging scheme.

## Simulator and network results

Device: iPhone 17 Simulator, iOS 27.0.

- True pairing UI submitted an operator-issued, short-lived Staging code over LAN HTTPS. Fastify issued a per-device credential; the app displayed the paired state and made authenticated V1 reads. Keychain state survived App termination/relaunch.
- LAN feed, all four channel controls, search, detail and `not_requested` analysis UI succeeded against the Staging fixture. The fixture has a null publication time, null summary, and no categories.
- **A — forced Relay:** passed using the hosted Worker URL and the same Keychain device credential; fixture list/detail displayed the same Article ID and title as LAN.
- **B — automatic failover:** passed with the LAN endpoint deliberately made unreachable. The client timed out on LAN and loaded the fixture through the hosted Staging Relay; detail opened. Restoring the LAN endpoint made the client select LAN again.
- Server-side revocation of all three active Simulator test devices was performed. The retained Simulator credential then received 401 and the App presented the re-pair UI. The user-visible “delete local credential” action removed that revoked credential from Simulator Keychain after the screenshot.
- Relay Agent offline: Staging Worker health returned HTTP 503 / `relay=offline`; UI showed “Relay agent is not connected”. Restarting the Agent restored WSS registration and Worker health HTTP 200 / `relay=connected`.
- **C — actual network change:** not tested. CoreSimulator shares the Mac host network; this does not establish Wi-Fi-to-cellular behavior on a physical iPhone.
- HTTPS reads and pairing passed without ATS exceptions or certificate bypass. LAN HTTPS used the existing Simulator-trusted staging certificate; Relay used normal public TLS validation.
- Fastify Staging logs counted **18 GET API V1 reads** in the Staging Fastify logs across the audit window (under 30 minutes); this is below the 100-read/1-hour limit. Health probes are not included in that count. No load test was run.

## Verification

- `xcodebuild test ... -only-testing:TrendRadarTests`: **18 passed, 0 failed**.
- Mock UI regression suite (`testNewsChannelsRefreshAndUncategorizedArticle`, pagination/empty, error/retry, search/clear/detail, rapid search/pagination, details/nulls/analysis): **6 passed, 0 failed**. Includes LAN preference, unreachable/timeout-only fallback, no fallback for 401/403/TLS/certificate errors, HTTP CONNECT proxy `allowFailover=false`, Relay 503 mapping, API V1 models and request cancellation.
- Live XCTest UI flows: LAN pairing/read + channel/search/detail + Keychain relaunch + automatic Relay fallback/recovery: passed; forced hosted Relay read/detail: passed; revoked-device reauthentication: passed; Agent-offline 503 UI: passed. The temporary code-entry test method/result bundle were removed after detecting Xcode interaction logging of typed text; routine committed UI tests do not accept pairing secrets.
- Staging Agent `npm run build && npm test` in Node 22 container: **5 passed**. After the shutdown fix, Staging Agent stop completed in ~2 seconds with exit code 0, `oom=false`, and `Relay Agent shutdown complete`. The previous Staging stop had exit 137; the fix is in the Staging worktree only. Production Agent was not touched.
- Worker deployment version was not changed by this run. The expected Worker ID `d89eed5f-fa2e-4590-98ef-2b6ecc0d358b` was supplied as the precondition; this task did not call Wrangler deploy or mutate Cloudflare resources.

## Screenshot artifacts

See [`Artifacts/IOS-MVP-03/`](../Artifacts/IOS-MVP-03/README.md). Captures include pairing status, LAN feed, search, detail, forced Relay, automatic fallback, recovered LAN, and reauthentication. Images contain no code or Bearer value.

## Gaps / follow-up

- Simulator system locale was English, so timestamps rendered with English month names; Chinese locale rendering was not validated.
- A separate device with removed `news:read` scope was not issued, so live Relay 403 UI was not tested in this run. Unit tests verify 403 does not trigger fallback; the earlier staging contract suite covers authorization mapping.
- Physical iPhone, Wi-Fi/cellular transition, external DNS/certificate trust on a physical device, and relay read after the final Agent restart remain unverified.
- A one-time Staging pairing code was echoed in truncated XCTest interaction output when `typeText` was used. It was single-use and consumed; the result bundle and temporary pairing/test-plan files were removed, and all Simulator test credentials were revoked. No long-lived Bearer or production secret appeared. Do not reintroduce plaintext pairing-code entry through `XCUITest.typeText`; use an audited secret-safe input channel in a future run.

## Stop state

Production remains unchanged. The temporary Staging Agent must be stopped after the audit and verified at exit code 0. No production rollout is authorized by this record.
