import type { FastifyInstance } from 'fastify';
import type { NewsRepository } from '../repositories/news-repository.js';

export async function registerStatusRoutes(app: FastifyInstance, repository: NewsRepository): Promise<void> {
  app.get('/api/v1/status', {
    schema: {
      tags: ['system'],
      security: [{ bearerAuth: [] }],
      summary: 'Get recent ingestion and analysis status',
      response: {
        200: {
          type: 'object',
          required: ['database', 'articleCount', 'latestArticleAt', 'latestRun'],
          properties: {
            database: { type: 'string', enum: ['ok'] },
            articleCount: { type: 'integer' },
            latestArticleAt: { type: ['string', 'null'], format: 'date-time' },
            latestRun: { type: ['object', 'null'], additionalProperties: true },
          },
        },
      },
    },
    preHandler: app.authenticateRead,
  }, async () => repository.status());
}
