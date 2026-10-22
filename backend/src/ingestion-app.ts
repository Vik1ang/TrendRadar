import Fastify, { type FastifyInstance } from 'fastify';
import type { Pool } from 'pg';
import swagger from '@fastify/swagger';
import swaggerUi from '@fastify/swagger-ui';
import type { IngestionConfig } from './config.js';
import { validBearer } from './auth.js';
import { IngestionRepository } from './repositories/ingestion-repository.js';
import { registerIngestionRoutes } from './routes/ingestion.js';

declare module 'fastify' {
  interface FastifyInstance {
    authenticateIngestion: (request: import('fastify').FastifyRequest, reply: import('fastify').FastifyReply) => Promise<void>;
  }
}

export async function buildIngestionApp(pool: Pool, config: IngestionConfig): Promise<FastifyInstance> {
  const app = Fastify({ bodyLimit: 10 * 1024 * 1024, logger: { redact: ['req.headers.authorization', 'req.headers.cookie'] } });
  app.decorate('authenticateIngestion', async (request, reply) => {
    if (!validBearer(request.headers.authorization, config.ingestionToken)) {
      await reply.code(401).send({ error: { code: 'UNAUTHORIZED', message: 'Authentication required' }, requestId: request.id });
    }
  });
  app.setErrorHandler((error, request, reply) => {
    request.log.error({ err: error }, 'Internal ingestion request failed');
    const details = error as Error & { validation?: unknown; statusCode?: number; code?: string };
    if (details.validation) {
      return reply.code(400).send({ error: { code: 'VALIDATION_ERROR', message: 'Invalid ingestion batch' }, requestId: request.id });
    }
    if (details.statusCode === 400) {
      return reply.code(400).send({ error: { code: 'VALIDATION_ERROR', message: 'Invalid ingestion batch' }, requestId: request.id });
    }
    if (['ECONNREFUSED', 'ETIMEDOUT', '57P01', '08000', '08006', '53300'].includes(details.code ?? '')) {
      return reply.code(503).send({ error: { code: 'DATABASE_UNAVAILABLE', message: 'Database is temporarily unavailable' }, requestId: request.id });
    }
    return reply.code(500).send({ error: { code: 'INTERNAL_ERROR', message: 'Internal server error' }, requestId: request.id });
  });
  await app.register(swagger, {
    openapi: {
      info: { title: 'TrendRadar Internal Ingestion API', version: '1.0.0' },
      servers: [{ url: '/' }],
      tags: [{ name: 'internal' }],
      components: { securitySchemes: { ingestionToken: { type: 'http', scheme: 'bearer' } } },
    },
  });
  await app.register(swaggerUi, { routePrefix: '/documentation', staticCSP: true });
  app.get('/internal/health', async (_request, reply) => {
    try {
      await pool.query('SELECT 1');
      return { status: 'ok', database: 'ok' };
    } catch {
      return reply.code(503).send({ status: 'error', database: 'unavailable' });
    }
  });
  await registerIngestionRoutes(app, new IngestionRepository(pool, {
    maxPerRun: config.analysisMaxPerRun,
    maxPerDay: config.analysisMaxPerDay,
    maxConcurrent: config.analysisMaxConcurrent,
    maxAttempts: config.analysisMaxAttempts,
  }));
  return app;
}
