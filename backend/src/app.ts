import Fastify, { type FastifyInstance } from 'fastify';
import swagger from '@fastify/swagger';
import swaggerUi from '@fastify/swagger-ui';
import { TypeBoxTypeProvider } from '@fastify/type-provider-typebox';
import type { AppConfig } from './config.js';
import type { Pool } from 'pg';
import { NewsRepository } from './repositories/news-repository.js';
import { NewsService } from './services/news-service.js';
import { registerNewsRoutes } from './routes/news.js';
import { registerHealthRoutes } from './routes/health.js';
import { registerStatusRoutes } from './routes/status.js';
import { validBearer } from './auth.js';
import { API_LOG_REDACT_PATHS } from './logging.js';

declare module 'fastify' {
  interface FastifyInstance {
    authenticateRead: (request: import('fastify').FastifyRequest, reply: import('fastify').FastifyReply) => Promise<void>;
  }
}

export interface AppDependencies { pool: Pool; config: AppConfig }

export async function buildApp({ pool, config }: AppDependencies): Promise<FastifyInstance> {
  const app = Fastify({ bodyLimit: 3 * 1024 * 1024, logger: { redact: [...API_LOG_REDACT_PATHS] } })
    .withTypeProvider<TypeBoxTypeProvider>();
  const repository = new NewsRepository(pool);
  const service = new NewsService(repository);

  app.decorate('authenticateRead', async (request, reply) => {
    if (!validBearer(request.headers.authorization, config.apiReadToken)) {
      await reply.code(401).send({ error: { code: 'UNAUTHORIZED', message: 'Authentication required' } });
    }
  });
  app.setErrorHandler((error, request, reply) => {
    request.log.error({ err: error }, 'Request failed');
    const details = error as Error & { validation?: unknown; statusCode?: number; code?: string };
    if (details.validation) {
      return reply.code(400).send({ error: { code: 'VALIDATION_ERROR', message: 'Invalid request parameters' }, requestId: request.id });
    }
    if (details.statusCode === 400) {
      return reply.code(400).send({ error: { code: 'VALIDATION_ERROR', message: 'Invalid request body or parameters' }, requestId: request.id });
    }
    if (details.statusCode === 401 || details.statusCode === 404) {
      return reply.code(details.statusCode).send({ error: { code: details.statusCode === 401 ? 'UNAUTHORIZED' : 'NOT_FOUND', message: details.message }, requestId: request.id });
    }
    const errorCode = details.code;
    if (['ECONNREFUSED', 'ETIMEDOUT', '57P01', '08000', '08006', '53300'].includes(errorCode ?? '')) {
      return reply.code(503).send({ error: { code: 'DATABASE_UNAVAILABLE', message: 'Database is temporarily unavailable' }, requestId: request.id });
    }
    return reply.code(500).send({ error: { code: 'INTERNAL_ERROR', message: 'Internal server error' }, requestId: request.id });
  });

  await app.register(swagger, {
    openapi: {
      info: { title: 'TrendRadar Intelligence API', version: '1.0.0' },
      servers: [{ url: '/' }],
      tags: [{ name: 'news' }, { name: 'catalog' }, { name: 'system' }, { name: 'internal' }],
      components: { securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } } },
    },
  });
  await app.register(swaggerUi, { routePrefix: '/documentation', staticCSP: true });
  await registerHealthRoutes(app, repository);
  await registerStatusRoutes(app, repository);
  await registerNewsRoutes(app, service);
  return app;
}
