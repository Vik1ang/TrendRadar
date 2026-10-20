import { loadIngestionConfig } from './config.js';
import { createPool } from './db/pool.js';
import { buildIngestionApp } from './ingestion-app.js';

async function main(): Promise<void> {
  const config = loadIngestionConfig();
  const pool = createPool(config.databaseUrl);
  const app = await buildIngestionApp(pool, config);
  app.addHook('onClose', async () => pool.end());
  const shutdown = async (): Promise<void> => { await app.close(); };
  process.once('SIGINT', () => void shutdown());
  process.once('SIGTERM', () => void shutdown());
  await app.listen({ host: config.host, port: config.port });
}

main().catch((error: unknown) => {
  console.error('Ingestion service startup failed:', error instanceof Error ? error.message : 'unknown error');
  process.exitCode = 1;
});
