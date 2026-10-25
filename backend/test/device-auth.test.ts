import { createHash } from 'node:crypto';
import { afterEach, describe, expect, it } from 'vitest';
import type { Pool, PoolClient } from 'pg';
import type { FastifyInstance } from 'fastify';
import { DeviceAuthError, DeviceAuthRepository, PairingRateLimiter } from '../src/device-auth.js';
import { buildApp } from '../src/app.js';
import { buildIngestionApp } from '../src/ingestion-app.js';
import Fastify from 'fastify';
import { registerDeviceAuthRoutes } from '../src/routes/device-auth.js';

const apps: FastifyInstance[] = [];
const token = `trd_dev_${Buffer.alloc(32, 7).toString('base64url')}`;
const hash = (value: string) => createHash('sha256').update(value).digest();

afterEach(async () => Promise.all(apps.splice(0).map((app) => app.close())));

describe('device credential repository', () => {
  it('redeems a pairing code once and stores only hashes', async () => {
    let pairing: { id: string; codeHash: Buffer; name: string; expiresAt: Date; used: boolean; attempts: number } | undefined;
    const storedTokenHashes: Buffer[] = [];
    const pool = {
      query: async (sql: string, values: unknown[] = []) => {
        if (sql.includes('INSERT INTO device_pairing_codes')) {
          pairing = { id: String(values[0]), codeHash: values[1] as Buffer, name: String(values[2]), expiresAt: values[3] as Date, used: false, attempts: 0 };
          return { rows: [], rowCount: 1 };
        }
        if (sql.includes('INSERT INTO device_credentials')) {
          storedTokenHashes.push(values[2] as Buffer);
          return { rows: [], rowCount: 1 };
        }
        return { rows: [], rowCount: 0 };
      },
      connect: async () => ({
        query: async (sql: string, values: unknown[] = []) => {
          if (sql.includes('SELECT id, expected_device_name')) {
            const submittedHash = values[0] as Buffer;
            if (!pairing || !pairing.codeHash.equals(submittedHash) || pairing.used || pairing.expiresAt <= new Date()) return { rows: [], rowCount: 0 };
            return { rows: [{ id: pairing.id, expected_device_name: pairing.name, expires_at: pairing.expiresAt, used_at: null, attempts: pairing.attempts, max_attempts: 5 }], rowCount: 1 };
          }
          if (sql.includes('UPDATE device_pairing_codes SET attempts')) { if (pairing) pairing.attempts = Number(values[1]); return { rows: [], rowCount: 1 }; }
          if (sql.includes('UPDATE device_pairing_codes SET used_at')) { if (pairing) pairing.used = true; return { rows: [], rowCount: 1 }; }
          if (sql.includes('INSERT INTO device_credentials')) { storedTokenHashes.push(values[2] as Buffer); return { rows: [], rowCount: 1 }; }
          if (sql === 'COMMIT' || sql === 'ROLLBACK' || sql === 'BEGIN') return { rows: [], rowCount: 0 };
          throw new Error(`Unexpected SQL: ${sql}`);
        },
        release: () => undefined,
      } as unknown as PoolClient),
    } as unknown as Pool;
    const repository = new DeviceAuthRepository(pool);
    const issued = await repository.issuePairingCode('My iPhone');
    expect(pairing?.codeHash.equals(hash(issued.code))).toBe(true);
    expect(pairing?.codeHash.toString()).not.toContain(issued.code);

    const paired = await repository.redeemPairingCode(issued.code, 'My iPhone');
    expect(paired.accessToken).toMatch(/^trd_dev_[A-Za-z0-9_-]{43}$/);
    expect(paired.scopes).toEqual(['news:read']);
    expect(storedTokenHashes).toHaveLength(1);
    expect(storedTokenHashes[0]?.equals(hash(paired.accessToken))).toBe(true);
    expect(storedTokenHashes[0]?.toString()).not.toContain(paired.accessToken);
    await expect(repository.redeemPairingCode(issued.code, 'My iPhone')).rejects.toMatchObject({ code: 'INVALID_PAIRING_CODE' });
    const expired = await repository.issuePairingCode('Expired iPhone');
    if (pairing) pairing.expiresAt = new Date(0);
    await expect(repository.redeemPairingCode(expired.code, 'Expired iPhone')).rejects.toMatchObject({ code: 'INVALID_PAIRING_CODE' });
  });

  it('rejects malformed and expired device credentials before granting identity', async () => {
    let queried = false;
    const repository = new DeviceAuthRepository({ query: async () => { queried = true; return { rows: [], rowCount: 0 }; } } as unknown as Pool);
    expect(await repository.authenticate('not-a-device-token')).toBeNull();
    expect(queried).toBe(false);
    expect(await repository.authenticate(token)).toBeNull();
    expect(queried).toBe(true); // The production query requires enabled, not revoked and expires_at > now().
  });

  it('revokes one device without affecting another and rejects expired credentials', async () => {
    const tokenA = `trd_dev_${Buffer.alloc(32, 1).toString('base64url')}`;
    const tokenB = `trd_dev_${Buffer.alloc(32, 2).toString('base64url')}`;
    const expiredToken = `trd_dev_${Buffer.alloc(32, 3).toString('base64url')}`;
    const records = [
      { id: 'device-a', name: 'Phone A', tokenHash: hash(tokenA), expiresAt: new Date(Date.now() + 60_000), enabled: true, revoked: false },
      { id: 'device-b', name: 'Phone B', tokenHash: hash(tokenB), expiresAt: new Date(Date.now() + 60_000), enabled: true, revoked: false },
      { id: 'device-expired', name: 'Old Phone', tokenHash: hash(expiredToken), expiresAt: new Date(0), enabled: true, revoked: false },
    ];
    const pool = { query: async (sql: string, values: unknown[] = []) => {
      if (sql.includes('FROM device_credentials')) {
        const match = records.find((record) => record.tokenHash.equals(values[0] as Buffer)
          && record.enabled && !record.revoked && record.expiresAt > new Date());
        return { rows: match ? [{ id: match.id, device_name: match.name, scopes: ['news:read'], expires_at: match.expiresAt, last_used_at: new Date() }] : [], rowCount: match ? 1 : 0 };
      }
      if (sql.includes('UPDATE device_credentials SET enabled = false')) {
        const match = records.find((record) => record.id === values[0] && !record.revoked);
        if (match) { match.enabled = false; match.revoked = true; }
        return { rows: match ? [{ id: match.id }] : [], rowCount: match ? 1 : 0 };
      }
      return { rows: [], rowCount: 0 };
    } } as unknown as Pool;
    const repository = new DeviceAuthRepository(pool);
    expect(await repository.authenticate(tokenA)).toMatchObject({ id: 'device-a' });
    expect(await repository.authenticate(tokenB)).toMatchObject({ id: 'device-b' });
    expect(await repository.authenticate(expiredToken)).toBeNull();
    expect(await repository.revoke('device-a')).toBe(true);
    expect(await repository.authenticate(tokenA)).toBeNull();
    expect(await repository.authenticate(tokenB)).toMatchObject({ id: 'device-b' });
  });

  it('applies a bounded per-key attempt limit', () => {
    const limiter = new PairingRateLimiter(2, 1000);
    expect(limiter.consume('ip', 100)).toBe(true);
    expect(limiter.consume('ip', 200)).toBe(true);
    expect(limiter.consume('ip', 300)).toBe(false);
    expect(limiter.consume('ip', 1100)).toBe(true);
  });
});

