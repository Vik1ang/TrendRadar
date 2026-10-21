import { describe, expect, it } from 'vitest';
import { updateNewsSearchParams } from './news-query';

describe('news list query state', () => {
  it('advances and returns pages while retaining all active filters', () => {
    const current = new URLSearchParams('q=DeepSeek&category=ai&source=hotlist%3Azhihu&from=2026-10-01&to=2026-10-20&offset=0');
    const secondPage = updateNewsSearchParams(current, 'offset', '30');
    expect(secondPage.get('offset')).toBe('30');
    expect(secondPage.get('q')).toBe('DeepSeek');
    expect(secondPage.get('category')).toBe('ai');
    expect(secondPage.get('source')).toBe('hotlist:zhihu');
    expect(secondPage.get('from')).toBe('2026-10-01');
    expect(secondPage.get('to')).toBe('2026-10-20');
    expect(updateNewsSearchParams(secondPage, 'offset', '0').get('offset')).toBe('0');
  });

  it('resets pagination when a search or filter changes', () => {
    const current = new URLSearchParams('q=DeepSeek&category=ai&offset=30');
    expect(updateNewsSearchParams(current, 'category', 'technology').toString()).toBe('q=DeepSeek&category=technology');
    expect(updateNewsSearchParams(current, 'q', 'NVIDIA').toString()).toBe('q=NVIDIA&category=ai');
    expect(updateNewsSearchParams(current, 'source', '').has('offset')).toBe(false);
  });
});
