import { afterEach, describe, expect, it } from 'vitest';
import type { Pool } from 'pg';
import type { FastifyInstance } from 'fastify';
import { buildIngestionApp } from '../src/ingestion-app.js';

const apps: FastifyInstance[] = [];

async function createTestApp(pool?: Pool) {
  const fakePool = pool ?? {
    query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
    connect: async () => { throw new Error('unexpected database transaction'); },
  } as unknown as Pool;
  const app = await buildIngestionApp(fakePool, {
    host: '127.0.0.1', port: 3002, databaseUrl: 'postgres://unused', ingestionToken: 'ingest-test-token',
    analysisMaxPerRun: 1, analysisMaxPerDay: 0, analysisMaxConcurrent: 1, analysisMaxAttempts: 2,
  });
  apps.push(app);
  return app;
}

afterEach(async () => {
  await Promise.all(apps.splice(0).map((app) => app.close()));
});

describe('private ingestion API', () => {
  const baseArticle = (id: string) => ({
    id,
    sourceId: 'rss:test',
    title: 'Test article',
    url: 'https://example.com/article',
    normalizedUrl: 'https://example.com/article',
    dedupeKey: `sha256:${id}`,
    firstSeenAt: '2026-10-20T00:00:00Z',
    contentStatus: 'title_only' as const,
    analysisStatus: 'not_requested' as const,
    categories: [],
  });

  const ingestionPayload = (articles: ReturnType<typeof baseArticle>[]) => ({
    run: {
      id: '22222222-2222-4222-8222-222222222222',
      startedAt: '2026-10-20T00:00:00Z',
      finishedAt: '2026-10-20T00:01:00Z',
      status: 'completed' as const,
      sourceCount: 1,
    },
    sources: [{ id: 'rss:test', name: 'Test RSS', type: 'rss' as const }],
    articles,
  });

  it('requires the independent ingestion credential', async () => {
    const app = await createTestApp();
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      payload: {
        run: { id: '22222222-2222-4222-8222-222222222222', startedAt: '2026-10-20T00:00:00Z', finishedAt: '2026-10-20T00:01:00Z', status: 'completed', sourceCount: 0 },
        sources: [], articles: [],
      },
    });
    expect(response.statusCode).toBe(401);
    expect(response.json().error.code).toBe('UNAUTHORIZED');
  });

  it('validates imports at the boundary', async () => {
    const app = await createTestApp();
    const response = await app.inject({ method: 'POST', url: '/api/v1/internal/ingestion', payload: {} });
    expect(response.statusCode).toBe(400);
    expect(response.json().error.code).toBe('VALIDATION_ERROR');
  });

  it('accepts valid, null, and omitted publication times in one atomic batch', async () => {
    const insertedValues: unknown[][] = [];
    let connectCount = 0;
    const fakeClient = {
      query: async (sql: string, values?: unknown[]) => {
        if (sql.includes('INSERT INTO articles')) {
          insertedValues.push(values ?? []);
          return { rows: [{ id: values?.[0], inserted: true }], rowCount: 1 };
        }
        return { rows: [], rowCount: 1 };
      },
      release: () => undefined,
    };
    const pool = {
      query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
      connect: async () => { connectCount += 1; return fakeClient; },
    } as unknown as Pool;
    const app = await createTestApp(pool);
    const valid = { ...baseArticle('11111111-1111-4111-8111-111111111111'), publishedAt: '2026-10-20T08:30:00Z' };
    const unknown = {
      ...baseArticle('11111111-1111-4111-8111-111111111112'),
      summary: null,
      publishedAt: null,
    };
    const omitted = baseArticle('11111111-1111-4111-8111-111111111113');
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: ingestionPayload([valid, unknown, omitted]),
    });

    expect(response.statusCode).toBe(201);
    expect(connectCount).toBe(1);
    expect(insertedValues).toHaveLength(3);
    expect(insertedValues.map((values) => values[7])).toEqual([
      '2026-10-20T08:30:00Z', null, null,
    ]);
    expect(insertedValues[1]?.[6]).toBeNull();
  });

  it('rejects invalid publication times before opening a transaction', async () => {
    let connectCount = 0;
    const pool = {
      query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
      connect: async () => { connectCount += 1; throw new Error('invalid batch must not write'); },
    } as unknown as Pool;
    const app = await createTestApp(pool);
    const invalid = { ...baseArticle('11111111-1111-4111-8111-111111111114'), publishedAt: 'not-a-date' };
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: ingestionPayload([baseArticle('11111111-1111-4111-8111-111111111115'), invalid]),
    });

    expect(response.statusCode).toBe(400);
    expect(response.json().error.code).toBe('VALIDATION_ERROR');
    expect(connectCount).toBe(0);
  });

  it('returns a structured 400 for malformed JSON imports', async () => {
    const app = await createTestApp();
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token', 'content-type': 'application/json' },
      payload: '{invalid',
    });
    expect(response.statusCode).toBe(400);
    expect(response.json().error.code).toBe('VALIDATION_ERROR');
  });

  it('maps private database failures to a stable 503 response', async () => {
    const fakePool = {
      query: async () => { throw Object.assign(new Error('private connection detail'), { code: 'ECONNREFUSED' }); },
      connect: async () => { throw new Error('unexpected database transaction'); },
    } as unknown as Pool;
    const app = await createTestApp(fakePool);
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/analysis-candidates',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: { modelName: 'fixture', promptVersion: 'v1', analysisVersion: 1 },
    });
    expect(response.statusCode).toBe(503);
    expect(response.json().error).toEqual({ code: 'DATABASE_UNAVAILABLE', message: 'Database is temporarily unavailable' });
    expect(response.body).not.toContain('private connection detail');
  });

  it('does not downgrade prior analysis when a reimport skips analysis', async () => {
    const statements: string[] = [];
    const fakeClient = {
      query: async (sql: string) => {
        statements.push(sql);
        if (sql.includes('INSERT INTO articles')) {
          return { rows: [{ id: '11111111-1111-4111-8111-111111111111', inserted: false }], rowCount: 1 };
        }
        return { rows: [], rowCount: 1 };
      },
      release: () => undefined,
    };
    const pool = {
      query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
      connect: async () => fakeClient,
    } as unknown as Pool;
    const app = await createTestApp(pool);
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: {
        run: { id: '22222222-2222-4222-8222-222222222222', startedAt: '2026-10-20T00:00:00Z', finishedAt: '2026-10-20T00:01:00Z', status: 'completed', sourceCount: 1 },
        sources: [{ id: 'hotlist:test', name: 'Test', type: 'hotlist' }],
        articles: [{
          id: '11111111-1111-4111-8111-111111111111', sourceId: 'hotlist:test', title: 'Test article',
          dedupeKey: 'sha256:test', firstSeenAt: '2026-10-20T00:00:00Z', contentStatus: 'title_only',
          analysisStatus: 'not_requested', categories: [{ id: 'ai', assignedBy: 'rule' }],
        }],
      },
    });

    expect(response.statusCode).toBe(201);
    const articleUpsert = statements.find((sql) => sql.includes('INSERT INTO articles'))?.replace(/\s+/g, ' ');
    expect(articleUpsert).toContain("WHEN EXCLUDED.analysis_status='not_requested' THEN articles.analysis_status");
    expect(articleUpsert).toContain("EXCLUDED.analysis_status IN ('pending','processing') AND articles.analysis_status IN ('completed','failed')");
    expect(statements.some((sql) => sql.includes('DELETE FROM article_categories'))).toBe(false);
    expect(statements.some((sql) => sql.includes('INSERT INTO article_analysis_jobs'))).toBe(false);
  });

  it('persists source snapshot and dedupe telemetry in ingestion run metadata', async () => {
    let runSql = '';
    let runValues: unknown[] = [];
    const fakeClient = {
      query: async (sql: string, values?: unknown[]) => {
        if (sql.includes('INSERT INTO ingestion_runs')) {
          runSql = sql;
          runValues = values ?? [];
        }
        return { rows: [], rowCount: 1 };
      },
      release: () => undefined,
    };
    const pool = {
      query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
      connect: async () => fakeClient,
    } as unknown as Pool;
    const app = await createTestApp(pool);
    const metadata = {
      sqliteSync: {
        sourceDatabaseDate: '2026-10-22',
        sources: [{ source: 'news', recordId: 29, crawlTime: '14-02', readCount: 10, failedCount: 0 }],
        readCount: 10,
        deduplicatedCount: 2,
        failureCount: 0,
      },
    };
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: {
        run: { id: '22222222-2222-4222-8222-222222222222', startedAt: '2026-10-20T00:00:00Z',
          finishedAt: '2026-10-20T00:01:00Z', status: 'completed', sourceCount: 1, metadata },
        sources: [], articles: [],
      },
    });
    expect(response.statusCode).toBe(201);
    expect(runSql).toContain('error_summary,metadata');
    expect(JSON.parse(String(runValues[11]))).toEqual(metadata);
  });

  it('fails closed when the server-side daily analysis budget is zero', async () => {
    const app = await createTestApp();
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/analysis-start',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: {
        runId: '22222222-2222-4222-8222-222222222222',
        articleId: '11111111-1111-4111-8111-111111111111',
        modelName: 'fixture', promptVersion: 'v1', analysisVersion: 1,
      },
    });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ started: false, reason: 'DAILY_BUDGET' });
  });

  it('queues newly inserted articles only when analysis is requested', async () => {
    const statements: string[] = [];
    const fakeClient = {
      query: async (sql: string) => {
        statements.push(sql);
        if (sql.includes('INSERT INTO articles')) {
          return { rows: [{ id: '11111111-1111-4111-8111-111111111111', inserted: true }], rowCount: 1 };
        }
        return { rows: [], rowCount: 1 };
      },
      release: () => undefined,
    };
    const pool = {
      query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
      connect: async () => fakeClient,
    } as unknown as Pool;
    const app = await createTestApp(pool);
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token', 'content-type': 'application/json' },
      payload: {
        run: { id: '22222222-2222-4222-8222-222222222222', startedAt: '2026-10-20T00:00:00Z', finishedAt: '2026-10-20T00:01:00Z', status: 'completed', sourceCount: 1 },
        sources: [{ id: 'hotlist:test', name: 'Test', type: 'hotlist' }],
        articles: [{
          id: '11111111-1111-4111-8111-111111111111', sourceId: 'hotlist:test', title: 'AI article',
          dedupeKey: 'sha256:new', firstSeenAt: '2026-10-20T00:00:00Z', contentStatus: 'title_only',
          analysisStatus: 'pending', categories: [{ id: 'ai', assignedBy: 'rule' }],
        }],
      },
    });
    expect(response.statusCode).toBe(201);
    const jobInsert = statements.find((sql) => sql.includes('INSERT INTO article_analysis_jobs'));
    expect(jobInsert).toContain('ON CONFLICT (article_id) DO NOTHING');
  });

  it('does not create analysis jobs for metadata-only history imports', async () => {
    const statements: string[] = [];
    const fakeClient = {
      query: async (sql: string) => {
        statements.push(sql);
        if (sql.includes('INSERT INTO articles')) {
          return { rows: [{ id: '11111111-1111-4111-8111-111111111111', inserted: true }], rowCount: 1 };
        }
        return { rows: [], rowCount: 1 };
      },
      release: () => undefined,
    };
    const pool = {
      query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
      connect: async () => fakeClient,
    } as unknown as Pool;
    const app = await createTestApp(pool);
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: {
        run: { id: '22222222-2222-4222-8222-222222222222', startedAt: '2026-10-20T00:00:00Z', finishedAt: '2026-10-20T00:01:00Z', status: 'completed', sourceCount: 1 },
        sources: [{ id: 'hotlist:test', name: 'Test', type: 'hotlist' }],
        articles: [{
          id: '11111111-1111-4111-8111-111111111111', sourceId: 'hotlist:test', title: 'Historical item',
          dedupeKey: 'sha256:history', firstSeenAt: '2026-10-20T00:00:00Z', contentStatus: 'title_only',
          analysisStatus: 'not_requested', categories: [],
        }],
      },
    });
    expect(response.statusCode).toBe(201);
    expect(statements.some((sql) => sql.includes('INSERT INTO article_analysis_jobs'))).toBe(false);
  });

  it('preserves an existing live analysis status on metadata-only history upsert', async () => {
    const statements: string[] = [];
    const fakeClient = {
      query: async (sql: string) => {
        statements.push(sql);
        if (sql.includes('INSERT INTO articles')) {
          return { rows: [{ id: '11111111-1111-4111-8111-111111111111', inserted: false }], rowCount: 1 };
        }
        return { rows: [], rowCount: 1 };
      },
      release: () => undefined,
    };
    const pool = {
      query: async () => ({ rows: [{ '?column?': 1 }], rowCount: 1 }),
      connect: async () => fakeClient,
    } as unknown as Pool;
    const app = await createTestApp(pool);
    const response = await app.inject({
      method: 'POST', url: '/api/v1/internal/ingestion',
      headers: { authorization: 'Bearer ingest-test-token' },
      payload: {
        run: { id: '22222222-2222-4222-8222-222222222222', startedAt: '2026-10-20T00:00:00Z', finishedAt: '2026-10-20T00:01:00Z', status: 'completed', sourceCount: 1 },
        sources: [{ id: 'hotlist:test', name: 'Test', type: 'hotlist' }],
        articles: [{
          id: '11111111-1111-4111-8111-111111111111', sourceId: 'hotlist:test', title: 'Existing live item',
          dedupeKey: 'sha256:existing-live', firstSeenAt: '2026-10-20T00:00:00Z', contentStatus: 'title_only',
          analysisStatus: 'not_requested', categories: [],
        }],
      },
    });
    expect(response.statusCode).toBe(201);
    const articleUpsert = statements.find((sql) => sql.includes('INSERT INTO articles'))?.replace(/\s+/g, ' ');
    expect(articleUpsert).toContain("WHEN EXCLUDED.analysis_status='not_requested' THEN articles.analysis_status");
    expect(statements.some((sql) => sql.includes('INSERT INTO article_analysis_jobs'))).toBe(false);
  });
});
