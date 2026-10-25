import { DeviceAuthRepository } from './device-auth.js';
import { createPool } from './db/pool.js';

function argument(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

async function main(): Promise<void> {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error('DATABASE_URL is required');
  const pool = createPool(databaseUrl);
  const repository = new DeviceAuthRepository(pool);
  try {
    const command = process.argv[2];
    if (command === 'issue') {
      const deviceName = argument('--device-name');
      if (!deviceName) throw new Error('issue requires --device-name');
      const pairing = await repository.issuePairingCode(deviceName);
      process.stdout.write(`${JSON.stringify({ pairingCode: pairing.code, expiresAt: pairing.expiresAt.toISOString() })}\n`);
      return;
    }
    if (command === 'list') {
      process.stdout.write(`${JSON.stringify(await repository.list(), null, 2)}\n`);
      return;
    }
    if (command === 'revoke') {
      const id = process.argv[3];
      if (process.argv[4] !== '--confirm' || !id || !/^[0-9a-f-]{36}$/i.test(id)) {
        throw new Error('revoke requires a device UUID and --confirm');
      }
      const revoked = await repository.revoke(id);
      process.stdout.write(`${JSON.stringify({ deviceId: id, revoked })}\n`);
      return;
    }
    throw new Error('command must be issue, list, or revoke');
  } finally {
    await pool.end();
  }
}

if (process.argv[1]?.endsWith('/device-auth-cli.js')) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : 'Unknown failure';
    process.stderr.write(`Device authentication command failed: ${message}\n`);
    process.exitCode = 1;
  });
}
