export interface ArticleFilters {
  q?: string;
  category?: string;
  source?: string;
  from?: string;
  to?: string;
  limit: number;
  offset: number;
}

export interface ArticleSummary {
  id: string;
  title: string;
  source: { id: string; name: string; type: 'hotlist' | 'rss' };
  publishedAt: string | null;
  firstSeenAt: string;
  url: string | null;
  summary: string | null;
  analysisSummary: string | null;
  contentStatus: string;
  analysisStatus: string;
  categories: Array<{ id: string; name: string }>;
}

export interface Page<T> {
  data: T[];
  pagination: { limit: number; offset: number; total: number; hasMore: boolean };
}
