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
  summary?: string;
  publishedAt?: string;
  firstSeenAt: string;
  contentStatus: 'title_only' | 'summary_available' | 'content_available';
  analysisStatus: 'pending' | 'processing' | 'completed' | 'failed' | 'not_requested';
  replaceCategories?: boolean;
  categories: Array<{ id: string; confidence?: number; assignedBy?: string }>;
  analysis?: AnalysisInput;
};
export type IngestionInput = {
  run: { id: string; startedAt: string; finishedAt: string; status: 'running' | 'completed' | 'partial' | 'failed'; sourceCount: number; errorSummary?: string };
  sources: SourceInput[];
  articles: ArticleInput[];
};

export class IngestionRepository {
  constructor(private readonly pool: Pool) {}

  async analysisCandidates(input: { articleIds: string[]; modelName: string; promptVersion: string; analysisVersion: number }): Promise<string[]> {
    const result = await this.pool.query<{ id: string }>(
      `SELECT a.id
         FROM unnest($1::uuid[]) WITH ORDINALITY requested(id, ordinal)
         JOIN articles a ON a.id = requested.id
        WHERE NOT EXISTS (
          SELECT 1 FROM article_analyses aa
           WHERE aa.article_id = a.id AND aa.model_name = $2 AND aa.prompt_version = $3
             AND aa.analysis_version = $4
             AND (aa.status = 'completed' OR (aa.status = 'failed' AND COALESCE(aa.analyzed_at, aa.created_at) > now() - interval '1 hour'))
        )
        ORDER BY requested.ordinal`,
      [input.articleIds, input.modelName, input.promptVersion, input.analysisVersion],
    );
    return result.rows.map((row) => row.id);
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

        if (article.replaceCategories || article.categories.length > 0) {
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
        }
      }

      await client.query(
        `INSERT INTO ingestion_runs(id,started_at,finished_at,status,source_count,article_count,inserted_count,updated_count,analysis_succeeded,analysis_failed,error_summary)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
         ON CONFLICT (id) DO UPDATE SET finished_at=EXCLUDED.finished_at,status=EXCLUDED.status,
           source_count=EXCLUDED.source_count,article_count=EXCLUDED.article_count,
           inserted_count=GREATEST(ingestion_runs.inserted_count,EXCLUDED.inserted_count),
           updated_count=GREATEST(ingestion_runs.updated_count,EXCLUDED.updated_count),
           analysis_succeeded=EXCLUDED.analysis_succeeded,analysis_failed=EXCLUDED.analysis_failed,error_summary=EXCLUDED.error_summary`,
        [input.run.id, input.run.startedAt, input.run.finishedAt, input.run.status, input.run.sourceCount,
          input.articles.length, insertedCount, updatedCount, analysisSucceeded, analysisFailed, input.run.errorSummary ?? null],
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
