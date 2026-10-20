import type { FastifyInstance } from 'fastify';
import { Type, type Static } from 'typebox';
import type { NewsService } from '../services/news-service.js';

const pageQuery = Type.Object({
  q: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
  category: Type.Optional(Type.String({ minLength: 1, maxLength: 80 })),
  source: Type.Optional(Type.String({ minLength: 1, maxLength: 160 })),
  from: Type.Optional(Type.String({ format: 'date-time' })),
  to: Type.Optional(Type.String({ format: 'date-time' })),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100, default: 30 })),
  offset: Type.Optional(Type.Integer({ minimum: 0, maximum: 1000000, default: 0 })),
});
type PageQuery = Static<typeof pageQuery>;

const articleIdParams = Type.Object({ id: Type.String({ format: 'uuid' }) });

const sourceSchema = {
  type: 'object', required: ['id', 'name', 'type'],
  properties: { id: { type: 'string' }, name: { type: 'string' }, type: { type: 'string', enum: ['hotlist', 'rss'] } },
} as const;
const categorySchema = {
  type: 'object', required: ['id', 'name'],
  properties: { id: { type: 'string' }, name: { type: 'string' } },
} as const;
const articleSchema = {
  type: 'object', required: ['id', 'title', 'source', 'publishedAt', 'firstSeenAt', 'url', 'summary', 'analysisSummary', 'contentStatus', 'analysisStatus', 'categories'],
  properties: {
    id: { type: 'string', format: 'uuid' }, title: { type: 'string' }, source: sourceSchema,
    publishedAt: { type: ['string', 'null'], format: 'date-time' }, firstSeenAt: { type: 'string', format: 'date-time' },
    url: { type: ['string', 'null'] }, summary: { type: ['string', 'null'] }, analysisSummary: { type: ['string', 'null'] }, contentStatus: { type: 'string' },
    analysisStatus: { type: 'string' }, categories: { type: 'array', items: categorySchema },
  },
} as const;
const pageSchema = {
  type: 'object',
  properties: {
    data: { type: 'array', items: articleSchema },
    pagination: {
      type: 'object', required: ['limit', 'offset', 'total', 'hasMore'],
      properties: { limit: { type: 'integer' }, offset: { type: 'integer' }, total: { type: 'integer' }, hasMore: { type: 'boolean' } },
    },
  },
  required: ['data', 'pagination'],
} as const;

function toFilters(query: PageQuery) {
  return {
    ...(query.q === undefined ? {} : { q: query.q }),
    ...(query.category === undefined ? {} : { category: query.category }),
    ...(query.source === undefined ? {} : { source: query.source }),
    ...(query.from === undefined ? {} : { from: query.from }),
    ...(query.to === undefined ? {} : { to: query.to }),
    limit: query.limit ?? 30,
    offset: query.offset ?? 0,
  };
}

export async function registerNewsRoutes(app: FastifyInstance, service: NewsService): Promise<void> {
  app.get('/api/v1/news', {
    schema: { querystring: pageQuery, response: { 200: pageSchema }, security: [{ bearerAuth: [] }], tags: ['news'], summary: 'List and filter news' },
    preHandler: app.authenticateRead,
  }, async (request) => service.list(toFilters(request.query as PageQuery)));

  app.get('/api/v1/search', {
    schema: { querystring: pageQuery, response: { 200: pageSchema }, security: [{ bearerAuth: [] }], tags: ['news'], summary: 'Search news in PostgreSQL' },
    preHandler: app.authenticateRead,
  }, async (request) => service.list(toFilters(request.query as PageQuery)));

  app.get<{ Params: { id: string } }>('/api/v1/news/:id', {
    schema: { params: articleIdParams, response: { 200: articleSchema }, security: [{ bearerAuth: [] }], tags: ['news'], summary: 'Get a news article' },
    preHandler: app.authenticateRead,
  }, async (request, reply) => {
    const article = await service.getById(request.params.id);
    if (!article) return reply.code(404).send({ error: { code: 'NOT_FOUND', message: 'Article not found' }, requestId: request.id });
    return article;
  });

  app.get<{ Params: { id: string } }>('/api/v1/news/:id/analysis', {
    schema: { params: articleIdParams, response: { 200: {
      type: 'object', required: ['status'], additionalProperties: false,
      properties: {
        status: { type: 'string', enum: ['pending', 'processing', 'completed', 'failed', 'not_requested'] },
        summary: { type: 'string' }, keyFacts: { type: 'array' }, background: { type: 'string' },
        potentialImpacts: { type: 'array' }, uncertainties: { type: 'array' }, evidenceScope: { type: 'string', enum: ['title', 'summary', 'content'] },
        modelName: { type: 'string' }, promptVersion: { type: 'string' }, analysisVersion: { type: 'integer' }, analyzedAt: { type: 'string', format: 'date-time' },
      },
    } }, security: [{ bearerAuth: [] }], tags: ['news'], summary: 'Get pre-generated article analysis' },
    preHandler: app.authenticateRead,
  }, async (request, reply) => {
    const analysis = await service.getAnalysis(request.params.id);
    if (!analysis) return reply.code(404).send({ error: { code: 'NOT_FOUND', message: 'Article not found' }, requestId: request.id });
    return analysis;
  });

  app.get('/api/v1/categories', {
    schema: { response: { 200: { type: 'array', items: { ...categorySchema, required: ['id', 'name', 'sortOrder'], properties: { ...categorySchema.properties, sortOrder: { type: 'integer' } } } } }, security: [{ bearerAuth: [] }], tags: ['catalog'], summary: 'List categories' },
    preHandler: app.authenticateRead,
  }, () => service.listCategories());

  app.get('/api/v1/sources', {
    schema: { response: { 200: { type: 'array', items: sourceSchema } }, security: [{ bearerAuth: [] }], tags: ['catalog'], summary: 'List sources' },
    preHandler: app.authenticateRead,
  }, () => service.listSources());
}
