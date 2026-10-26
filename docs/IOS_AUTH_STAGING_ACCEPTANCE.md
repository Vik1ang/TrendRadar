# IOS-AUTH-STAGING-01 — staging acceptance record

Date: 2026-10-26
Branch: `staging/ios-auth-01`
Integrated commit: `8bcc81ec98818105e93b30f1c20892b0421a80f6`
Input auth commit: `a0b60f4`; integration base (Linux `dev`): `f965e5d`

## Isolation and topology

The staging stack is a separate Compose project, `trendradar-ios-auth-staging`, at
`docker-compose.ios-auth-staging.yml`. It owns database `ios_auth_staging`, volume
`trendradar-ios-auth-staging-postgres`, and networks
`trendradar-ios-auth-staging-db/edge`. It does not join the existing LAN acceptance
network. PostgreSQL and ingestion have no host-published ports. The API is published
only on host loopback `127.0.0.1:13101`; the TLS gateway binds LAN address
`192.168.2.117:18443`, restricts source addresses to `192.168.2.0/24`, and only
proxies GET `/api/v1/*` and POST `/api/v2/devices/pair`. No production Compose file,
database, token, Worker, or
Relay Agent configuration was changed.

All staging-only secrets and TLS private keys live under
`~/.config/trendradar-staging/ios-auth-01` (directory mode 0700, secret files 0600).
They are not tracked or printed. The stack has no model-provider credential and the
analysis daily budget is fixed at zero.

The normal repository Dockerfile targets Node 24. The staging-only Dockerfile uses
the already cached Node 22 image because this host could not retrieve the Node 24
base from Docker Hub during staging bootstrap. Node 22 is within the Fastify 5
runtime floor, but this is a staging-only environment difference; no production image
was changed.

## Secure iOS staging connection

LAN Base URL: `https://192.168.2.117:18443`

The endpoint has a SAN for the LAN IP and TLS 1.2/1.3. Its chain is signed by a
private, staging-only CA, not a public CA. The chain was verified from macOS with
`curl --cacert` and from Linux Python TLS with hostname/IP verification. The iOS
Simulator trust store was provisioned only with this staging root using
`xcrun simctl keychain <simulator-udid> add-root-cert <root-ca.crt>`. ATS and
certificate validation stay enabled; no trust bypass is present. A physical iPhone
must receive the root CA through an approved profile or use a publicly trusted,
DNS-named certificate before it can connect. The private CA is not a production
certificate and its root private key is never copied to Mac.

The shared Xcode scheme `TrendRadar-Staging` is an explicit staging run
configuration: it passes `--live-api --lan-base-url
https://192.168.2.117:18443`. The existing `TrendRadar` scheme and default app
launch remain Mock-backed. No token is embedded in either scheme. Pair from the
Device Authentication sheet after generating a short-lived code using the CLI below.

Pairing contract:
- `POST /api/v2/devices/pair`
- HTTPS only; `Authorization: Pairing trd_pair_<43 URL-safe characters>`
- JSON `{"deviceName":"<name>"}`
- Success returns `deviceId`, `deviceName`, `accessToken`, `expiresAt`,
  and exactly the `news:read` scope.
- The app stores the returned device credential in the device Keychain.
- A 401 requires re-pairing; no automatic shared-token fallback exists.
- A 403 is permission denial, not a signal to try Relay.
- Never pass pairing codes in URLs, paste them into logs, or include them in evidence.

Operator-only CLI (run from the Linux staging worktree; output is a secret):
```sh
umask 077
BASE="$HOME/.config/trendradar-staging/ios-auth-01"
docker compose --env-file "$BASE/staging.env" -f docker-compose.ios-auth-staging.yml \
  exec -T api node dist/src/device-auth-cli.js issue --device-name "iPhone staging" \
  > "$BASE/pairing.json"
# Read/use pairing.json only locally in the authorized device-pairing flow.
docker compose --env-file "$BASE/staging.env" -f docker-compose.ios-auth-staging.yml \
  exec -T api node dist/src/device-auth-cli.js list
docker compose --env-file "$BASE/staging.env" -f docker-compose.ios-auth-staging.yml \
  exec -T api node dist/src/device-auth-cli.js revoke <device-uuid> --confirm
```
Remove the local pairing file after pairing using an approved secure deletion policy;
do not put it in the repo. The list output includes metadata/UUIDs but no token.

