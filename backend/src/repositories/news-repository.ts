import type { Pool, PoolClient } from 'pg';
import type { ArticleFilters, ArticleSummary } from '../types.js';

interface DbArticle {
  id: string;
  title: string;
  source_id: string;
  source_name: string;
  source_type: 'hotlist' | 'rss';
  published_at: Date | null;
  first_seen_at: Date;
  url: string | null;
  summary: string | null;
  analysis_summary: string | null;
  content_status: string;
  analysis_status: string;
  categories: Array<{ id: string; name: string }> | null;
}

function mapArticle(row: DbArticle): ArticleSummary {
  return {
    id: row.id,
    title: row.title,
    source: { id: row.source_id, name: row.source_name, type: row.source_type },
    publishedAt: row.published_at?.toISOString() ?? null,
    firstSeenAt: row.first_seen_at.toISOString(),
    url: row.url,
    summary: row.summary,
    analysisSummary: row.analysis_summary,
    contentStatus: row.content_status,
    analysisStatus: row.analysis_status,
    categories: row.categories ?? [],
  };
}

const articleSelect = `
  SELECT a.id, a.title, a.source_id, s.name AS source_name, s.type AS source_type,
         a.published_at, a.first_seen_at, a.url, a.summary,
         CASE WHEN a.analysis_status = 'completed' THEN (
           SELECT aa.summary FROM article_analyses aa
            WHERE aa.article_id = a.id AND aa.status = 'completed'
            ORDER BY aa.created_at DESC, aa.analyzed_at DESC NULLS LAST LIMIT 1
         ) ELSE NULL END AS analysis_summary,
         a.content_status, a.analysis_status,
         COALESCE(jsonb_agg(DISTINCT jsonb_build_object('id', c.id, 'name', c.name))
           FILTER (WHERE c.id IS NOT NULL), '[]'::jsonb) AS categories
    FROM articles a
    JOIN sources s ON s.id = a.source_id
    LEFT JOIN article_categories ac ON ac.article_id = a.id
    LEFT JOIN categories c ON c.id = ac.category_id`;

export class NewsRepository {
  constructor(private readonly pool: Pool) {}

  async list(filters: ArticleFilters): Promise<{ data: ArticleSummary[]; total: number }> {
    // Search uses literal substring matching, so % and _ in a query are ordinary characters.
    const conditions: string[] = [];
    const values: unknown[] = [];
    if (filters.q) {
      values.push(filters.q);
      const query = `$${values.length}`;
      conditions.push(`(
        position(lower(${query}) in lower(a.title)) > 0
        OR position(lower(${query}) in lower(COALESCE(a.summary, ''))) > 0
        OR (a.analysis_status = 'completed' AND EXISTS (
          SELECT 1 FROM article_analyses aas
           WHERE aas.article_id = a.id AND aas.status = 'completed'
             AND position(lower(${query}) in lower(COALESCE(aas.summary, ''))) > 0
        ))
      )`);
    }
    if (filters.category) { values.push(filters.category); conditions.push(`EXISTS (SELECT 1 FROM article_categories acf WHERE acf.article_id = a.id AND acf.category_id = $${values.length})`); }
    if (filters.source) { values.push(filters.source); conditions.push(`a.source_id = $${values.length}`); }
    if (filters.from) { values.push(filters.from); conditions.push(`COALESCE(a.published_at, a.first_seen_at) >= $${values.length}::timestamptz`); }
    if (filters.to) { values.push(filters.to); conditions.push(`COALESCE(a.published_at, a.first_seen_at) < $${values.length}::timestamptz`); }
    const predicate = conditions.length ? ` WHERE ${conditions.join(' AND ')}` : '';
    const count = await this.pool.query<{ total: string }>(`SELECT count(*)::text AS total FROM articles a${predicate}`, values);
    const rows = await this.pool.query<DbArticle>(
      `${articleSelect}${predicate} GROUP BY a.id, s.id ORDER BY COALESCE(a.published_at, a.first_seen_at) DESC, a.id LIMIT $${values.length + 1} OFFSET $${values.length + 2}`,
      [...values, filters.limit, filters.offset],
    );
    return { data: rows.rows.map(mapArticle), total: Number(count.rows[0]?.total ?? 0) };
  }

  async getById(id: string): Promise<ArticleSummary | null> {
    const result = await this.pool.query<DbArticle>(
      `${articleSelect} WHERE a.id = $1 GROUP BY a.id, s.id`, [id],
    );
    return result.rows[0] ? mapArticle(result.rows[0]) : null;
  }

  async getAnalysis(id: string): Promise<Record<string, unknown> | null> {
    const result = await this.pool.query(
      `SELECT status, summary, key_facts AS "keyFacts", background,
              potential_impacts AS "potentialImpacts", uncertainties, evidence_scope AS "evidenceScope",
              model_name AS "modelName", prompt_version AS "promptVersion",
              analysis_version AS "analysisVersion", analyzed_at AS "analyzedAt"
         FROM article_analyses WHERE article_id = $1
         ORDER BY created_at DESC, analyzed_at DESC NULLS LAST LIMIT 1`, [id],
    );
    if (result.rows[0]) return result.rows[0] as Record<string, unknown>;
    const article = await this.pool.query<{ analysis_status: string }>('SELECT analysis_status FROM articles WHERE id = $1', [id]);
    if (!article.rowCount) return null;
    return { status: article.rows[0]?.analysis_status ?? 'pending' };
  }

  async listCategories(): Promise<Array<{ id: string; name: string; sortOrder: number }>> {
    const result = await this.pool.query<{ id: string; name: string; sort_order: number }>('SELECT id, name, sort_order FROM categories ORDER BY sort_order, id');
    return result.rows.map((row) => ({ id: row.id, name: row.name, sortOrder: row.sort_order }));
  }

  async listSources(): Promise<Array<{ id: string; name: string; type: string }>> {
    const result = await this.pool.query<{ id: string; name: string; type: string }>('SELECT id, name, type FROM sources ORDER BY name, id');
    return result.rows;
  }

  async health(): Promise<{ database: 'ok' }> {
    await this.pool.query('SELECT 1');
    return { database: 'ok' };
  }

  async status(): Promise<Record<string, unknown>> {
    const result = await this.pool.query(`
      SELECT
        (SELECT count(*)::integer FROM articles) AS article_count,
        (SELECT max(updated_at) FROM articles) AS latest_article_at,
        (SELECT jsonb_build_object(
          'id', id, 'status', status, 'startedAt', started_at,
          'finishedAt', finished_at, 'articleCount', article_count,
          'insertedCount', inserted_count, 'updatedCount', updated_count,
          'analysisSucceeded', analysis_succeeded, 'analysisFailed', analysis_failed,
          'errorSummary', error_summary
        ) FROM ingestion_runs ORDER BY started_at DESC LIMIT 1) AS latest_run
    `);
    const row = result.rows[0] as { article_count: number; latest_article_at: Date | null; latest_run: unknown } | undefined;
    return {
      database: 'ok',
      articleCount: row?.article_count ?? 0,
      latestArticleAt: row?.latest_article_at?.toISOString() ?? null,
      latestRun: row?.latest_run ?? null,
    };
  }

  async withTransaction<T>(callback: (client: PoolClient) => Promise<T>): Promise<T> {
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      const result = await callback(client);
      await client.query('COMMIT');
      return result;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }
}
