import pg from 'pg';

const { Pool } = pg;

export function createPool(connectionString: string): pg.Pool {
  return new Pool({ connectionString, max: 10, connectionTimeoutMillis: 3000, idleTimeoutMillis: 30000 });
}
