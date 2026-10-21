import { describe, expect, it } from 'vitest';
import { allowedReadPath, allowedReadQuery } from '../src/allowlist';
import { RequestCorrelator } from '../src/correlator';
import { nextRateBucket } from '../src/rate-limit';

describe('Relay route allowlist', () => {
  it('accepts only client-facing read API resource paths', () => {
    expect(allowedReadPath('/api/v1/news')).toBe(true);
    expect(allowedReadPath('/api/v1/news/0f8fad5b-d9cb-469f-a165-70867728950e/analysis')).toBe(true);
    expect(allowedReadPath('/api/v1/internal/ingestion')).toBe(false);
    expect(allowedReadPath('/api/v1/news/anything')).toBe(false);
    expect(allowedReadPath('/api/v1/news/0f8fad5b-d9cb-469f-a165-70867728950e/delete')).toBe(false);
    expect(allowedReadPath('/api/v1/health/../../internal')).toBe(false);
  });

  it('rejects unsupported or duplicate query keys', () => {
    expect(allowedReadQuery(new URLSearchParams('q=DeepSeek&limit=10'))).toBe(true);
    expect(allowedReadQuery(new URLSearchParams('target=http://127.0.0.1'))).toBe(false);
    expect(allowedReadQuery(new URLSearchParams('limit=10&limit=20'))).toBe(false);
  });
});

describe('request correlation', () => {
  it('matches out-of-order responses by unique request id', async () => {
    const correlator = new RequestCorrelator<string>();
    const first = correlator.wait('request-a', 1000, 'timeout-a');
    const second = correlator.wait('request-b', 1000, 'timeout-b');
    expect(correlator.resolve('request-b', 'response-b')).toBe(true);
    expect(correlator.resolve('request-a', 'response-a')).toBe(true);
    await expect(Promise.all([first, second])).resolves.toEqual(['response-a', 'response-b']);
    expect(correlator.size).toBe(0);
  });

  it('clears correlation state on timeout and socket failure', async () => {
    const correlator = new RequestCorrelator<string>();
    await expect(correlator.wait('slow', 5, 'timed-out')).resolves.toBe('timed-out');
    const pending = correlator.wait('active', 1000, 'timed-out');
    correlator.resolveAll('disconnected');
    await expect(pending).resolves.toBe('disconnected');
    expect(correlator.size).toBe(0);
  });
});

describe('durable rate window', () => {
  it('caps authenticated Relay API dispatch at 30 requests per minute and resets on the next window', () => {
    expect(nextRateBucket(undefined, 10)).toEqual({ allowed: true, hits: 1 });
    expect(nextRateBucket({ window: 10, hits: 29 }, 10)).toEqual({ allowed: true, hits: 30 });
    expect(nextRateBucket({ window: 10, hits: 30 }, 10)).toEqual({ allowed: false, hits: 30 });
    expect(nextRateBucket({ window: 10, hits: 30 }, 11)).toEqual({ allowed: true, hits: 1 });
  });
});
