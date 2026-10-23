// Mirrors each public Fastify error payload. Authentication failures omit
// requestId; route, validation, and backend failures include it.
function apiErrorResponseSchema(code: string, withRequestId: boolean) {
  return {
    type: 'object',
    required: withRequestId ? ['error', 'requestId'] : ['error'],
    additionalProperties: false,
    properties: {
      error: {
        type: 'object',
        required: ['code', 'message'],
        additionalProperties: false,
        properties: { code: { const: code }, message: { type: 'string' } },
      },
      ...(withRequestId ? { requestId: { type: 'string' } } : {}),
    },
  } as const;
}

export const readErrorResponses = {
  401: apiErrorResponseSchema('UNAUTHORIZED', false),
  500: apiErrorResponseSchema('INTERNAL_ERROR', true),
  503: apiErrorResponseSchema('DATABASE_UNAVAILABLE', true),
} as const;

export const queryErrorResponses = {
  400: apiErrorResponseSchema('VALIDATION_ERROR', true),
  ...readErrorResponses,
} as const;

export const articleErrorResponses = {
  400: apiErrorResponseSchema('VALIDATION_ERROR', true),
  ...readErrorResponses,
  404: apiErrorResponseSchema('NOT_FOUND', true),
} as const;
