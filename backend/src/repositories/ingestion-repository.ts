import { randomUUID } from 'node:crypto';
import type { Pool } from 'pg';

export type SourceInput = { id: string; name: string; type: 'hotlist' | 'rss' };
export type AnalysisInput = {
  summary?: string;
  keyFacts: string[];
  background?: string;
  potentialImpacts: string[];
  uncertainties: string[];
  evidenceScope: 'title' | 'summary' | 'content';
  modelName: string;
  promptVersion: string;
  analysisVersion: number;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  errorCode?: string;
  analyzedAt?: string;
};
export type ArticleInput = {
  id: string;
  sourceId: string;
  title: string;
  url?: string;
  normalizedUrl?: string;
  dedupeKey: string;
  summary?: string | null;
  publishedAt?: string | null;
  firstSeenAt: string;
  contentStatus: 'title_only' | 'summary_available' | 'content_available';
  analysisStatus: 'pending' | 'processing' | 'completed' | 'failed' | 'not_requested';
  replaceCategories?: boolean;
  categories: Array<{ id: string; confidence?: number; assignedBy?: string }>;
  analysis?: AnalysisInput;
};
export type IngestionInput = {
  run: { id: string; startedAt: string; finishedAt: string; status: 'running' | 'completed' | 'partial' | 'failed'; sourceCount: number; errorSummary?: string; metadata?: Record<string, unknown> };
  sources: SourceInput[];
  articles: ArticleInput[];
};

export type AnalysisCandidate = {
  id: string;
  sourceId: string;
  sourceName: string;
  title: string;
  summary: string | null;
  categories: Array<{ id: string; confidence?: number; assignedBy: string }>;
};

export type AnalysisBudget = {
  maxPerRun: number;
  maxPerDay: number;
  maxConcurrent: number;
  maxAttempts: number;
};

export type AnalysisStart = {
  runId: string;
  articleId: string;
  modelName: string;
  promptVersion: string;
  analysisVersion: number;
};

export type AnalysisStartResult = { started: true } | { started: false; reason: 'DAILY_BUDGET' | 'RUN_BUDGET' | 'CONCURRENCY_LIMIT' | 'NOT_ELIGIBLE' | 'ATTEMPTS_EXHAUSTED' | 'ALREADY_CLAIMED' };

export class IngestionRepository {
  constructor(
    private readonly pool: Pool,
    private readonly budget: AnalysisBudget = { maxPerRun: 1, maxPerDay: 0, maxConcurrent: 1, maxAttempts: 2 },
  ) {}

  async analysisCandidates(input: { modelName: string; promptVersion: string; analysisVersion: number }): Promise<AnalysisCandidate[]> {
    const result = await this.pool.query<AnalysisCandidate>(
      `SELECT a.id, a.source_id AS "sourceId", s.name AS "sourceName", a.title, a.summary,
              COALESCE(jsonb_agg(jsonb_strip_nulls(jsonb_build_object('id', c.id, 'confidence', ac.confidence, 'assignedBy', ac.assigned_by))
                ORDER BY c.sort_order) FILTER (WHERE c.id IS NOT NULL), '[]'::jsonb) AS categories
         FROM article_analysis_jobs j
         JOIN articles a ON a.id = j.article_id
         JOIN sources s ON s.id = a.source_id
         LEFT JOIN article_categories ac ON ac.article_id = a.id
         LEFT JOIN categories c ON c.id = ac.category_id
        WHERE j.status <> 'completed'
          AND j.attempts < $4
          AND (j.status <> 'processing' OR j.lease_expires_at <= now())
          AND j.available_at <= now()
          AND (j.model_name IS NULL OR (j.model_name = $1 AND j.prompt_version = $2 AND j.analysis_version = $3))
          AND NOT EXISTS (
            SELECT 1 FROM article_analyses aa
             WHERE aa.article_id = a.id AND aa.model_name = $1 AND aa.prompt_version = $2
               AND aa.analysis_version = $3 AND aa.status = 'completed'
          )
        GROUP BY a.id, s.name
        ORDER BY a.first_seen_at, a.id
        LIMIT 1000`,
      [input.modelName, input.promptVersion, input.analysisVersion, this.budget.maxAttempts],
    );
    return result.rows;
  }

