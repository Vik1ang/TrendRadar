import type { FastifyInstance } from 'fastify';
import { Type, type Static } from 'typebox';
import {
  DeviceAuthError,
  DeviceAuthRepository,
  PairingRateLimiter,
} from '../device-auth.js';

const pairBody = Type.Object({
  deviceName: Type.String({ minLength: 1, maxLength: 80 }),
}, { additionalProperties: false });

const pairResponse = Type.Object({
  deviceId: Type.String({ format: 'uuid' }),
  deviceName: Type.String(),
  accessToken: Type.String(),
  expiresAt: Type.String({ format: 'date-time' }),
  scopes: Type.Array(Type.String()),
}, { additionalProperties: false });

type PairBody = Static<typeof pairBody>;

const errorResponse = Type.Object({
  error: Type.Object({ code: Type.String(), message: Type.String() }),
  requestId: Type.Optional(Type.String()),
}, { additionalProperties: false });

function apiError(code: string, message: string) {
  return { error: { code, message } };
}

export async function registerDeviceAuthRoutes(
  app: FastifyInstance,
  repository: DeviceAuthRepository,
): Promise<void> {
  const limiter = new PairingRateLimiter(5, 60_000);

  app.post<{ Body: PairBody }>('/api/v2/devices/pair', {
    schema: {
      hide: true,
      body: pairBody,
      response: {
        200: pairResponse,
        400: errorResponse,
        401: errorResponse,
        429: errorResponse,
        503: errorResponse,
      },
    },
  }, async (request, reply) => {
    if (request.protocol !== 'https') {
      return reply.code(400).send(apiError('HTTPS_REQUIRED', 'Device pairing requires HTTPS'));
    }
    if (!limiter.consume(request.ip)) {
      return reply.code(429).send(apiError('PAIRING_RATE_LIMITED', 'Too many pairing attempts'));
    }
    const code = request.headers.authorization?.match(/^Pairing\s+(trd_pair_[A-Za-z0-9_-]{43})$/)?.[1];
    if (!code) return reply.code(401).send(apiError('INVALID_PAIRING_CODE', 'Pairing code is invalid or expired'));
    try {
      const device = await repository.redeemPairingCode(code, request.body.deviceName);
      return reply.code(200).send({
        deviceId: device.id,
        deviceName: device.name,
        accessToken: device.accessToken,
        expiresAt: device.expiresAt.toISOString(),
        scopes: device.scopes,
      });
    } catch (error) {
      if (error instanceof DeviceAuthError && error.code === 'INVALID_DEVICE_NAME') {
        return reply.code(400).send(apiError('DEVICE_NAME_MISMATCH', 'Device name does not match this pairing code'));
      }
      if (error instanceof DeviceAuthError) {
        return reply.code(401).send(apiError('INVALID_PAIRING_CODE', 'Pairing code is invalid or expired'));
      }
      throw error;
    }
  });
}