## API contract baselines

- iOS API V1 file: `docs/ios/openapi-v1.json`
- V1 SHA-256: `80ecf1eb8e25a400375cb555cb6c819afdee857453b632ae59cb3f906da65231`
- Device Auth V2 file: `docs/DEVICE_AUTH_V2.openapi.json`
- V2 SHA-256: `eedbb42574f7ced5114e6d84563820955edaf49e07b2035a9708d57ff32da1b3`
- The V1 baseline tag was not modified.

## Verification performed

The real PostgreSQL staging smoke runner is
`scripts/ios-auth-staging-smoke.py`. It repeats migration on the actual isolated
PostgreSQL 16 volume and verifies all three migration records, a seeded sanitized
fixture with null V1 fields, unauthenticated 401, legacy API read-token compatibility,
simultaneous redemption of one pairing code (exactly one 200 and one 401),
hash-only credential persistence, invalid and expired credentials, replay and
expired-pairing rejection, `news:read` scope denial (403), two-device independence,
concurrent and idempotent revoke, transaction rollback after a forced database
constraint failure, denied ingestion access for a device token, acceptance of
the separately generated staging INGEST credential, pairing rate limiting (five
invalid requests get 401, the sixth 429), and absence of credential-shaped values
in API logs. It revokes test identities after success. No model call is invoked.

Acceptance commands/results from 2026-10-26:
- Staging migration: startup first pass + smoke-runner rerun; 3 versions, idempotent.
- Real PostgreSQL/HTTPS lifecycle smoke: all checks PASS.
- Backend suite: 44 tests PASS; typecheck PASS.
- Cloudflare Worker unit suite: 10 tests PASS; typecheck PASS.
- Relay Agent suite: 3 tests PASS after `npm run build`.
- Node dependency audit during test install reported existing advisories (backend:
  5 total, including 2 critical; Worker: 3 total, including 1 high). No dependency
  upgrades were made in this staging task; review these before a production release.
- iOS Simulator on iPhone 17 / iOS 27: the staging CA was added to that simulator
  only; the app launched with explicit HTTPS live-mode arguments and reached the
  reauthentication/pairing state after the Staging API returned 401 (TLS was not
  bypassed). Existing UI tests reported 5/5 pass in the full run. A separate
  normally signed unit run reported 15/15 pass, including Keychain lifecycle. The
  initial all-target run used `CODE_SIGNING_ALLOWED=NO` and failed one Keychain test
  with `errSecMissingEntitlement`; this was confirmed as invocation-related by the
  normally signed unit run.
- A completed pair + Keychain persistence + post-pair news read from within iOS
  Simulator was not verified: this host has headless CoreSimulator and no installed
  `Simulator.app` GUI for a manual pairing flow, and the Xcode test runner does not
  inherit arbitrary shell environment variables for secret injection. Pairing API
  and simulator HTTPS were tested independently. Do not treat this as a passed
  device-pairing acceptance.
- Physical device pairing is not claimed.

## Relay and remaining gates

No independent Cloudflare Worker / Durable Object staging account/configuration was
available within the authorized scope. The production Worker was not edited or
called. The Worker and Relay Agent unit suites validate allowlisting, Bearer
forwarding, and fixed-target behavior, but this is not a live Relay acceptance.
There is no Staging Relay Base URL; do not point this build at the production Relay.
A separate free-resource staging Worker/DO and Relay Agent configuration, with a
separate Worker client token and internal Agent token, is required to test
revocation through Relay and offline responses.

No app API V1 implementation changed; Staging uses the frozen V1 contract. The
Xcode scheme is an opt-in live-mode scheme and starts unauthenticated until pairing.
Before IOS-MVP-03 can be treated as full LAN+Relay acceptance, complete a live
Simulator pair/read/Keychain persistence check, obtain approved trusted HTTPS for
the physical device (or distribute the staging CA profile), and establish/test an
isolated Worker/DO + Relay Agent. LAN-only Simulator integration can proceed after
that explicit simulator test.

## Rollback and stop condition

This staging environment is disposable and isolated. The application change has
not been pushed or deployed to production. If cleanup is authorized later, use only
the staging Compose project name and inspect its resources first; do not remove the
existing `trendradar-lan-acceptance` project or production resources. This task
stops at Staging; production migration, service deployment, credential issuance,
and Worker changes remain unauthorized and unperformed.
