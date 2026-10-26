import { DurableObject } from 'cloudflare:workers';
import { allowedDeviceReadPath, allowedReadPath, allowedReadQuery, allowedReadRequest } from './allowlist';
import { clientReadAuthorizationAccepted, isDeviceBearerCandidate, tokenMatches } from './client-auth';
import { RequestCorrelator } from './correlator';
import { nextRateBucket } from './rate-limit';

export interface Env {
  RELAY_HUB: DurableObjectNamespace<RelayHub>;
  CLIENT_API_TOKEN: string;
  RELAY_AGENT_TOKEN: string;
}

function error(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status });
}

async function hubRequest(env: Env, pathname: string, init?: RequestInit): Promise<Response> {
  const id = env.RELAY_HUB.idFromName('single-user-api');
  return env.RELAY_HUB.get(id).fetch(`https://relay-hub.internal${pathname}`, init);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === '/health' && request.method === 'GET') {
      try {
        const response = await hubRequest(env, '/_control/status', {
          headers: { 'x-relay-control': env.RELAY_AGENT_TOKEN },
        });
        const relay = await response.json() as { connected?: boolean };
        return Response.json({ status: relay.connected ? 'ok' : 'degraded', relay: relay.connected ? 'connected' : 'offline' }, { status: relay.connected ? 200 : 503 });
      } catch {
        return error(503, 'RELAY_UNAVAILABLE', 'Relay session is unavailable');
      }
    }
    if (url.pathname === '/agent' && request.method === 'GET') {
      if (!(await tokenMatches(request.headers.get('authorization')?.replace(/^Bearer\s+/i, '') ?? null, env.RELAY_AGENT_TOKEN))) {
        return error(401, 'UNAUTHORIZED', 'Relay agent authentication required');
      }
      if (request.headers.get('upgrade')?.toLowerCase() !== 'websocket') return error(426, 'WEBSOCKET_REQUIRED', 'WebSocket upgrade required');
      return env.RELAY_HUB.get(env.RELAY_HUB.idFromName('single-user-api')).fetch(request);
    }
    if (!allowedReadRequest(request.method, url.pathname)) return error(404, 'NOT_FOUND', 'Route not found');
    const authorization = request.headers.get('authorization');
    if (!(await clientReadAuthorizationAccepted(authorization, env.CLIENT_API_TOKEN))
      || (isDeviceBearerCandidate(authorization) && !allowedDeviceReadPath(url.pathname))) {
      return error(401, 'UNAUTHORIZED', 'Authentication required');
    }
    if (!allowedReadPath(url.pathname)) return error(403, 'PATH_NOT_ALLOWED', 'API path is not allowed through Relay');
    if (!allowedReadQuery(url.searchParams)) return error(400, 'QUERY_NOT_ALLOWED', 'Query parameter is not allowed');
    if (url.href.length > 2048) return error(414, 'URI_TOO_LONG', 'Request URL is too long');
    try {
      const response = await hubRequest(env, '/_dispatch', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-relay-control': env.RELAY_AGENT_TOKEN },
        body: JSON.stringify({ method: 'GET', path: `${url.pathname}${url.search}`, authorization: request.headers.get('authorization') }),
      });
      return response;
    } catch {
      return error(503, 'RELAY_UNAVAILABLE', 'Relay agent is offline or the request timed out');
    }
  },
};

type Dispatch = { id: string; method: 'GET'; path: string };
type RelayMessage =
  | { type: 'ready' | 'pong' }
  | { type: 'ping' }
  | { type: 'response'; id: string; status: number; contentType: string; body: string };

export class RelayHub extends DurableObject<Env> {
  private readonly pending = new RequestCorrelator<Response>();
  private rateTableInitialized = false;

