import { createHash, randomBytes, randomUUID } from 'node:crypto';
import type { Pool } from 'pg';

export const DEVICE_ACCESS_TOKEN_PATTERN = /^trd_dev_[A-Za-z0-9_-]{43}$/;
export const DEVICE_PAIRING_CODE_PATTERN = /^trd_pair_[A-Za-z0-9_-]{43}$/;
export const DEVICE_READ_SCOPE = 'news:read';
export const DEVICE_CREDENTIAL_TTL_DAYS = 90;
export const DEVICE_PAIRING_TTL_MINUTES = 10;

const MAX_DEVICE_NAME_LENGTH = 80;
const MAX_PAIRING_ATTEMPTS = 5;

function digest(value: string): Buffer {
  return createHash('sha256').update(value, 'utf8').digest();
}

function randomSecret(prefix: 'trd_pair_' | 'trd_dev_'): string {
  return `${prefix}${randomBytes(32).toString('base64url')}`;
}

function cleanName(value: string): string {
  const name = value.trim().replace(/\p{Cc}/gu, '');
  if (!name || name.length > MAX_DEVICE_NAME_LENGTH) throw new DeviceAuthError('INVALID_DEVICE_NAME');
  return name;
}

export class DeviceAuthError extends Error {
  constructor(readonly code: 'INVALID_PAIRING_CODE' | 'INVALID_DEVICE_NAME' | 'DEVICE_NOT_FOUND') {
    super(code);
    this.name = 'DeviceAuthError';
  }
}

export interface DeviceIdentity {
  id: string;
  name: string;
  scopes: string[];
  expiresAt: Date;
}

export interface IssuedPairingCode {
  code: string;
  expiresAt: Date;
}

export interface PairedDevice extends DeviceIdentity {
  accessToken: string;
  issuedAt: Date;
}

export interface DeviceMetadata {
  id: string;
  name: string;
  scopes: string[];
  enabled: boolean;
  issuedAt: Date;
  expiresAt: Date;
  revokedAt: Date | null;
  lastUsedAt: Date | null;
}

export class DeviceAuthRepository {
  constructor(private readonly pool: Pool) {}

  async issuePairingCode(deviceName: string, ttlMinutes = DEVICE_PAIRING_TTL_MINUTES): Promise<IssuedPairingCode> {
    if (!Number.isInteger(ttlMinutes) || ttlMinutes < 1 || ttlMinutes > DEVICE_PAIRING_TTL_MINUTES) {
      throw new RangeError('Pairing code lifetime is out of range');
    }
    const name = cleanName(deviceName);
    const code = randomSecret('trd_pair_');
    const id = randomUUID();
    const expiresAt = new Date(Date.now() + ttlMinutes * 60_000);
    await this.pool.query(
      `INSERT INTO device_pairing_codes
        (id, code_hash, expected_device_name, expires_at, max_attempts)
       VALUES ($1, $2, $3, $4, $5)`,
      [id, digest(code), name, expiresAt, MAX_PAIRING_ATTEMPTS],
    );
    return { code, expiresAt };
  }

