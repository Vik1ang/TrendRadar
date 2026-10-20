import { loadConfig } from './config.js';
import { createPool } from './db/pool.js';
import { buildApp } from './app.js';

async function main(): Promise<void> {
  const config = loadConfig();
  const pool = createPool(config.databaseUrl);
  const app = await buildApp({ pool, config });
  app.addHook('onClose', async () => pool.end());
  const shutdown = async (): Promise<void> => {
    await app.close();
  };
  process.once('SIGINT', () => void shutdown());
  process.once('SIGTERM', () => void shutdown());
  await app.listen({ host: config.host, port: config.port });
}

main().catch((error: unknown) => {
  console.error('API startup failed:', error instanceof Error ? error.message : 'unknown error');
  process.exitCode = 1;
});
