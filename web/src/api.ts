export type Category = { id: string; name: string; sortOrder?: number };
export type Source = { id: string; name: string; type: 'hotlist' | 'rss' };
export type Article = {
  id: string;
  title: string;
  source: Source;
  publishedAt: string | null;
  firstSeenAt: string;
  url: string | null;
  summary: string | null;
  analysisSummary: string | null;
  contentStatus: 'title_only' | 'summary_available' | 'content_available';
  analysisStatus: 'pending' | 'processing' | 'completed' | 'failed' | 'not_requested';
  categories: Category[];
};
export type Page = { data: Article[]; pagination: { limit: number; offset: number; total: number; hasMore: boolean } };
export type Analysis = {
  status: Article['analysisStatus']; errorCode?: string; summary?: string | null; keyFacts?: string[]; background?: string | null;
  potentialImpacts?: string[]; uncertainties?: string[]; evidenceScope?: 'title' | 'summary' | 'content';
  modelName?: string; promptVersion?: string; analysisVersion?: number; analyzedAt?: string | null;
};
export type SystemStatus = {
  database: 'ok'; articleCount: number; latestArticleAt: string | null;
  latestRun: null | {
    id: string; status: string; startedAt: string; finishedAt: string | null;
    articleCount: number; insertedCount: number; updatedCount: number;
    analysisSucceeded: number; analysisFailed: number; errorSummary: string | null;
  };
};
export type HealthStatus = {
  api: 'checking' | 'reachable' | 'unreachable';
  database: 'checking' | 'ok' | 'unavailable' | 'unknown';
};

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new Error(body?.error?.message ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  list: (params: URLSearchParams) => request<Page>(`/news?${params.toString()}`),
  article: (id: string) => request<Article>(`/news/${encodeURIComponent(id)}`),
  analysis: (id: string) => request<Analysis>(`/news/${encodeURIComponent(id)}/analysis`),
  categories: () => request<Category[]>('/categories'),
  sources: () => request<Source[]>('/sources'),
  status: () => request<SystemStatus>('/status'),
  health: async (): Promise<HealthStatus> => {
    const response = await fetch('/api/v1/health', { headers: { Accept: 'application/json' } });
    const body = await response.json().catch(() => null) as { status?: string; database?: string } | null;
    if (response.ok && body?.status === 'ok' && body.database === 'ok') return { api: 'reachable', database: 'ok' };
    if (body?.status === 'error' && body.database === 'unavailable') return { api: 'reachable', database: 'unavailable' };
    return { api: 'reachable', database: 'unknown' };
  },
};
