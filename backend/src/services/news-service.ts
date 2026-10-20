import type { NewsRepository } from '../repositories/news-repository.js';
import type { ArticleFilters, ArticleSummary, Page } from '../types.js';

export class NewsService {
  constructor(private readonly repository: NewsRepository) {}

  async list(filters: ArticleFilters): Promise<Page<ArticleSummary>> {
    const result = await this.repository.list(filters);
    return {
      data: result.data,
      pagination: {
        limit: filters.limit,
        offset: filters.offset,
        total: result.total,
        hasMore: filters.offset + result.data.length < result.total,
      },
    };
  }

  getById(id: string) { return this.repository.getById(id); }
  getAnalysis(id: string) { return this.repository.getAnalysis(id); }
  listCategories() { return this.repository.listCategories(); }
  listSources() { return this.repository.listSources(); }
}
