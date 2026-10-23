# TrendRadar iOS Backend API Contract V1

Status: frozen for the iOS MVP client against the repository Fastify contract.
Machine-readable OpenAPI snapshot: [`openapi-v1.json`](openapi-v1.json).

## Contract rules

- Business API base path is `/api/v1`; LAN and Relay use the same paths and JSON models.
- All read resources are GET-only. Reading a news article or analysis never invokes an AI model.
- Fastify does not require business Bearer for health; all other business reads require Bearer. The current Cloudflare Worker adds its own ingress authentication, including for health, before forwarding. `API_READ_TOKEN` is a server-side shared credential, not an iOS credential and must not be embedded in the app.
- Article `id` is a stable UUID assigned during normalization and persisted as the PostgreSQL primary key. Do not synthesize or reinterpret it on the client. Identity currently derives from normalized URL when present, otherwise RSS GUID plus source, otherwise source plus normalized title; the UUID is UUIDv5 over the SHA-256 identity digest.
- Output timestamps are ISO 8601/RFC 3339 date-time strings in UTC (`Z`). `publishedAt` is required in article JSON but nullable; `firstSeenAt` is required and non-null. Do not substitute collection time for a missing publication time.
- `summary` (source-provided) and `analysisSummary` (completed AI summary) are required nullable fields. Missing content must remain null; clients must not invent article text.
- `categories` is always an array, supports multiple tags, and may be empty. Current seeded IDs are `ai`, `technology`, `finance`, and `society`; clients should obtain the catalog from `/api/v1/categories`, not hardcode display names.
- Clients must ignore unknown response fields. Additive optional fields are compatible; removing, renaming, changing type/nullability, pagination semantics, stable IDs, category identifiers, or analysis status requires a new API version or an explicit migration.

## Read endpoints

| Method / path | Auth | Contract |
|---|---|---|
| `GET /api/v1/health` | Fastify: none; current Relay Worker: ingress Bearer | Backend/DB readiness and stable `service` identity. `200` returns `{status:"ok",database:"ok",service:"trendradar-intelligence"}`; DB unavailable returns `503` with `{status:"error",database:"unavailable",service:"trendradar-intelligence"}`. TLS hostname/chain must also be verified by clients. |
| `GET /api/v1/news` | Bearer | List with optional category/source/date/keyword filters and offset pagination. |
| `GET /api/v1/search` | Bearer | Server-side PostgreSQL search; same filter and pagination parameters as news. If `q` is absent, behavior is the same filtered listing. |
| `GET /api/v1/news/{id}` | Bearer | Article resource by UUID; unknown article returns business `404 NOT_FOUND`. |
| `GET /api/v1/news/{id}/analysis` | Bearer | Reads only persisted analysis/status. If no analysis row exists, returns `{status:<article analysis status>}` (for example `not_requested`); does not run a model. Unknown article returns business `404 NOT_FOUND`. |
| `GET /api/v1/categories` | Bearer | Current category catalog (`id`, `name`, `sortOrder`). |
| `GET /api/v1/sources` | Bearer | Current source catalog (`id`, `name`, `type`). |
| `GET /api/v1/status` | Bearer | Read-only ingestion/API status; not required for article browsing. |

### Query parameters (`news`, `search`)

- `q`: optional string, 1–200 characters; literal substring matching over title, source summary, and completed AI summary.
- `category`: optional category ID string, 1–80 characters; articles can have multiple category tags.
- `source`: optional source ID string, 1–160 characters.
- `from`, `to`: optional ISO 8601 date-time filters. The SQL range is inclusive `from` and exclusive `to`, against `COALESCE(publishedAt, firstSeenAt)`.
- `limit`: optional integer, default 30, range 1–100.
- `offset`: optional integer, default 0, range 0–1,000,000.
- Response is `{data: Article[], pagination:{limit,offset,total,hasMore}}`. Sorting is stable by `COALESCE(publishedAt, firstSeenAt) DESC, id`; pagination is offset-based, not cursor-based.

### Article response

`id`, `title`, `source`, `publishedAt`, `firstSeenAt`, `url`, `summary`, `analysisSummary`, `contentStatus`, `analysisStatus`, and `categories` are present. `publishedAt`, `url`, `summary`, and `analysisSummary` are nullable. `source` has `id`, `name`, `type` (`hotlist|rss`). `contentStatus` indicates title/summary/content availability. `analysisStatus` is one of `pending|processing|completed|failed|not_requested`.

When analysis is not completed, `analysisSummary` is null. The analysis endpoint returns `status`; when a stored analysis exists it may also return `errorCode`, `summary`, `keyFacts`, `background`, `potentialImpacts`, `uncertainties`, `evidenceScope`, `modelName`, `promptVersion`, `analysisVersion`, and `analyzedAt`. Optional analysis fields are absent when no analysis row exists; nullable fields remain null when present.

## Errors

| HTTP | `error.code` | Body contract |
|---|---|---|
| 400 | `VALIDATION_ERROR` | `{error:{code,message},requestId}` |
| 401 | `UNAUTHORIZED` | `{error:{code,message}}`; intentionally has no `requestId` |
| 404 (article/analysis only) | `NOT_FOUND` | `{error:{code,message},requestId}` |
| 500 | `INTERNAL_ERROR` | `{error:{code,message},requestId}` |
| 503 (business reads) | `DATABASE_UNAVAILABLE` | `{error:{code,message},requestId}` |

No business error includes `details`. Unknown route 404 is Fastify's separate `{message,error,statusCode}` shape and is not an article response. Health 503 uses the dedicated readiness shape above. Do not assume every error has `requestId`.

## Generation and validation

From repository root:

```sh
cd backend
npm run --silent openapi:export > ../docs/ios/openapi-v1.json
cd ..
uv run openapi-spec-validator docs/ios/openapi-v1.json
sha256sum docs/ios/openapi-v1.json
```

Export builds the contract from `buildApp()` and does not connect to PostgreSQL. For deployed compatibility, separately GET `/documentation/json` from the target LAN/Relay backend and compare paths/success schemas and observed response payloads. Do not put credentials, private endpoint URLs, response article content, or production database data in the snapshot.
