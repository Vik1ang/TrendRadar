# IOS-AUTH-STAGING-02 — Simulator, Relay and certificate acceptance

Date: 2026-10-26
Branch: `staging/ios-auth-01`
Staging integration base: `4fa0304225d0709ea0604490035a968a064b499d`
iOS validation commit: `32241fbc5eb4377a64bf07062ed176c5bb84699b` (integrated as `4b65fa2`)
Production and formal Cloudflare Worker: unchanged.

## Results

### Simulator pairing and Keychain

The `TrendRadar-Staging` app was exercised on the iPhone 17 / iOS 27 Simulator using the staging private CA installed only in that Simulator. An operator-issued, short-lived staging pairing code was entered into the actual pairing UI. The HTTPS pairing request was verified by Staging Fastify, which issued a separate device credential with `news:read`; the app stored it in the Simulator Keychain. No Mock pairing response, shared API token, or TLS bypass was used.

The test then read staging categories/news and opened the seeded staging article and its detail. After terminating and relaunching the app, Keychain authentication remained valid. A deliberately unavailable LAN port exercised the local Relay path and returned the same fixture article/detail/analysis state. Follow-up UI tests revoked the device, then observed 401 and the reauthentication UI through both LAN and Relay paths. The scope-less credential test showed 403 through Relay; stopping the Relay Agent showed the explicit 503/offline state. Recovery after the Agent returned healthy was also exercised. All temporary Simulator pairing credentials were revoked and temporary pairing-code files removed. The staging device list has no enabled test identity (count rechecked after cleanup).

Default `TrendRadar`/Mock launch behavior is unchanged. The opt-in `TrendRadar-Staging` scheme remains the only live configuration. No pairing code, bearer, or complete Keychain content is part of this record.

### Relay boundary: local E2E only

The isolated local stack uses a Wrangler `wrangler dev --local` Worker with local Durable Object persistence, a separate staging Relay Agent identity and the staging Fastify API. The Worker only forwards allowlisted news-read paths; the Agent connects only to this staging API. End-to-end UI tests traversed Worker → local Durable Object → Relay Agent → Fastify for successful reads, revoked credentials (401), missing scope (403), and Agent-offline (503). The same staging article ID and response content were observed on LAN and Relay. Relay credentials and state are staging-only/private; logs were checked for credential values.

