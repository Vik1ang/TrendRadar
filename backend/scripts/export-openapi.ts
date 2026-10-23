import type { Pool } from 'pg';
import { buildApp } from '../src/app.js';

// Route registration and Swagger generation do not touch the database.
const unusedPool = {} as Pool;
const app = await buildApp({
  pool: unusedPool,
  config: {
    host: '127.0.0.1',
    port: 3001,
    databaseUrl: 'openapi-generation-only',
    apiReadToken: 'openapi-generation-only',
  },
});

try {
  await app.ready();
  process.stdout.write(`${JSON.stringify(app.swagger(), null, 2)}\n`);
} finally {
  await app.close();
}
