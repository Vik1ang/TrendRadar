# TrendRadar Device Authentication API v2

This is an additive contract. It does not change the frozen iOS API V1 OpenAPI document or its tag. Device credentials authorize a single installation to read news; Fastify is the authority for credential validity, expiry, revocation, and scope on both LAN and Relay requests.

## Trust boundaries

| Hop | Responsibility |
| --- | --- |
| iOS → LAN HTTPS Fastify | TLS and presentation of the per-device credential. Pairing is supported only here. |
| iOS → Cloudflare Worker → Durable Object → Relay Agent → Fastify | Worker validates the existing shared `CLIENT_API_TOKEN` as before, or admits only the strict device-token syntax through existing GET read allowlists. The Relay Agent authenticates its Worker WebSocket with the separate `RELAY_AGENT_TOKEN`, then forwards the original client `Authorization` unchanged. Fastify performs final device lookup/scope/expiry/revocation checks. |
| Web → Nginx → Fastify | Nginx continues to replace Authorization with the existing `API_READ_TOKEN`; no Web behavior changes. |
| ingestion client → private ingestion Fastify | `INGEST_TOKEN` remains independent. Device credentials are explicitly rejected with 403. |

No client-supplied identity headers are trusted. Worker syntax acceptance is not authorization. Invalid device credentials are rejected by Fastify with 401. A recognized credential without permission receives 403. Pairing is not relay-accessible.

## Pairing code issuance (operator-only)

There is intentionally no public issuance API. An operator with shell access to the API host and the application database runs `npm run device:auth -- issue --device-name "<label>"` after building the backend. The command emits a random one-time pairing code and expiration exactly once to stdout. Transfer it to the intended device over a private channel and do not paste it into chat, logs, tickets, shell history, or source control. The code expires after 10 minutes and is limited to five attempts. Only its SHA-256 hash is stored.

`npm run device:auth -- list` prints metadata only. Revoke one credential with `npm run device:auth -- revoke <device-uuid> --confirm`. These local commands are not exposed by HTTP/Worker/Relay.

## `POST /api/v2/devices/pair`

Must be called directly through HTTPS, not HTTP or Relay. Header: `Authorization: Pairing trd_pair_<43 base64url characters>`. JSON body: `{"deviceName":"<same label used at issuance>"}`. Additional body fields are rejected.

Success (200; response contains the bearer once):

```json
{"deviceId":"uuid","deviceName":"My iPhone","accessToken":"trd_dev_<43 base64url characters>","expiresAt":"2026-10-23T00:00:00.000Z","scopes":["news:read"]}
```

The returned 256-bit opaque token is valid for 90 days. Only its SHA-256 hash is persisted. Do not log or persist it outside the device Keychain. There is no refresh endpoint in this version; pair again with a newly issued code after expiry/revocation. Local logout deletes the Keychain item. Server revocation is an independent operator action.

Errors: 400 (`HTTPS_REQUIRED`, `DEVICE_NAME_MISMATCH`, or validation error), 401 (`INVALID_PAIRING_CODE`, including expired/replayed/malformed), 429 (`PAIRING_RATE_LIMITED`), or 503 (`DATABASE_UNAVAILABLE`). Per-process per-IP pairing throttle: five attempts per 60 seconds. Pairing must be placed behind a trusted TLS reverse proxy; set `API_TRUSTED_PROXY_IPS` only to the exact proxy peer address(es), never `*` or an untrusted client range, so Fastify derives HTTPS and client IP safely.

## Device reads

Send `Authorization: Bearer trd_dev_<43 base64url characters>` to existing V1 GET news/search/detail/analysis/categories/sources endpoints. The credential has only `news:read`; `/api/v1/status`, health, ingestion, and management operations are not granted to device identities. V1 success payloads are unchanged. Missing, malformed, expired, disabled, revoked, or unknown credentials return 401. A recognized credential without required scope/resource access returns 403. Device tokens cannot authenticate the separate ingestion listener.