  async redeemPairingCode(code: string, requestedDeviceName: string): Promise<PairedDevice> {
    if (!DEVICE_PAIRING_CODE_PATTERN.test(code)) throw new DeviceAuthError('INVALID_PAIRING_CODE');
    const name = cleanName(requestedDeviceName);
    const client = await this.pool.connect();
    let transactionOpen = false;
    try {
      await client.query('BEGIN');
      transactionOpen = true;
      const result = await client.query<{
        id: string;
        expected_device_name: string;
        expires_at: Date;
        used_at: Date | null;
        attempts: number;
        max_attempts: number;
      }>(
        `SELECT id, expected_device_name, expires_at, used_at, attempts, max_attempts
           FROM device_pairing_codes WHERE code_hash = $1 FOR UPDATE`,
        [digest(code)],
      );
      const pairing = result.rows[0];
      if (!pairing || pairing.used_at || pairing.expires_at.getTime() <= Date.now() || pairing.attempts >= pairing.max_attempts) {
        await client.query('ROLLBACK');
        transactionOpen = false;
        throw new DeviceAuthError('INVALID_PAIRING_CODE');
      }
      const nextAttempt = pairing.attempts + 1;
      await client.query('UPDATE device_pairing_codes SET attempts = $2 WHERE id = $1', [pairing.id, nextAttempt]);
      if (name !== pairing.expected_device_name || nextAttempt > pairing.max_attempts) {
        await client.query('COMMIT');
        transactionOpen = false;
        throw new DeviceAuthError(name === pairing.expected_device_name ? 'INVALID_PAIRING_CODE' : 'INVALID_DEVICE_NAME');
      }

      const now = new Date();
      const expiresAt = new Date(now.getTime() + DEVICE_CREDENTIAL_TTL_DAYS * 24 * 60 * 60_000);
      const deviceId = randomUUID();
      const accessToken = randomSecret('trd_dev_');
      await client.query('UPDATE device_pairing_codes SET used_at = $2 WHERE id = $1', [pairing.id, now]);
      await client.query(
        `INSERT INTO device_credentials (id, device_name, token_hash, scopes, issued_at, expires_at)
         VALUES ($1, $2, $3, ARRAY[$4]::text[], $5, $6)`,
        [deviceId, name, digest(accessToken), DEVICE_READ_SCOPE, now, expiresAt],
      );
      await client.query('COMMIT');
      transactionOpen = false;
      return { id: deviceId, name, accessToken, scopes: [DEVICE_READ_SCOPE], issuedAt: now, expiresAt };
    } catch (error) {
      if (transactionOpen) await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  async authenticate(token: string, updateLastUsed = true): Promise<DeviceIdentity | null> {
    if (!DEVICE_ACCESS_TOKEN_PATTERN.test(token)) return null;
    const result = await this.pool.query<{
      id: string;
      device_name: string;
      scopes: string[];
      expires_at: Date;
      last_used_at: Date | null;
    }>(
      `SELECT id, device_name, scopes, expires_at, last_used_at
         FROM device_credentials
        WHERE token_hash = $1 AND enabled = true AND revoked_at IS NULL AND expires_at > now()`,
      [digest(token)],
    );
    const device = result.rows[0];
    if (!device || typeof device.id !== 'string' || !Array.isArray(device.scopes)) return null;
    if (updateLastUsed && (!device.last_used_at || Date.now() - device.last_used_at.getTime() > 60_000)) {
      await this.pool.query(
        `UPDATE device_credentials SET last_used_at = now()
          WHERE id = $1 AND enabled = true AND revoked_at IS NULL AND expires_at > now()`,
        [device.id],
      );
    }
    return {
      id: device.id,
      name: device.device_name,
      scopes: device.scopes,
      expiresAt: device.expires_at,
    };
  }

  async revoke(deviceId: string): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE device_credentials SET enabled = false, revoked_at = now()
        WHERE id = $1 AND revoked_at IS NULL RETURNING id`,
      [deviceId],
    );
    return (result.rowCount ?? result.rows.length) > 0;
  }

  async list(): Promise<DeviceMetadata[]> {
    const result = await this.pool.query<DeviceMetadata>(
      `SELECT id, device_name AS name, scopes, enabled, issued_at AS "issuedAt",
              expires_at AS "expiresAt", revoked_at AS "revokedAt", last_used_at AS "lastUsedAt"
         FROM device_credentials ORDER BY issued_at DESC`,
    );
    return result.rows;
  }
}

export class PairingRateLimiter {
  private readonly buckets = new Map<string, { startedAt: number; count: number }>();
  private readonly maxBuckets = 10_000;

  constructor(private readonly limit = 5, private readonly windowMs = 60_000) {}

  consume(key: string, now = Date.now()): boolean {
    const current = this.buckets.get(key);
    if (!current || now - current.startedAt >= this.windowMs) {
      if (!current && this.buckets.size >= this.maxBuckets) {
        for (const [bucketKey, bucket] of this.buckets) {
          if (now - bucket.startedAt >= this.windowMs) this.buckets.delete(bucketKey);
        }
        if (this.buckets.size >= this.maxBuckets) return false;
      }
      this.buckets.set(key, { startedAt: now, count: 1 });
      return true;
    }
    if (current.count >= this.limit) return false;
    current.count += 1;
    return true;
  }
}
