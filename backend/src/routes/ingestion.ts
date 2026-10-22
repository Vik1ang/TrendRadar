import type { FastifyInstance } from 'fastify';
import { Type, type Static } from 'typebox';
import type { IngestionRepository } from '../repositories/ingestion-repository.js';

const source = Type.Object({ id: Type.String({ minLength: 1, maxLength: 160 }), name: Type.String({ minLength: 1, maxLength: 240 }), type: Type.Union([Type.Literal('hotlist'), Type.Literal('rss')]) });
const category = Type.Object({ id: Type.String({ minLength: 1, maxLength: 80 }), confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })), assignedBy: Type.Optional(Type.String({ maxLength: 80 })) });
const analysis = Type.Object({
  summary: Type.Optional(Type.String({ maxLength: 10000 })),
  keyFacts: Type.Array(Type.String({ maxLength: 3000 }), { maxItems: 30 }),
  background: Type.Optional(Type.String({ maxLength: 10000 })),
  potentialImpacts: Type.Array(Type.String({ maxLength: 3000 }), { maxItems: 30 }),
  uncertainties: Type.Array(Type.String({ maxLength: 3000 }), { maxItems: 30 }),
  evidenceScope: Type.Union([Type.Literal('title'), Type.Literal('summary'), Type.Literal('content')]),
  modelName: Type.String({ minLength: 1, maxLength: 160 }),
  promptVersion: Type.String({ minLength: 1, maxLength: 80 }),
  analysisVersion: Type.Integer({ minimum: 1 }),
  status: Type.Union([Type.Literal('pending'), Type.Literal('processing'), Type.Literal('completed'), Type.Literal('failed')]),
  errorCode: Type.Optional(Type.String({ maxLength: 160 })),
  analyzedAt: Type.Optional(Type.String({ format: 'date-time' })),
});
const article = Type.Object({
  id: Type.String({ format: 'uuid' }), sourceId: Type.String({ minLength: 1, maxLength: 160 }),
  title: Type.String({ minLength: 1, maxLength: 2000 }), url: Type.Optional(Type.String({ format: 'uri', maxLength: 4000 })),
  normalizedUrl: Type.Optional(Type.String({ format: 'uri', maxLength: 4000 })),
  dedupeKey: Type.String({ minLength: 1, maxLength: 256 }), summary: Type.Optional(Type.String({ maxLength: 10000 })),
  publishedAt: Type.Optional(Type.String({ format: 'date-time' })), firstSeenAt: Type.String({ format: 'date-time' }),
  contentStatus: Type.Union([Type.Literal('title_only'), Type.Literal('summary_available'), Type.Literal('content_available')]),
  analysisStatus: Type.Union([Type.Literal('pending'), Type.Literal('processing'), Type.Literal('completed'), Type.Literal('failed'), Type.Literal('not_requested')]),
  categories: Type.Array(category, { maxItems: 20 }), replaceCategories: Type.Optional(Type.Boolean()), analysis: Type.Optional(analysis),
});
const bodySchema = Type.Object({
  run: Type.Object({
    id: Type.String({ format: 'uuid' }), startedAt: Type.String({ format: 'date-time' }), finishedAt: Type.String({ format: 'date-time' }),
    status: Type.Union([Type.Literal('running'), Type.Literal('completed'), Type.Literal('partial'), Type.Literal('failed')]),
    sourceCount: Type.Integer({ minimum: 0, maximum: 10000 }), errorSummary: Type.Optional(Type.String({ maxLength: 4000 })),
    metadata: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
  }),
  sources: Type.Array(source, { maxItems: 500 }),
  articles: Type.Array(article, { maxItems: 1000 }),
});
type Body = Static<typeof bodySchema>;

export async function registerIngestionRoutes(app: FastifyInstance, repository: IngestionRepository): Promise<void> {
  const candidates = Type.Object({
    modelName: Type.String({ minLength: 1, maxLength: 160 }),
    promptVersion: Type.String({ minLength: 1, maxLength: 80 }),
    analysisVersion: Type.Integer({ minimum: 1 }),
  });
  const analysisCandidate = Type.Object({
    id: Type.String({ format: 'uuid' }), sourceId: Type.String(), sourceName: Type.String(),
    title: Type.String(), summary: Type.Union([Type.String(), Type.Null()]),
    categories: Type.Array(Type.Object({
      id: Type.String(), confidence: Type.Optional(Type.Number()), assignedBy: Type.String(),
    })),
  });
  app.post<{ Body: Static<typeof candidates> }>('/api/v1/internal/analysis-candidates', {
    schema: {
      body: candidates,
      response: { 200: Type.Object({ articles: Type.Array(analysisCandidate) }) },
      security: [{ ingestionToken: [] }], tags: ['internal'], summary: 'List newly queued articles awaiting bounded analysis',
    },
    preHandler: app.authenticateIngestion,
  }, async (request) => ({ articles: await repository.analysisCandidates(request.body) }));

  const startAnalysis = Type.Object({
    runId: Type.String({ format: 'uuid' }),
    articleId: Type.String({ format: 'uuid' }),
    modelName: Type.String({ minLength: 1, maxLength: 160 }),
    promptVersion: Type.String({ minLength: 1, maxLength: 80 }),
    analysisVersion: Type.Integer({ minimum: 1 }),
  });
  app.post<{ Body: Static<typeof startAnalysis> }>('/api/v1/internal/analysis-start', {
    schema: {
      body: startAnalysis,
      security: [{ ingestionToken: [] }], tags: ['internal'], summary: 'Reserve one analysis call within configured budgets',
    },
    preHandler: app.authenticateIngestion,
  }, async (request) => repository.startAnalysis(request.body));

  app.post<{ Body: Body }>('/api/v1/internal/ingestion', {
    schema: {
      body: bodySchema,
      response: { 201: { type: 'object', required: ['runId', 'articleCount', 'insertedCount', 'updatedCount'], properties: {
        runId: { type: 'string', format: 'uuid' }, articleCount: { type: 'integer' }, insertedCount: { type: 'integer' }, updatedCount: { type: 'integer' },
      } } },
      security: [{ ingestionToken: [] }], tags: ['internal'], summary: 'Import a normalized ingestion batch (internal only)',
    },
    preHandler: app.authenticateIngestion,
  }, async (request, reply) => reply.code(201).send(await repository.import(request.body)));
}
