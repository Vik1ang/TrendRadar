# iOS Backend Development Baseline

Date: 2026-10-23

Branch at audit start: `dev`

Source HEAD at audit start: `523ac7198d36be8fe4071028f1551f024ecb8118`
Baseline commit and tag are recorded in Git after verification (see final task report).

## V1 contract

- Contract text: [`API_CONTRACT_V1.md`](API_CONTRACT_V1.md).
- Machine-readable snapshot: [`openapi-v1.json`](openapi-v1.json), OpenAPI `3.0.3`, API version `1.0.0`.
- Snapshot SHA-256: `80ecf1eb8e25a400375cb555cb6c819afdee857453b632ae59cb3f906da65231`.
- Rebuild: `cd backend && npm run --silent openapi:export > ../docs/ios/openapi-v1.json`.
- Validation: `uv run openapi-spec-validator docs/ios/openapi-v1.json`.
- Article ID: stable persisted UUID; clients treat it as opaque.
- Current category IDs: `ai`, `technology`, `finance`, `society`; read catalog instead of hardcoding localized names.
- Null semantics: `publishedAt`, `summary`, `url`, and `analysisSummary` can be null. An article without an analysis row reports its stored state (including `not_requested`) without triggering inference.

The read API includes `GET /api/v1/health`, `/news`, `/search`, `/news/{id}`, `/news/{id}/analysis`, `/categories`, `/sources`, and `/status`. Read the contract for auth, query bounds, pagination, error bodies, and sorting semantics. The iOS app must not embed the shared server-side `API_READ_TOKEN`.

## Verification evidence and deployment relationship

Read-only checks performed on 2026-10-23:

- Current LAN Fastify `GET /documentation/json`: OpenAPI 3.0.3, API 1.0.0, eight public paths. Its route set, parameters, and 200 success schemas match the source snapshot. The deployed OpenAPI predates this documentation-only error-response schema enrichment and did not declare the business 400/401/404/500/503 responses (or catalog/status 401/500/503); the source snapshot now declares those observed error variants. No route, successful response, or runtime behavior changed.
- Current deployed runtime examples: list/search/detail/analysis returned 200; unauthenticated read returned 401; invalid `limit` returned 400; missing article returned business 404; unknown route returned Fastify's separate 404. Error JSON is detailed in the V1 contract.
- LAN and Cloudflare Relay returned the same selected real article, pagination, detail, and `not_requested` analysis; unauthenticated Relay request returned 401. Live data content and credentials are intentionally not copied here.
- The live API returned 1,185 total articles at verification time. This does not confirm AI-analysis or queue counts.
- The running API image was created `2026-10-22T01:14:36Z` and has no `org.opencontainers.image.revision` label. Therefore its exact Git commit cannot be established from runtime metadata. The running image predates source HEAD `523ac71` (commit time `2026-10-23 15:13:26 +0800`); do not claim production is running this baseline Commit. The OpenAPI difference is additive error-response documentation; observed runtime error payloads and source regression tests agree. No production deploy was performed.

## Quality checks

Backend command set:

```sh
cd backend
npm test
npm run typecheck
npm run lint
npm run build
npm run --silent openapi:export > ../docs/ios/openapi-v1.json
```

Result: Backend Vitest 34 tests passed; typecheck, ESLint, and build passed. OpenAPI export reproduced the snapshot byte-for-byte and `openapi-spec-validator` passed. Python Pytest reported 68 passed plus 12 subtests; targeted Ruff and Mypy checks for history-sync passed. Full-repository Ruff remains non-clean with 1,556 existing findings; unrelated files were not changed. No model call, ingestion, production write, or production deployment was performed for this baseline.

## P2 / operations remain separate

This baseline does not accept P2 production automation. The last recorded operational state (not all re-read during this baseline) was: frozen spool 1,187 records at `nextIndex=740`, B3 paused, 12,477 uncovered historical identities, Timer disabled, and automated AI daily budget zero. Previous report values listed 1,185 articles, 3 completed analyses, and 733 queue jobs; this turn verified the article total only through the read API. These values are operational records, not part of the API fixture or OpenAPI snapshot.

The following remain independent pre-release work:

- LAN HTTPS endpoint: not provisioned; do not use the LAN Web cleartext URL as an iOS API endpoint.
- Device-scoped, expiring/revocable credentials: not implemented. Do not embed the shared read token or Relay management credentials in SwiftUI.
- iOS real-device TLS, Local Network permission, network switching, and credential lifecycle tests: not performed.
- P2 history catch-up/B3, historical backlog, and automatic scheduling: not accepted or enabled.

SwiftUI MVP work can begin against the frozen read contract with authentication/network access isolated behind client protocols. Real-device authenticated integration and release remain blocked on trusted LAN HTTPS and device credentials.