describe('device authorization boundaries', () => {
  it('requires HTTPS and returns a one-time pairing response without exposing secrets elsewhere', async () => {
    let redeemed = false;
    const pairingRepository = {
      redeemPairingCode: async () => {
        if (redeemed) throw new DeviceAuthError('INVALID_PAIRING_CODE');
        redeemed = true;
        return { id: '11111111-1111-4111-8111-111111111111', name: 'My iPhone', accessToken: token, scopes: ['news:read'], expiresAt: new Date(Date.now() + 1000) };
      },
    };
    const app = Fastify({ trustProxy: ['127.0.0.1'], logger: false });
    await registerDeviceAuthRoutes(app, pairingRepository as unknown as DeviceAuthRepository);
    apps.push(app);
    const code = `trd_pair_${Buffer.alloc(32, 8).toString('base64url')}`;
    const unencrypted = await app.inject({ method: 'POST', url: '/api/v2/devices/pair', remoteAddress: '127.0.0.1', headers: { authorization: `Pairing ${code}` }, payload: { deviceName: 'My iPhone' } });
    expect(unencrypted.statusCode).toBe(400);
    expect(unencrypted.json()).toMatchObject({ error: { code: 'HTTPS_REQUIRED' } });
    const headers = { authorization: `Pairing ${code}`, 'x-forwarded-proto': 'https' };
    const success = await app.inject({ method: 'POST', url: '/api/v2/devices/pair', remoteAddress: '127.0.0.1', headers, payload: { deviceName: 'My iPhone' } });
    expect(success.statusCode).toBe(200);
    expect(success.json().accessToken).toBe(token);
    const replay = await app.inject({ method: 'POST', url: '/api/v2/devices/pair', remoteAddress: '127.0.0.1', headers, payload: { deviceName: 'My iPhone' } });
    expect(replay.statusCode).toBe(401);
    expect(replay.body).not.toContain(code);
  });

  it('maps transient pairing-store failures to the stable 503 envelope', async () => {
    const pool = {
      query: async () => ({ rows: [], rowCount: 0 }),
      connect: async () => { throw Object.assign(new Error('private timeout detail'), { code: 'ETIMEDOUT' }); },
    } as unknown as Pool;
    const app = await buildApp({ pool, config: {
      host: '127.0.0.1', port: 3001, databaseUrl: 'unused', apiReadToken: 'legacy-test', trustedProxyIps: ['127.0.0.1'],
    } });
    apps.push(app);
    const response = await app.inject({
      method: 'POST', url: '/api/v2/devices/pair', remoteAddress: '127.0.0.1',
      headers: { authorization: `Pairing trd_pair_${Buffer.alloc(32, 6).toString('base64url')}`, 'x-forwarded-proto': 'https' },
      payload: { deviceName: 'My iPhone' },
    });
    expect(response.statusCode).toBe(503);
    expect(response.json()).toMatchObject({ error: { code: 'DATABASE_UNAVAILABLE' } });
    expect(response.body).not.toContain('private timeout detail');
  });

  it('allows a device news read, denies status, and preserves the legacy read token', async () => {
    const pool = {
      query: async (sql: string) => {
        if (sql.includes('FROM device_credentials')) return { rows: [{ id: 'd1', device_name: 'Phone', scopes: ['news:read'], expires_at: new Date(Date.now() + 60_000), last_used_at: new Date() }], rowCount: 1 };
        if (sql.includes('UPDATE device_credentials')) return { rows: [], rowCount: 1 };
        if (sql.includes('FROM categories')) return { rows: [], rowCount: 0 };
        if (sql.includes('FROM sources')) return { rows: [], rowCount: 0 };
        return { rows: [], rowCount: 0 };
      },
      connect: async () => { throw new Error('unexpected transaction'); },
    } as unknown as Pool;
    const app = await buildApp({ pool, config: { host: '127.0.0.1', port: 3001, databaseUrl: 'unused', apiReadToken: 'legacy-test' } });
    apps.push(app);
    const deviceRead = await app.inject({ method: 'GET', url: '/api/v1/categories', headers: { authorization: `Bearer ${token}` } });
    expect(deviceRead.statusCode).toBe(200);
    const forbidden = await app.inject({ method: 'GET', url: '/api/v1/status', headers: { authorization: `Bearer ${token}` } });
    expect(forbidden.statusCode).toBe(403);
    const legacyRead = await app.inject({ method: 'GET', url: '/api/v1/categories', headers: { authorization: 'Bearer legacy-test' } });
    expect(legacyRead.statusCode).toBe(200);
  });

  it('rejects recognized device credentials on the separate ingestion listener', async () => {
    const pool = { query: async (sql: string) => sql.includes('FROM device_credentials')
      ? { rows: [{ id: 'd1', device_name: 'Phone', scopes: ['news:read'], expires_at: new Date(Date.now() + 60_000), last_used_at: new Date() }], rowCount: 1 }
      : { rows: [], rowCount: 0 }, connect: async () => { throw new Error('unexpected transaction'); } } as unknown as Pool;
    const app = await buildIngestionApp(pool, {
      host: '127.0.0.1', port: 3002, databaseUrl: 'unused', ingestionToken: 'ingest-test',
      analysisMaxPerRun: 1, analysisMaxPerDay: 0, analysisMaxConcurrent: 1, analysisMaxAttempts: 1,
    });
    apps.push(app);
    const payload = { runId: '11111111-1111-4111-8111-111111111111', articleId: '22222222-2222-4222-8222-222222222222', modelName: 'test', promptVersion: 'v1', analysisVersion: 1 };
    const response = await app.inject({ method: 'POST', url: '/api/v1/internal/analysis-start', headers: { authorization: `Bearer ${token}` }, payload });
    expect(response.statusCode).toBe(403);
    const legacy = await app.inject({ method: 'POST', url: '/api/v1/internal/analysis-start', headers: { authorization: 'Bearer ingest-test' }, payload });
    expect(legacy.statusCode).not.toBe(401);
  });
});