  constructor(state: DurableObjectState, env: Env) {
    super(state, env);
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === '/agent' && request.method === 'GET') {
      if (!(await tokenMatches(request.headers.get('authorization')?.replace(/^Bearer\s+/i, '') ?? null, this.env.RELAY_AGENT_TOKEN))) {
        return error(401, 'UNAUTHORIZED', 'Relay agent authentication required');
      }
      if (request.headers.get('upgrade')?.toLowerCase() !== 'websocket') return error(426, 'WEBSOCKET_REQUIRED', 'WebSocket upgrade required');
      const pair = new WebSocketPair();
      const client = pair[0]; const server = pair[1];
      for (const previous of this.ctx.getWebSockets('agent')) previous.close(1012, 'agent session replaced');
      this.ctx.acceptWebSocket(server, ['agent']);
      server.serializeAttachment({ role: 'agent', sessionId: crypto.randomUUID(), connectedAt: Date.now(), ready: false });
      server.send(JSON.stringify({ type: 'connected' }));
      return new Response(null, { status: 101, webSocket: client });
    }
    if (request.headers.get('x-relay-control') !== this.env.RELAY_AGENT_TOKEN) return error(401, 'UNAUTHORIZED', 'Internal Relay request rejected');
    if (url.pathname === '/_control/status' && request.method === 'GET') {
      return Response.json({ connected: this.agentSocket() !== null });
    }
    if (url.pathname !== '/_dispatch' || request.method !== 'POST') return error(404, 'NOT_FOUND', 'Route not found');
    if (!this.consumeRateLimit()) return error(429, 'RATE_LIMITED', 'Relay request limit exceeded; retry later');
    const socket = this.agentSocket();
    if (!socket) return error(503, 'RELAY_OFFLINE', 'Relay agent is not connected');
    if (this.pending.size >= 32) return error(429, 'TOO_MANY_REQUESTS', 'Relay is at capacity');
    let dispatch: Omit<Dispatch, 'id'> & { authorization?: unknown };
    try {
      const contentLength = Number(request.headers.get('content-length') ?? 0);
      if (contentLength > 8_192) return error(413, 'REQUEST_TOO_LARGE', 'Relay request exceeds the size limit');
      const text = await request.text();
      if (new TextEncoder().encode(text).byteLength > 8_192) return error(413, 'REQUEST_TOO_LARGE', 'Relay request exceeds the size limit');
      const value: unknown = JSON.parse(text);
      if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid payload');
      const payload = value as Record<string, unknown>;
      if (Object.keys(payload).some((key) => !['method', 'path', 'authorization'].includes(key))) throw new Error('unexpected field');
      if (payload.method !== 'GET' || typeof payload.path !== 'string'
        || typeof payload.authorization !== 'string' || !/^Bearer\s+[^\s]{1,480}$/i.test(payload.authorization)) throw new Error('invalid request');
      dispatch = payload as Omit<Dispatch, 'id'> & { authorization?: unknown };
    } catch {
      return error(400, 'INVALID_REQUEST', 'Relay request must be JSON');
    }
    const target = new URL(dispatch.path ?? '', 'https://local.invalid');
    if (dispatch.method !== 'GET' || target.origin !== 'https://local.invalid' || !allowedReadPath(target.pathname)) {
      return error(403, 'PATH_NOT_ALLOWED', 'Relay only allows approved read-only API paths');
    }
    let queryAllowed = true;
    const queryCounts = new Map<string, number>();
    target.searchParams.forEach((_value, key) => {
      const count = (queryCounts.get(key) ?? 0) + 1;
      queryCounts.set(key, count);
      if (!['q', 'category', 'source', 'from', 'to', 'limit', 'offset'].includes(key) || count > 1) queryAllowed = false;
    });
    if (!queryAllowed || target.href.length > 2048) return error(400, 'QUERY_NOT_ALLOWED', 'Relay query is invalid');
    const id = crypto.randomUUID();
    const response = this.pending.wait(id, 20_000, error(504, 'RELAY_TIMEOUT', 'Relay request timed out'));
    try {
      socket.send(JSON.stringify({ id, method: 'GET', path: `${target.pathname}${target.search}`, authorization: dispatch.authorization }));
    } catch {
      this.pending.resolve(id, error(503, 'RELAY_UNAVAILABLE', 'Relay agent disconnected'));
    }
    return response;
  }

  webSocketMessage(socket: WebSocket, message: string | ArrayBuffer): void {
    if (typeof message !== 'string' || message.length > 2_000_000) { socket.close(1009, 'invalid message'); return; }
    let payload: RelayMessage;
    try { payload = JSON.parse(message) as RelayMessage; } catch { socket.close(1007, 'invalid JSON'); return; }
    if (payload.type === 'ping') { socket.send(JSON.stringify({ type: 'pong' })); return; }
    if (payload.type === 'ready') {
      const attachment = socket.deserializeAttachment() as { role?: string; sessionId?: string; ready?: boolean } | null;
      if (attachment?.role === 'agent' && attachment.sessionId === this.currentSessionId()) {
        socket.serializeAttachment({ ...attachment, ready: true });
      }
      return;
    }
    if (payload.type !== 'response' || !('id' in payload)) return;
    if (socket !== this.agentSocket()) return;
    if (!this.pending.size) return;
    if (typeof payload.id !== 'string' || !Number.isInteger(payload.status) || payload.status < 200 || payload.status > 599
      || typeof payload.body !== 'string' || payload.body.length > 1_500_000 || typeof payload.contentType !== 'string') {
      this.pending.resolve(payload.id, error(502, 'INVALID_AGENT_RESPONSE', 'Relay agent returned an invalid response'));
      return;
    }
    try {
      const bytes = Uint8Array.from(atob(payload.body), (character) => character.charCodeAt(0));
      this.pending.resolve(payload.id, new Response(bytes, {
        status: payload.status,
        headers: { 'content-type': payload.contentType.slice(0, 160) || 'application/json' },
      }));
    } catch {
      this.pending.resolve(payload.id, error(502, 'INVALID_AGENT_RESPONSE', 'Relay agent returned an invalid response body'));
    }
  }

  webSocketClose(socket: WebSocket): void {
    const active = this.currentSocket();
    if (active === socket || active === null) this.pending.resolveAll(error(503, 'RELAY_UNAVAILABLE', 'Relay agent disconnected'));
  }

  webSocketError(socket: WebSocket): void { this.webSocketClose(socket); }

  private agentSocket(): WebSocket | null {
    return this.ctx.getWebSockets('agent').find((socket) => {
      const attachment = socket.deserializeAttachment() as { ready?: boolean } | null;
      return socket.readyState === WebSocket.OPEN && attachment?.ready === true;
    }) ?? null;
  }

  private currentSocket(): WebSocket | null {
    return this.ctx.getWebSockets('agent').find((socket) => socket.readyState === WebSocket.OPEN) ?? null;
  }

  private currentSessionId(): string | undefined {
    const attachment = this.currentSocket()?.deserializeAttachment() as { sessionId?: string } | null;
    return attachment?.sessionId;
  }

  private consumeRateLimit(): boolean {
    if (!this.rateTableInitialized) {
      this.ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS relay_rate_limit (id INTEGER PRIMARY KEY CHECK(id = 1), window INTEGER NOT NULL, hits INTEGER NOT NULL)');
      this.rateTableInitialized = true;
    }
    const window = Math.floor(Date.now() / 60_000);
    const rows = this.ctx.storage.sql.exec<{ window: number; hits: number }>('SELECT window, hits FROM relay_rate_limit WHERE id = 1').toArray();
    const next = nextRateBucket(rows[0], window);
    if (!next.allowed) return false;
    this.ctx.storage.sql.exec(
      'INSERT INTO relay_rate_limit (id, window, hits) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET window = excluded.window, hits = excluded.hits',
      window,
      next.hits,
    );
    return true;
  }
}
