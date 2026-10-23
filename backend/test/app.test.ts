import { afterEach, describe, expect, it } from 'vitest';
import type { Pool } from 'pg';
import type { FastifyInstance } from 'fastify';
import { buildApp } from '../src/app.js';

const apps: FastifyInstance[] = [];

async function createTestApp(pool?: Pool) {
  const fakePool = pool ?? {
    query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
    connect: async () => { throw new Error('unexpected database transaction'); },
  } as unknown as Pool;
  const app = await buildApp({
    pool: fakePool,
    config: { host: '127.0.0.1', port: 3001, databaseUrl: 'postgres://unused', apiReadToken: 'test-token' },
  });
  apps.push(app);
  return app;
}

afterEach(async () => {
  await Promise.all(apps.splice(0).map((app) => app.close()));
});

describe('Fastify API boundary', () => {
  it('exposes unauthenticated readiness without exposing database details', async () => {
    const app = await createTestApp();
    const response = await app.inject({ method: 'GET', url: '/api/v1/health' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: 'ok', database: 'ok', service: 'trendradar-intelligence' });
  });

  it('reports database unavailability from health without leaking database errors', async () => {
    const fakePool = {
      query: async () => { throw Object.assign(new Error('private connection detail'), { code: 'ECONNREFUSED' }); },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({ method: 'GET', url: '/api/v1/health' });
    expect(response.statusCode).toBe(503);
    expect(response.json()).toEqual({ status: 'error', database: 'unavailable', service: 'trendradar-intelligence' });
    expect(response.body).not.toContain('private connection detail');
  });

  it('maps read-query database failures to a stable 503 error', async () => {
    const fakePool = {
      query: async () => { throw Object.assign(new Error('private connection detail'), { code: 'ECONNREFUSED' }); },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({
      method: 'GET', url: '/api/v1/news?limit=10',
      headers: { authorization: 'Bearer test-token' },
    });
    expect(response.statusCode).toBe(503);
    expect(response.json().error).toEqual({ code: 'DATABASE_UNAVAILABLE', message: 'Database is temporarily unavailable' });
    expect(response.body).not.toContain('private connection detail');
  });

  it('maps unexpected read failures to a stable 500 without exposing internals', async () => {
    const fakePool = {
      query: async () => { throw new Error('private query detail'); },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({
      method: 'GET', url: '/api/v1/news?limit=1', headers: { authorization: 'Bearer test-token' },
    });
    expect(response.statusCode).toBe(500);
    expect(response.json()).toMatchObject({ error: { code: 'INTERNAL_ERROR', message: 'Internal server error' } });
    expect(response.json().requestId).toEqual(expect.any(String));
    expect(response.body).not.toContain('private query detail');
    expect(response.json().error).not.toHaveProperty('details');
  });

  it('rejects unauthenticated business reads', async () => {
    const app = await createTestApp();
    const response = await app.inject({ method: 'GET', url: '/api/v1/news' });
    expect(response.statusCode).toBe(401);
    expect(response.json().error.code).toBe('UNAUTHORIZED');
  });

  it('preserves the actual 400, 401, and article 404 error payloads', async () => {
    const app = await createTestApp();

    const unauthorized = await app.inject({ method: 'GET', url: '/api/v1/news' });
    expect(unauthorized.statusCode).toBe(401);
    expect(unauthorized.json()).toEqual({ error: { code: 'UNAUTHORIZED', message: 'Authentication required' } });
    expect(unauthorized.json()).not.toHaveProperty('requestId');
    expect(unauthorized.json().error).not.toHaveProperty('details');

    const invalid = await app.inject({
      method: 'GET', url: '/api/v1/news?limit=0', headers: { authorization: 'Bearer test-token' },
    });
    expect(invalid.statusCode).toBe(400);
    expect(invalid.json()).toMatchObject({ error: { code: 'VALIDATION_ERROR', message: 'Invalid request parameters' } });
    expect(invalid.json().requestId).toEqual(expect.any(String));
    expect(invalid.json().error).not.toHaveProperty('details');

    const emptyPool = {
      query: async () => ({ rows: [], rowCount: 0 }),
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const missingArticleApp = await createTestApp(emptyPool);
    const missingArticle = await missingArticleApp.inject({
      method: 'GET', url: '/api/v1/news/11111111-1111-4111-8111-111111111111',
      headers: { authorization: 'Bearer test-token' },
    });
    expect(missingArticle.statusCode).toBe(404);
    expect(missingArticle.json()).toMatchObject({ error: { code: 'NOT_FOUND', message: 'Article not found' } });
    expect(missingArticle.json().requestId).toEqual(expect.any(String));
    expect(missingArticle.json().error).not.toHaveProperty('details');
  });

  it('keeps unknown-route 404 separate from the article error envelope', async () => {
    const app = await createTestApp();
    const response = await app.inject({ method: 'GET', url: '/api/v1/not-a-route' });
    expect(response.statusCode).toBe(404);
    expect(response.json()).toMatchObject({ error: 'Not Found', statusCode: 404 });
    expect(response.json()).toHaveProperty('message');
    expect(response.json()).not.toHaveProperty('requestId');
  });

  it('publishes route-specific standard error responses in OpenAPI', async () => {
    const app = await createTestApp();
    await app.ready();
    type ErrorSchema = { required?: string[]; properties?: Record<string, unknown> };
    type ErrorResponse = { content?: Record<string, { schema?: ErrorSchema }> };
    type Operation = { responses: Record<string, ErrorResponse> };
    const spec = app.swagger() as unknown as { paths: Record<string, { get: Operation }> };
    const newsResponses = spec.paths['/api/v1/news']!.get.responses;
    expect(Object.keys(newsResponses).sort()).toEqual(['200', '400', '401', '500', '503']);
    expect(Object.keys(spec.paths['/api/v1/news/{id}']!.get.responses).sort()).toEqual(['200', '400', '401', '404', '500', '503']);
    expect(Object.keys(spec.paths['/api/v1/categories']!.get.responses).sort()).toEqual(['200', '401', '500', '503']);
    const unauthorized = newsResponses['401']!.content!['application/json']!.schema!;
    const validation = newsResponses['400']!.content!['application/json']!.schema!;
    expect(unauthorized.required).toEqual(['error']);
    expect(validation.required).toEqual(['error', 'requestId']);
    expect((validation.properties!.error as ErrorSchema).properties).not.toHaveProperty('details');
  });

  it('protects the system status endpoint with the read token', async () => {
    const app = await createTestApp();
    const response = await app.inject({ method: 'GET', url: '/api/v1/status' });
    expect(response.statusCode).toBe(401);
  });

  it('returns a stable analysis error code when a model run failed', async () => {
    const fakePool = {
      query: async (sql: string) => sql.includes('FROM article_analyses')
        ? { rows: [{ status: 'failed', errorCode: 'MODEL_REQUEST_FAILED' }], rowCount: 1 }
        : { rows: [], rowCount: 0 },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({
      method: 'GET', url: '/api/v1/news/11111111-1111-4111-8111-111111111111/analysis',
      headers: { authorization: 'Bearer test-token' },
    });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: 'failed', errorCode: 'MODEL_REQUEST_FAILED' });
  });

  it('omits a null analysis error code and preserves null fields', async () => {
    const fakePool = {
      query: async (sql: string) => sql.includes('FROM article_analyses')
        ? { rows: [{
          status: 'processing', errorCode: null, summary: null, keyFacts: [], background: null,
          potentialImpacts: [], uncertainties: [], evidenceScope: 'title', modelName: 'fixture',
          promptVersion: 'v1', analysisVersion: 1, analyzedAt: null,
        }], rowCount: 1 }
        : { rows: [], rowCount: 0 },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({
      method: 'GET', url: '/api/v1/news/11111111-1111-4111-8111-111111111111/analysis',
      headers: { authorization: 'Bearer test-token' },
    });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({ status: 'processing', summary: null, background: null, analyzedAt: null });
    expect(response.json()).not.toHaveProperty('errorCode');
  });

  it('validates list parameters before reaching the repository', async () => {
    const app = await createTestApp();
    const response = await app.inject({
      method: 'GET',
      url: '/api/v1/news?limit=0',
      headers: { authorization: 'Bearer test-token' },
    });
    expect(response.statusCode).toBe(400);
    expect(response.json().error.code).toBe('VALIDATION_ERROR');
  });

  it('does not expose the private ingestion route on the public API listener', async () => {
    const app = await createTestApp();
    const response = await app.inject({ method: 'POST', url: '/api/v1/internal/ingestion', payload: {} });
    expect(response.statusCode).toBe(404);
  });

  it('returns source and AI summaries as distinct fields in the news list', async () => {
    const statements: string[] = [];
    const fakePool = {
      query: async (sql: string) => {
        statements.push(sql);
        if (sql.includes('count(*)::text')) return { rows: [{ total: '1' }], rowCount: 1 };
        if (sql.includes('FROM articles a')) return { rows: [{
          id: '11111111-1111-4111-8111-111111111111', title: 'AI article',
          source_id: 'rss:example', source_name: 'Example RSS', source_type: 'rss',
          published_at: null, first_seen_at: new Date('2026-10-20T00:00:00Z'),
          url: 'https://example.com/article', summary: 'Source-provided summary',
          analysis_summary: 'AI-generated summary', content_status: 'summary_available',
          analysis_status: 'completed', categories: [],
        }], rowCount: 1 };
        return { rows: [], rowCount: 1 };
      },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({
      method: 'GET', url: '/api/v1/news?limit=10',
      headers: { authorization: 'Bearer test-token' },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json().data[0]).toMatchObject({
      summary: 'Source-provided summary',
      analysisSummary: 'AI-generated summary',
      analysisStatus: 'completed',
    });
    expect(statements.some((sql) => sql.includes('FROM article_analyses aa'))).toBe(true);
  });

  it('searches title, source summary, and completed AI summary with the same paginated filter', async () => {
    const queries: Array<{ sql: string; values: unknown[] }> = [];
    const fakePool = {
      query: async (sql: string, values: unknown[] = []) => {
        queries.push({ sql, values });
        if (sql.includes('count(*)::text')) return { rows: [{ total: '0' }], rowCount: 1 };
        return { rows: [], rowCount: 0 };
      },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({
      method: 'GET', url: '/api/v1/search?q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD&limit=20&offset=40',
      headers: { authorization: 'Bearer test-token' },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json().pagination).toMatchObject({ total: 0, limit: 20, offset: 40, hasMore: false });
    expect(queries).toHaveLength(2);
    expect(queries[0]?.sql).toContain('position(lower($1) in lower(a.title))');
    expect(queries[0]?.sql).toContain("position(lower($1) in lower(COALESCE(a.summary, '')))");
    expect(queries[0]?.sql).toContain('article_analyses aas');
    expect(queries[0]?.values).toEqual(['人工智能']);
    expect(queries[1]?.values).toEqual(['人工智能', 20, 40]);
  });
});
