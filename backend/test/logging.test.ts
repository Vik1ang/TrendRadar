import { describe, expect, it } from 'vitest';
import pino from 'pino';
import { Writable } from 'node:stream';
import { API_LOG_REDACT_PATHS } from '../src/logging.js';

describe('API log redaction', () => {
  it('redacts Authorization and Cookie values from structured request logs', async () => {
    const chunks: string[] = [];
    const destination = new Writable({
      write(chunk, _encoding, callback) {
        chunks.push(String(chunk));
        callback();
      },
    });
    const logger = pino({ redact: [...API_LOG_REDACT_PATHS] }, destination);
    const apiToken = 'api-token-test-marker';
    const cookie = 'session-cookie-test-marker';

    logger.info({ req: { headers: { authorization: `Bearer ${apiToken}`, cookie } } }, 'request');
    await new Promise<void>((resolve) => destination.end(resolve));

    const output = chunks.join('');
    expect(output).not.toContain(apiToken);
    expect(output).not.toContain(cookie);
    expect(output).toContain('[Redacted]');
  });
});
