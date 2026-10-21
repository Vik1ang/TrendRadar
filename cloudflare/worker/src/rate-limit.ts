export const RATE_LIMIT_PER_MINUTE = 30;

export function nextRateBucket(
  current: { window: number; hits: number } | undefined,
  window: number,
): { allowed: boolean; hits: number } {
  if (current?.window === window && current.hits >= RATE_LIMIT_PER_MINUTE) return { allowed: false, hits: current.hits };
  return { allowed: true, hits: current?.window === window ? current.hits + 1 : 1 };
}
