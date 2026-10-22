import { describe, expect, it } from 'vitest';
import type { Pool } from 'pg';
import { IngestionRepository, type AnalysisBudget, type AnalysisStart } from '../src/repositories/ingestion-repository.js';

const runId = '22222222-2222-4222-8222-222222222222';
const articleId = '11111111-1111-4111-8111-111111111111';
const input: AnalysisStart = {
  runId, articleId, modelName: 'test/model', promptVersion: 'test-v1', analysisVersion: 1,
};

class BudgetDatabase {
  attempts: Array<AnalysisStart & { status: string }> = [];
  job = { attempts: 0, status: 'queued', available_at: new Date(0), lease_expires_at: null as Date | null };

  pool(): Pool {
    const client = {
      query: async (sql: string, params: unknown[] = []) => {
        if (/^(BEGIN|COMMIT|ROLLBACK)$/.test(sql.trim())) return { rows: [], rowCount: 0 };
        if (sql.includes('pg_advisory_xact_lock')) return { rows: [], rowCount: 1 };
        if (sql.includes('FROM article_analysis_attempts') && sql.includes('started_at >=')) {
          return { rows: [{ count: String(this.attempts.length) }], rowCount: 1 };
        }
        if (sql.includes('FROM article_analysis_attempts WHERE run_id')) {
          return { rows: [{ count: String(this.attempts.filter((attempt) => attempt.runId === params[0]).length) }], rowCount: 1 };
        }
        if (sql.includes('FROM article_analysis_jobs') && sql.includes("status = 'processing'")) {
          return { rows: [{ count: String(this.job.status === 'processing' ? 1 : 0) }], rowCount: 1 };
        }
        if (sql.includes('FROM article_analysis_jobs WHERE article_id')) {
          return { rows: [this.job], rowCount: 1 };
        }
        if (sql.includes('FROM article_analyses')) return { rows: [], rowCount: 0 };
        if (sql.includes('INSERT INTO article_analysis_attempts')) {
          const attempt: AnalysisStart & { status: string } = {
            runId: String(params[1]), articleId: String(params[2]), modelName: String(params[3]),
            promptVersion: String(params[4]), analysisVersion: Number(params[5]), status: 'processing',
          };
          if (this.attempts.some((row) => row.runId === attempt.runId && row.articleId === attempt.articleId)) {
            return { rows: [], rowCount: 0 };
          }
          this.attempts.push(attempt);
          return { rows: [{ id: params[0] }], rowCount: 1 };
        }
        if (sql.includes('UPDATE article_analysis_jobs SET status=\'processing\'')) {
          this.job = { ...this.job, status: 'processing', attempts: this.job.attempts + 1, lease_expires_at: new Date(Date.now() + 60_000) };
          return { rows: [], rowCount: 1 };
        }
        throw new Error(`Unhandled test SQL: ${sql}`);
      },
      release: () => undefined,
    };
    return {
      query: async () => ({ rows: [], rowCount: 0 }),
      connect: async () => client,
    } as unknown as Pool;
  }
}

function repository(database: BudgetDatabase, budget: AnalysisBudget): IngestionRepository {
  return new IngestionRepository(database.pool(), budget);
}

describe('durable analysis budget reservation', () => {
  it('enforces the daily cap before another model call can start', async () => {
    const database = new BudgetDatabase();
    const repo = repository(database, { maxPerRun: 5, maxPerDay: 1, maxConcurrent: 1, maxAttempts: 2 });
    expect(await repo.startAnalysis(input)).toEqual({ started: true });
    database.job = { ...database.job, status: 'queued' };
    expect(await repo.startAnalysis({ ...input, runId: '33333333-3333-4333-8333-333333333333' }))
      .toEqual({ started: false, reason: 'DAILY_BUDGET' });
    expect(database.attempts).toHaveLength(1);
  });

  it('enforces per-run limit independently from the daily limit', async () => {
    const database = new BudgetDatabase();
    const repo = repository(database, { maxPerRun: 1, maxPerDay: 10, maxConcurrent: 1, maxAttempts: 2 });
    expect(await repo.startAnalysis(input)).toEqual({ started: true });
    database.job = { ...database.job, status: 'queued' };
    expect(await repo.startAnalysis({ ...input, articleId: '44444444-4444-4444-8444-444444444444' }))
      .toEqual({ started: false, reason: 'RUN_BUDGET' });
  });

  it('fails closed when no daily budget is configured', async () => {
    const database = new BudgetDatabase();
    const repo = repository(database, { maxPerRun: 1, maxPerDay: 0, maxConcurrent: 1, maxAttempts: 2 });
    expect(await repo.startAnalysis(input)).toEqual({ started: false, reason: 'DAILY_BUDGET' });
    expect(database.attempts).toHaveLength(0);
  });

  it('does not claim the same article while its processing lease is active', async () => {
    const database = new BudgetDatabase();
    const repo = repository(database, { maxPerRun: 5, maxPerDay: 10, maxConcurrent: 2, maxAttempts: 2 });
    expect(await repo.startAnalysis(input)).toEqual({ started: true });
    expect(await repo.startAnalysis({ ...input, runId: '33333333-3333-4333-8333-333333333333' }))
      .toEqual({ started: false, reason: 'NOT_ELIGIBLE' });
    expect(database.attempts).toHaveLength(1);
  });

  it('stops retrying an article after its configured attempt cap', async () => {
    const database = new BudgetDatabase();
    database.job = { ...database.job, attempts: 2, status: 'failed' };
    const repo = repository(database, { maxPerRun: 5, maxPerDay: 10, maxConcurrent: 1, maxAttempts: 2 });
    expect(await repo.startAnalysis(input)).toEqual({ started: false, reason: 'ATTEMPTS_EXHAUSTED' });
    expect(database.attempts).toHaveLength(0);
  });
});