## Persistence and operational constraints

Migration `003_device_credentials.sql` stores pairing code hashes and device token hashes, never cleartext secrets. Rows carry UUID, name, scope array, issue/expiry/revocation/enablement and last-use metadata. Revoke sets `enabled=false` and `revoked_at`; it affects only that device. The pairing route is intentionally not included in Relay's method/path allowlist. No changes are made to the V1 spec, shared service tokens, production configuration, or Cloudflare secrets in this change.

## Future staging/production rollout and rollback (not executed)

Production remains untouched. After separate deployment approval, use this order:

1. Build and test an immutable release from the reviewed commit; back up the target database and verify restore procedure. Keep `API_READ_TOKEN`, `CLIENT_API_TOKEN`, `RELAY_AGENT_TOKEN`, and `INGEST_TOKEN` unchanged.
2. Apply additive Migration 003 to the target database. The old API binary remains compatible with the extra tables.
3. Deploy the updated Fastify API while retaining existing static-token authentication. Verify existing Web and API V1 first; then verify v2 pairing endpoint is HTTPS-only and unknown/unpaired device tokens receive 401.
4. Deploy the Worker compatibility release; it forwards only the fixed device token syntax through read routes, excluding Fastify's unauthenticated health route. Verify Worker rejects unsupported paths/methods and Relay Agent continues using its separate bridge token.
5. Configure the separately documented trusted LAN TLS proxy. Do not issue a code until the hostname/certificate/ATS and firewall checks pass in staging.
6. Use the local admin CLI to issue one staging code, pair one test device, verify reads, status 403, ingestion 403, expiry/revoke 401, Relay forwarding and unchanged Web behavior. Record no secret values.

Rollback: disable the LAN TLS virtual host/443 route first; roll Worker back to its previous static-token-only release (devices then fail closed at Relay); roll Fastify back to the previous application release (device tokens fail closed while Web static-token reads continue). Leave Migration 003 and its hash-only rows in place during rollback—do not drop credential tables while credentials might still exist. If any staging device was issued, revoke its row using the new CLI before stopping that release. A later database rollback should only be considered after confirming no newer code depends on the schema and after a separate backup/restore plan; no destructive down-migration is supplied.

## IOS-AUTH-01 verification record

- Backend Fastify/fake-Pool suite: **43 tests pass**. Pairing HTTPS gate, expiry/replay, transient-store 503 mapping, hash-only storage contract, device read vs status 403, legacy read-token compatibility, simulated two-device revocation isolation, and device-token ingestion rejection are covered. No local Docker/Podman/PostgreSQL executable is available here, so Migration 003, real transaction/row-lock behavior, concurrent replay, and SQL constraint execution remain unverified against a database.
- Cloudflare Worker: **10 unit tests pass** and TypeScript typecheck passes; Relay Agent: **3 protocol tests pass** and TypeScript build passes. Device bearer cannot reach the unauthenticated health route; `/api/v1/status` is forwarded so Fastify can return 401 for invalid or 403 for valid-but-out-of-scope credentials. Pairing is not Relay-allowlisted. No Worker/Agent deployment was performed.
- iOS Simulator: **15 unit tests and 6 UI tests pass** on `iPhone 18 Pro`; the app remains Mock by default. Existing home/search/detail screenshots remain under `TrendRadar-iOS/Artifacts/UI-Tests/`.
- Frozen V1 OpenAPI SHA-256 remains `80ecf1eb8e25a400375cb555cb6c819afdee857453b632ae59cb3f906da65231` in both the backend baseline and iOS bundled copy. The hidden pairing route does not appear in generated V1 paths.
- No real shared credential, production database, production service, Cloudflare secret, or real device pairing code was used. LAN HTTPS and staging end-to-end verification remain pending operator authorization and domain/proxy prerequisites.
