export function updateNewsSearchParams(current: URLSearchParams, key: string, value: string): URLSearchParams {
  const next = new URLSearchParams(current);
  if (value) next.set(key, value);
  else next.delete(key);

  // Pagination changes retain the requested offset; all other filters restart at page one.
  if (key !== 'offset') next.delete('offset');
  return next;
}
