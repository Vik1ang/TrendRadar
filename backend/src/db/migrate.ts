import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import pg from 'pg';
import { loadConfig } from '../config.js';

const { Client } = pg;
const directory = path.resolve(process.cwd(), 'migrations');

async function migrate(): Promise<void> {
  const config = loadConfig();
  const client = new Client({ connectionString: config.databaseUrl });
  await client.connect();
  try {
    await client.query('CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())');
    const files = (await readdir(directory)).filter((file) => file.endsWith('.sql')).sort();
    for (const file of files) {
      const found = await client.query('SELECT 1 FROM schema_migrations WHERE version = $1', [file]);
      if (found.rowCount) continue;
      const sql = await readFile(path.join(directory, file), 'utf8');
      await client.query('BEGIN');
      try {
        await client.query(sql);
        await client.query('INSERT INTO schema_migrations(version) VALUES ($1)', [file]);
        await client.query('COMMIT');
        console.info(`Applied migration ${file}`);
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      }
    }
  } finally {
    await client.end();
  }
}

migrate().catch((error: unknown) => {
  console.error('Migration failed:', error instanceof Error ? error.message : 'unknown error');
  process.exitCode = 1;
});
