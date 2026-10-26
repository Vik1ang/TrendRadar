# TrendRadar API V1 — Device Authentication Requirements

Status: API V1 remains frozen and contains no lifecycle endpoints. IOS-AUTH-01 adds the separately versioned LAN HTTPS pairing contract documented at `../../docs/DEVICE_AUTH_V2.md`.

## Current client boundary

The iOS client supports read-only API V1 calls plus explicit API v2 pairing over HTTPS LAN only. It must not embed `API_READ_TOKEN`, `CLIENT_API_TOKEN`, a Cloudflare Worker/Relay bridge token, or another shared production secret. Device credentials are per-installation, stored in Keychain, expire, and can be independently revoked by the server operator. API v2 currently has no refresh or client-side server-revoke endpoint; a 401 is surfaced as an authentication error and must not trigger transport fallback or infinite retries.

## Required device lifecycle

The backend contract should specify:

1. **Enrollment / login** — an explicit, auditable bootstrap flow that does not expose a shared server token to the app. Define user/operator authorization, device naming/identity, rate limits, and replay protections.
2. **Credential issuance** — issue a unique opaque bearer credential (or a signed, short-lived access token) per device, with read-only scopes, `issuedAt`, `expiresAt`, and a non-secret device identifier. Do not reuse service-to-service or legacy static tokens.
3. **Validation** — reject missing, malformed, expired, revoked, wrong-audience, or insufficient-scope credentials. Return 401 for absent/invalid/expired credentials and 403 for authenticated but unauthorized requests; error bodies may omit `requestId`.
4. **Refresh** — API v2 deliberately has no refresh endpoint. After expiry/revocation, the app must pair again using a newly operator-issued code; add refresh only as a separately specified future contract.
5. **Revocation / logout** — an operator can revoke one credential by UUID through the local admin CLI; Fastify checks the database on every authenticated request. App logout only deletes the local Keychain item and does not claim server revocation; there is no public revoke API.
6. **Recovery** — a lost/reinstalled device is paired as a new device and the old UUID is revoked through the CLI. No API response discloses credential material after the one-time issuance response.

## Cloudflare Worker ↔ Fastify trust boundary

Implemented boundary for this iteration:

- Worker accepts either the unchanged legacy shared client credential or only the strict `Bearer trd_dev_<43 base64url>` forwarding shape through its existing read allowlist. This syntax gate is not authorization and does not query device state.
- The Relay Agent continues authenticating its own Worker WebSocket with separate `RELAY_AGENT_TOKEN`; it forwards the client bearer unchanged to Fastify.
- Fastify is the authoritative verifier for database existence, expiry, enablement, revocation and `news:read` scope on both LAN and Relay. It does not trust client identity headers. Ingestion remains a separate listener with `INGEST_TOKEN`.

The Worker does not validate opaque-device credential state or mint identity assertions. This is an explicit tradeoff: it retains Fastify as the single source of truth and avoids a second device registry; syntactically valid but invalid/revoked device tokens may consume relay resources up to the existing relay limits.

In either design, the client presents only its per-device credential over HTTPS. The Relay bridge/service credential remains server-side and is never accepted as a mobile-device identity. Worker-origin authentication, assertion signing/rotation, replay protection, and the Fastify behavior when the Worker is bypassed must be specified. Untrusted `X-Device-*` or `X-Authenticated-*` headers must be stripped at ingress and never accepted as proof by Fastify.

## Client mapping

- Save issued credential in Keychain with device-only accessibility; never log it or include it in diagnostics/screenshots.
- Check local expiry before requests; treat server 401 as invalid/expired and transition to reauthentication-required UI state.
- Refresh only through a later, explicitly specified refresh contract; coalesce concurrent refreshes and atomically replace credentials.
- On logout/revocation, call the defined server operation if available, then delete local credential even if the network is unavailable; report the server-side outcome distinctly.
- Never use 401/403, timeout, or TLS failure as a reason to downgrade TLS or silently switch to an unauthenticated path.

## Backend deliverables before real-device integration

Remaining before real-device integration: apply the migration to an isolated staging DB; deploy backend and Worker compatibility with staging approval; set up a publicly trusted LAN HTTPS hostname/proxy; verify revocation and Relay-through-Fastify end to end; provide a non-production operator-issued pairing code. The app defaults to Mock API and production has not been changed.

## IOS-AUTH-01 client implementation boundary

The client now supports the following isolated contract without changing API V1:

- `POST /api/v2/devices/pair` must be HTTPS and accepts `Authorization: Pairing <one-time-code>` plus JSON `{ "deviceName": "..." }`.
- A successful response must contain `deviceId`, `deviceName`, an opaque `accessToken` beginning with `trd_dev_`, RFC 3339 `expiresAt`, and a `scopes` array containing `news:read`.
- The pairing code is sent only in the Authorization header; it is never placed in a URL, persisted, or logged. The client rejects HTTP pairing URLs and malformed responses.
- The issued credential, device metadata, expiry, and scopes are stored together in a device-only Keychain item. Local logout deletes the item. There is deliberately no client-side refresh flow because no refresh endpoint is frozen.
- With explicit `--live-api`, the app reads through the existing LAN-first `RoutedTrendRadarAPI`; otherwise it remains on Mock API. Relay fallback is HTTPS-only and only applies to reachability errors. 401, 403, and TLS failures are not fallback conditions.
- Missing, expired, or unauthorized credentials are presented as reauthentication-required states. The client never falls back to `API_READ_TOKEN`, an internal Relay credential, cleartext HTTP, or certificate bypass.

The server must keep the pairing route outside the frozen V1 tag and must not treat the pairing Authorization scheme as a news-read bearer credential. Fastify remains responsible for checking the resulting device scope on every read route; Worker/Relay authentication remains separate from the device credential.
