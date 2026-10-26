# LAN HTTPS for iOS device authentication (deployment runbook)

## Current state

No LAN TLS listener, certificate, or verified LAN API FQDN is configured by this change. Production/staging deployment and credential issuance were not authorized or performed. Do not claim real-device LAN access until each prerequisite and verification below is complete.

## Proposed topology

Use an existing domain controlled by the operator, e.g. `api-lan.<existing-domain>`, with split-horizon DNS: on the LAN it resolves to the host's private address; public DNS need only support DNS-01 certificate validation. Obtain a publicly trusted certificate using the existing domain's DNS-01-capable ACME flow. No paid server is required. The iOS client uses this hostname and normal ATS/system trust validation; no ATS exception, self-signed certificate, trust override, or HTTP fallback is permitted.

Add a separate HTTPS virtual host on TCP 443 in the existing edge reverse proxy. Proxy only `/api/v1/` and `/api/v2/devices/pair` to Fastify's loopback API port. Preserve the incoming `Authorization` header; do not reuse the Web `/api/v1` location that overwrites Authorization with `API_READ_TOKEN`. Do not expose PostgreSQL, the ingestion listener, administration CLI, MCP, or arbitrary Fastify routes. Keep existing Web hostname, port, and routing unchanged. The API may remain bound to loopback; do not publish the database or ingestion ports.

Set `API_TRUSTED_PROXY_IPS` to the exact source address observed by Fastify for the TLS proxy (or exact static addresses if multiple). Never use `*`, broad subnets, forwarded headers from untrusted clients, or a proxy that lets clients override forwarded proto/IP. Pairing checks Fastify's derived `request.protocol` and is intentionally rejected unless that trusted hop confirms HTTPS.

## Preflight checklist

1. Confirm domain control, DNS provider API access, current ingress/firewall owner, and a private LAN address. No credentials should be placed in this repository.
2. Confirm the reverse proxy's private-network listener and Fastify loopback target; identify its exact peer address.
3. Review the existing proxy config to prove the current Web token-injection location remains untouched.
4. Issue/renew the certificate through DNS-01, restrict private key file permissions, and configure a renewal hook.
5. Add the isolated TLS server block, preserving Authorization and limiting methods/paths. Reload only after config syntax validation.
6. Verify from LAN and an external network: valid chain/hostname, TLS >= 1.2, ATS-compatible HTTPS, HTTP refused/no downgrade, V1 protected reads, pairing route HTTPS behavior, and no open DB/ingestion ports.
7. Verify Web login/read still works with its existing shared service token. Verify relay and ingestion are unchanged.
8. Do not issue a real device pairing code until staging/production authorization is separately granted.

## Rollback

Disable the new API TLS virtual host and remove its 443 route/firewall rule; restore the prior reverse-proxy config and reload after syntax validation. Leave existing Web virtual hosts, Fastify, Worker, Relay Agent, database, and token configuration unchanged. Revoke any test device rows created in the affected non-production DB, then remove the temporary test database separately. Certificate revocation is only needed if the key is exposed or no longer controlled; ordinary route rollback does not require revoking a valid domain certificate.

## Deployment blockers

This repository/environment does not establish which FQDN/DNS provider to use, who controls firewall/DNS, or a real TLS proxy peer address. Therefore the runbook is executable once those operator facts are supplied, but deployment is deliberately deferred pending explicit staging/production authorization.
