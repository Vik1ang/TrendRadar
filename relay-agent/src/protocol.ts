const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}';
const ALLOWED = [
  /^\/api\/v1\/(health|status|categories|sources)$/,
  /^\/api\/v1\/(news|search)$/,
  new RegExp(`^/api/v1/news/${UUID}(?:/analysis)?$`, 'i'),
];

export type RelayCommand = { id: string; method: 'GET'; path: string; authorization: string };

export function parseRelayCommand(data: string): RelayCommand | null {
  try {
    const value = JSON.parse(data) as Partial<RelayCommand>;
    if (value.method !== 'GET' || typeof value.id !== 'string' || value.id.length > 80 || typeof value.path !== 'string'
      || !isAllowedApiPath(value.path) || typeof value.authorization !== 'string'
      || !/^Bearer\s+[^\s]{1,480}$/i.test(value.authorization)) return null;
    return { id: value.id, method: 'GET', path: value.path, authorization: value.authorization };
  } catch { return null; }
}

export function isAllowedApiPath(path: string): boolean {
  if (!path.startsWith('/') || path.startsWith('//') || path.length > 2048) return false;
  try {
    const target = new URL(path, 'http://api.internal');
    if (target.origin !== 'http://api.internal' || !ALLOWED.some((pattern) => pattern.test(target.pathname))) return false;
    const counts = new Map<string, number>();
    let valid = true;
    target.searchParams.forEach((_value, key) => {
      const count = (counts.get(key) ?? 0) + 1;
      counts.set(key, count);
      if (!['q', 'category', 'source', 'from', 'to', 'limit', 'offset'].includes(key) || count > 1) valid = false;
    });
    return valid;
  } catch { return false; }
}