**This is not a Cloudflare-hosted Worker or globally distributed Durable Object acceptance.** No Worker/DO/resource, route, secret, or production configuration was created or deployed. The Cloudflare account's present usage/quota was not inspected. Consequently there is no public Staging Relay URL. User authorization and a read-only account quota/isolation check are required before creating or deploying an external Cloudflare Staging resource. Cloudflare currently documents Workers Free request limits and separate Durable Object SQLite/free-plan quotas; confirm the account's actual usage before provisioning ([Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [Durable Objects limits](https://developers.cloudflare.com/durable-objects/platform/limits/), [pricing](https://developers.cloudflare.com/workers/platform/pricing/)).

### Staging integrity

The separate Compose project `trendradar-ios-auth-staging` remains running with PostgreSQL 16 and the independent staging data volume. The database has no published host port; ingestion has no published port; API is published only at `127.0.0.1:13101`; HTTPS gateway listens only on `192.168.2.117:18443`. TLS remains enabled and the gateway uses a staging-only CA. Staging secrets and keys stay in the private `~/.config/trendradar-staging/ios-auth-01` directory. Staging has no model-provider key and daily analysis budget is zero. The original `trendradar-lan-acceptance`, Linux `dev`, production database/API, production credentials and formal Worker were not changed.

Frozen OpenAPI files are unchanged:

- V1 `docs/ios/openapi-v1.json`: SHA-256 `80ecf1eb8e25a400375cb555cb6c819afdee857453b632ae59cb3f906da65231`
- Device Auth V2 `docs/DEVICE_AUTH_V2.openapi.json`: SHA-256 `eedbb42574f7ced5114e6d84563820955edaf49e07b2035a9708d57ff32da1b3`

### UI verification

The final full signed Xcode Simulator suite completed with **25 tests: 21 passed, 4 staging-gated tests skipped, 0 failed**. Additional staging-gated runs passed for:

- real UI pairing → Keychain save → LAN news/detail → app restart persistence → local Relay news/detail;
- revoked device through LAN and Relay → 401 reauthentication UI;
- scope denial through Relay → 403 UI;
- Relay Agent stopped → 503/offline UI.

Previous IOS-AUTH-STAGING-01 evidence remains in `docs/IOS_AUTH_STAGING_ACCEPTANCE.md` for the PostgreSQL migration/concurrency/replay tests and backend/Agent/Worker unit suites. This round changes iOS presentation/tests and isolated staging gateway/local Worker topology only; it does not alter API V1 response fields or auth contracts.

### Dependency advisories

Targeted `npm audit` was reviewed without upgrading unrelated dependencies:

- Backend production dependency path: `@fastify/swagger-ui` → `@fastify/static` (installed versions `5.2.6` / `9.3.0`); the reported static path traversal/high and noncanonical path/moderate advisories are reachable through API documentation/static serving. Staging gateway only exposes pairing and V1 read routes; local Worker allowlisting excludes docs and pairing, so this path is not reachable through the Staging Relay. It remains a backend dependency risk to address before any broader public exposure.
- Worker audit: `miniflare`/`undici` advisories are in the local simulation/test toolchain; Wrangler is a dev tool. `npm audit --omit=dev` reports no production Worker dependency findings, and no package is bundled as a Worker runtime dependency in this setup. No broad or unrelated upgrade was made. Re-audit at the time of any Cloudflare deployment.

### Physical iPhone HTTPS plan (not deployed)

Preferred path: use the user's existing DNS zone and a DNS-01-issued public certificate for a DNS name such as `ios-api.<existing-domain>`. Keep the current 18443 gateway as the LAN TLS termination point; serve the certificate's full trusted chain, and use split-horizon/internal DNS that resolves only to the LAN gateway address. DNS-01 does not require WAN inbound access ([Let's Encrypt challenge types](https://letsencrypt.org/docs/challenge-types/)). Restrict the DNS API token to the required existing zone and DNS edit capability, store it only on the gateway host with mode 0600, automate renewal, validate the renewed chain, atomically install files and reload Nginx. Do not publish database or ingestion ports or change production Web routing.

The current private CA is suitable only for controlled Simulator testing. A physical iPhone would require an installed profile with full trust (Apple describes the manual trust step and recommends managed/configurator deployment; [Apple certificate trust](https://support.apple.com/en-us/102390)). Prefer the publicly trusted DNS name for normal device use; do not disable ATS or certificate validation. The user's existing domain/provider is not established in this workspace, so no DNS zone, certificate, token, public record or gateway config was changed. A public cert and physical iPhone validation remain pending.

## Handoff to IOS-MVP-03

Available Simulator LAN URL: `https://192.168.2.117:18443` (private staging CA trust required in the Simulator only). Local-only Relay URL: `https://192.168.2.117:18443/relay` (same trust requirement); **not** a public Cloudflare endpoint. Use the explicit `TrendRadar-Staging` scheme and issue a fresh short-lived code through the staging CLI only when pairing; revoke the device after use. The Simulator test runner receives pairing-code input only through a private environment injection; never put it in scheme source, test output or screenshots. Device revocation uses the existing staging CLI. On 401, clear/reject local credentials and present the re-pair/reauthentication UI; 403 is authorization denial; 503 means Relay offline. No API V1 baseline change is needed.

IOS-MVP-03 can proceed for LAN Simulator integration with the present staging service. Full LAN+Cloudflare Relay and physical iPhone acceptance remain blocked on (1) explicit authorization plus isolated Worker/DO quota validation, and (2) confirming the user's existing domain/DNS provider and approving DNS-01 configuration for a publicly trusted certificate.

## Commits and stop condition

- iOS changes: `32241fbc5eb4377a64bf07062ed176c5bb84699b` on Mac `dev`.
- Staging integration: `4b65fa2` on `staging/ios-auth-01`, including isolated local Worker build, Simulator gateway allowlist for the authorized Mac source, and this record.
- No push, production deployment, production migration, Cloudflare deployment, production credential issuance, AI call, or P2 Timer activation occurred.

Stop at isolated Staging. External Cloudflare provisioning and production changes remain unauthorized and unperformed.
