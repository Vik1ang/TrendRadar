import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('browser API client', () => {
  it('sends server-side filters and returns the shared page contract', async () => {
    const payload = { data: [], pagination: { limit: 30, offset: 0, total: 0, hasMore: false } };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.list(new URLSearchParams({ q: '人工智能', category: 'ai' }))).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/news?q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD&category=ai', {
      headers: { Accept: 'application/json' },
    });
  });

  it('surfaces structured API failures rather than hiding them as empty results', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { message: 'Database is temporarily unavailable' } }), { status: 503 })));
    await expect(api.status()).rejects.toThrow('Database is temporarily unavailable');
  });
});
