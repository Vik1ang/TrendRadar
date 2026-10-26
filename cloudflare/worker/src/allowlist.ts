const MAX_URL_LENGTH = 2048;
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}';
const READ_PATHS = [
  /^\/api\/v1\/(health|status|categories|sources)$/,
  /^\/api\/v1\/(news|search)$/,
  new RegExp(`^/api/v1/news/${UUID}(?:/analysis)?$`, 'i'),
];

const DEVICE_READ_PATHS = [
  /^\/api\/v1\/news$/,
  /^\/api\/v1\/search$/,
  new RegExp(`^/api/v1/news/${UUID}(?:/analysis)?$`, 'i'),
  /^\/api\/v1\/(categories|sources)$/,
  /^\/api\/v1\/status$/,
];

export function allowedReadPath(pathname: string): boolean {
  return pathname.length <= MAX_URL_LENGTH && READ_PATHS.some((pattern) => pattern.test(pathname));
}

export function allowedReadRequest(method: string, pathname: string): boolean {
  return method === 'GET' && pathname.startsWith('/api/v1/');
}

/** Do not forward device-shaped bearers to unauthenticated health; Fastify enforces status scope. */
export function allowedDeviceReadPath(pathname: string): boolean {
  return DEVICE_READ_PATHS.some((pattern) => pattern.test(pathname));
}

export function allowedReadQuery(query: URLSearchParams): boolean {
  const allowed = new Set(['q', 'category', 'source', 'from', 'to', 'limit', 'offset']);
  const seen = new Set<string>();
  let valid = true;
  query.forEach((_value, key) => {
    if (!allowed.has(key) || seen.has(key)) valid = false;
    seen.add(key);
  });
  return valid;
}
