const DEVICE_BEARER = /^Bearer trd_dev_[A-Za-z0-9_-]{43}$/;

/**
 * Device credentials are accepted at the Relay only as a syntactically
 * valid forwarding candidate. Fastify remains the authority for existence,
 * expiry, revocation, and read scope.
 */
export function isDeviceBearerCandidate(authorization: string | null): boolean {
  if (authorization === null || !/^Bearer /i.test(authorization)) return false;
  return DEVICE_BEARER.test(`Bearer ${authorization.slice(7)}`);
}

export async function tokenMatches(candidate: string | null, expected: string): Promise<boolean> {
  if (!candidate || !expected || candidate.length > 512) return false;
  const encoder = new TextEncoder();
  const [left, right] = await Promise.all([
    crypto.subtle.digest('SHA-256', encoder.encode(candidate)),
    crypto.subtle.digest('SHA-256', encoder.encode(expected)),
  ]);
  const a = new Uint8Array(left); const b = new Uint8Array(right);
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a[index]! ^ b[index]!;
  return difference === 0 && candidate.length === expected.length;
}

/**
 * Preserve the legacy shared-token path while allowing only the strict
 * device-token shape to reach the authoritative Fastify verifier.
 */
export async function clientReadAuthorizationAccepted(
  authorization: string | null,
  expectedClientToken: string,
): Promise<boolean> {
  if (isDeviceBearerCandidate(authorization)) return true;
  return tokenMatches(authorization?.replace(/^Bearer\s+/i, '') ?? null, expectedClientToken);
}