  async startAnalysis(input: AnalysisStart): Promise<AnalysisStartResult> {
    if (this.budget.maxPerDay === 0) {
      return { started: false, reason: 'DAILY_BUDGET' };
    }
    if (this.budget.maxPerRun === 0) return { started: false, reason: 'RUN_BUDGET' };
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      await client.query('SELECT pg_advisory_xact_lock($1)', [74129518]);
      const daily = await client.query<{ count: string }>(
        `SELECT count(*)::text AS count FROM article_analysis_attempts
          WHERE started_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')`,
      );
      if (Number(daily.rows[0]?.count ?? 0) >= this.budget.maxPerDay) {
        await client.query('COMMIT');
        return { started: false, reason: 'DAILY_BUDGET' };
      }
      const runUsage = await client.query<{ count: string }>(
        'SELECT count(*)::text AS count FROM article_analysis_attempts WHERE run_id = $1', [input.runId],
      );
      if (Number(runUsage.rows[0]?.count ?? 0) >= this.budget.maxPerRun) {
        await client.query('COMMIT');
        return { started: false, reason: 'RUN_BUDGET' };
      }
      const active = await client.query<{ count: string }>(
        `SELECT count(*)::text AS count FROM article_analysis_jobs
          WHERE status = 'processing' AND lease_expires_at > now()`,
      );
      if (Number(active.rows[0]?.count ?? 0) >= this.budget.maxConcurrent) {
        await client.query('COMMIT');
        return { started: false, reason: 'CONCURRENCY_LIMIT' };
      }
      const job = await client.query<{ attempts: number; status: string; available_at: Date; lease_expires_at: Date | null }>(
        `SELECT attempts, status, available_at, lease_expires_at FROM article_analysis_jobs WHERE article_id = $1 FOR UPDATE`,
        [input.articleId],
      );
      const current = job.rows[0];
      if (!current || current.status === 'completed' || current.available_at > new Date()) {
        await client.query('COMMIT');
        return { started: false, reason: 'NOT_ELIGIBLE' };
      }
      if (current.status === 'processing' && current.lease_expires_at && current.lease_expires_at > new Date()) {
        await client.query('COMMIT');
        return { started: false, reason: 'NOT_ELIGIBLE' };
      }
      if (current.attempts >= this.budget.maxAttempts) {
        await client.query('COMMIT');
        return { started: false, reason: 'ATTEMPTS_EXHAUSTED' };
      }
      const completed = await client.query(
        `SELECT 1 FROM article_analyses WHERE article_id = $1 AND model_name = $2
          AND prompt_version = $3 AND analysis_version = $4 AND status = 'completed' LIMIT 1`,
        [input.articleId, input.modelName, input.promptVersion, input.analysisVersion],
      );
      if (completed.rowCount) {
        await client.query("UPDATE article_analysis_jobs SET status='completed',lease_expires_at=NULL,updated_at=now() WHERE article_id=$1", [input.articleId]);
        await client.query('COMMIT');
        return { started: false, reason: 'NOT_ELIGIBLE' };
      }
      const attemptId = randomUUID();
      const inserted = await client.query(
        `INSERT INTO article_analysis_attempts(id,run_id,article_id,model_name,prompt_version,analysis_version,status)
         VALUES ($1,$2,$3,$4,$5,$6,'processing') ON CONFLICT (run_id,article_id,model_name,prompt_version,analysis_version) DO NOTHING
         RETURNING id`,
        [attemptId, input.runId, input.articleId, input.modelName, input.promptVersion, input.analysisVersion],
      );
      if (!inserted.rowCount) {
        await client.query('COMMIT');
        return { started: false, reason: 'ALREADY_CLAIMED' };
      }
      await client.query(
        `UPDATE article_analysis_jobs SET status='processing',attempts=attempts+1,
           model_name=$2,prompt_version=$3,analysis_version=$4,lease_expires_at=now()+interval '10 minutes',
           last_error_code=NULL,updated_at=now() WHERE article_id=$1`,
        [input.articleId, input.modelName, input.promptVersion, input.analysisVersion],
      );
      await client.query('COMMIT');
      return { started: true };
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  async import(input: IngestionInput): Promise<{ runId: string; articleCount: number; insertedCount: number; updatedCount: number }> {
    const client = await this.pool.connect();
    let insertedCount = 0;
    let updatedCount = 0;
    let analysisSucceeded = 0;
    let analysisFailed = 0;
    try {
      await client.query('BEGIN');
      for (const source of input.sources) {
        await client.query(
          `INSERT INTO sources(id,name,type) VALUES ($1,$2,$3)
           ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,type=EXCLUDED.type,updated_at=now()`,
          [source.id, source.name, source.type],
        );
      }

      for (const article of input.articles) {
        const result = await client.query<{ id: string; inserted: boolean }>(
          `INSERT INTO articles(id,source_id,title,url,normalized_url,dedupe_key,summary,published_at,first_seen_at,content_status,analysis_status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           ON CONFLICT (dedupe_key) DO UPDATE SET
             title=EXCLUDED.title,url=COALESCE(EXCLUDED.url,articles.url),
             normalized_url=COALESCE(EXCLUDED.normalized_url,articles.normalized_url),summary=COALESCE(EXCLUDED.summary,articles.summary),
             published_at=COALESCE(EXCLUDED.published_at,articles.published_at),
             updated_at=now(),
             content_status=CASE WHEN EXCLUDED.content_status='title_only' AND articles.summary IS NOT NULL
               THEN articles.content_status ELSE EXCLUDED.content_status END,
             analysis_status=CASE WHEN EXCLUDED.analysis_status IN ('pending','processing','not_requested') AND articles.analysis_status IN ('completed','failed')
               THEN articles.analysis_status ELSE EXCLUDED.analysis_status END
           RETURNING id, (xmax = 0) AS inserted`,
          [article.id, article.sourceId, article.title, article.url ?? null, article.normalizedUrl ?? null,
            article.dedupeKey, article.summary ?? null, article.publishedAt ?? null, article.firstSeenAt,
            article.contentStatus, article.analysisStatus],
        );
        const upserted = result.rows[0];
        if (!upserted?.id) throw new Error('Article upsert returned no ID');
        const persistedId = upserted.id;
        if (upserted.inserted) insertedCount += 1;
        else updatedCount += 1;

        // Metadata-only/history imports are explicitly not_requested and must
        // not create deferred AI work just because an article is new.
        if (upserted.inserted && article.analysisStatus === 'pending') {
          await client.query(
            'INSERT INTO article_analysis_jobs(article_id) VALUES ($1) ON CONFLICT (article_id) DO NOTHING',
            [persistedId],
          );
        }

        // Baseline rules belong to new articles only. Re-imports must not
        // rewrite the existing 452-article history unless analysis explicitly
        // returns replaceCategories=true.
        if (upserted.inserted || article.replaceCategories) {
          await client.query('DELETE FROM article_categories WHERE article_id = $1', [persistedId]);
          for (const category of article.categories) {
            await client.query(
              `INSERT INTO article_categories(article_id,category_id,confidence,assigned_by)
               VALUES ($1,$2,$3,$4) ON CONFLICT (article_id,category_id) DO UPDATE
               SET confidence=EXCLUDED.confidence,assigned_by=EXCLUDED.assigned_by`,
              [persistedId, category.id, category.confidence ?? null, category.assignedBy ?? 'model'],
            );
          }
        }

        if (article.analysis) {
          const analysis = article.analysis;
          await client.query(
            `INSERT INTO article_analyses(id,article_id,summary,key_facts,background,potential_impacts,uncertainties,evidence_scope,model_name,prompt_version,analysis_version,status,error_code,analyzed_at)
             VALUES ($1,$2,$3,$4::jsonb,$5,$6::jsonb,$7::jsonb,$8,$9,$10,$11,$12,$13,$14)
             ON CONFLICT (article_id,model_name,prompt_version,analysis_version) DO UPDATE SET
               summary=EXCLUDED.summary,key_facts=EXCLUDED.key_facts,background=EXCLUDED.background,
               potential_impacts=EXCLUDED.potential_impacts,uncertainties=EXCLUDED.uncertainties,
               evidence_scope=EXCLUDED.evidence_scope,status=EXCLUDED.status,error_code=EXCLUDED.error_code,
               analyzed_at=EXCLUDED.analyzed_at`,
            [randomUUID(), persistedId, analysis.summary ?? null, JSON.stringify(analysis.keyFacts), analysis.background ?? null,
              JSON.stringify(analysis.potentialImpacts), JSON.stringify(analysis.uncertainties), analysis.evidenceScope,
              analysis.modelName, analysis.promptVersion, analysis.analysisVersion, analysis.status,
              analysis.errorCode ?? null, analysis.analyzedAt ?? null],
          );
          if (analysis.status === 'completed') analysisSucceeded += 1;
          if (analysis.status === 'failed') analysisFailed += 1;
          if (analysis.status === 'completed' || analysis.status === 'failed') {
            await client.query(
              `UPDATE article_analysis_attempts SET status=$5,error_code=$6,finished_at=COALESCE($7::timestamptz,now())
                WHERE run_id=$1 AND article_id=$2 AND model_name=$3 AND prompt_version=$4 AND analysis_version=$8 AND status='processing'`,
              [input.run.id, persistedId, analysis.modelName, analysis.promptVersion, analysis.status, analysis.errorCode ?? null,
                analysis.analyzedAt ?? null, analysis.analysisVersion],
            );
            await client.query(
              `UPDATE article_analysis_jobs SET status=$2,lease_expires_at=NULL,
                 available_at=CASE WHEN $2='failed' THEN now()+interval '1 hour' ELSE now() END,
                 last_error_code=$3,updated_at=now() WHERE article_id=$1`,
              [persistedId, analysis.status, analysis.errorCode ?? null],
            );
          }
        }

      }

      await client.query(
        `INSERT INTO ingestion_runs(id,started_at,finished_at,status,source_count,article_count,inserted_count,updated_count,analysis_succeeded,analysis_failed,error_summary,metadata)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
         ON CONFLICT (id) DO UPDATE SET finished_at=EXCLUDED.finished_at,status=EXCLUDED.status,
           source_count=EXCLUDED.source_count,article_count=GREATEST(ingestion_runs.article_count,EXCLUDED.article_count),
           inserted_count=GREATEST(ingestion_runs.inserted_count,EXCLUDED.inserted_count),
           updated_count=GREATEST(ingestion_runs.updated_count,EXCLUDED.updated_count),
           analysis_succeeded=EXCLUDED.analysis_succeeded,analysis_failed=EXCLUDED.analysis_failed,error_summary=EXCLUDED.error_summary,
           metadata=COALESCE(EXCLUDED.metadata,ingestion_runs.metadata)`,
        [input.run.id, input.run.startedAt, input.run.finishedAt, input.run.status, input.run.sourceCount,
          input.articles.length, insertedCount, updatedCount, analysisSucceeded, analysisFailed,
          input.run.errorSummary ?? null, input.run.metadata ? JSON.stringify(input.run.metadata) : null],
      );
      await client.query('COMMIT');
      return { runId: input.run.id, articleCount: input.articles.length, insertedCount, updatedCount };
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }
}
