import type { FastifyInstance } from 'fastify';
import type { NewsRepository } from '../repositories/news-repository.js';

export async function registerHealthRoutes(app: FastifyInstance, repository: NewsRepository): Promise<void> {
  app.get('/api/v1/health', {
    schema: {
      tags: ['system'],
      response: {
        200: { type: 'object', required: ['status', 'database'], properties: { status: { const: 'ok' }, database: { const: 'ok' } } },
        503: { type: 'object', required: ['status', 'database'], properties: { status: { const: 'error' }, database: { const: 'unavailable' } } },
      },
    },
  }, async (_request, reply) => {
    try {
      await repository.health();
      return { status: 'ok', database: 'ok' };
    } catch {
      return reply.code(503).send({ status: 'error', database: 'unavailable' });
    }
  });
}
