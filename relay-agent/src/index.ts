import { createServer } from 'node:http';
import { HttpsProxyAgent } from 'https-proxy-agent';
import { SocksProxyAgent } from 'socks-proxy-agent';
import WebSocket from 'ws';
import { isAllowedApiPath, parseRelayCommand, type RelayCommand } from './protocol.js';

type AgentConfig = { relayUrl: URL; apiOrigin: URL; token: string; proxy?: URL };

function requiredUrl(name: string, value: string | undefined): URL {
  if (!value) throw new Error(`${name} is required`);
  try { return new URL(value); } catch { throw new Error(`${name} must be a URL`); }
}

function configFromEnv(): AgentConfig {
  const relayUrl = requiredUrl('RELAY_WORKER_URL', process.env.RELAY_WORKER_URL);
  if (relayUrl.protocol !== 'https:' && relayUrl.protocol !== 'wss:') throw new Error('RELAY_WORKER_URL must use HTTPS/WSS');
  relayUrl.protocol = 'wss:';
  relayUrl.pathname = '/agent';
  relayUrl.search = '';
  relayUrl.hash = '';
  const apiOrigin = requiredUrl('LOCAL_API_ORIGIN', process.env.LOCAL_API_ORIGIN ?? 'http://api:3001');
  if (!['http:', 'https:'].includes(apiOrigin.protocol) || apiOrigin.pathname !== '/' || apiOrigin.search || apiOrigin.hash) throw new Error('LOCAL_API_ORIGIN must be a fixed HTTP origin');
  const token = process.env.RELAY_AGENT_TOKEN;
  if (!token) throw new Error('RELAY_AGENT_TOKEN is required');
  const proxy = process.env.PROXY_URL ? requiredUrl('PROXY_URL', process.env.PROXY_URL) : undefined;
  if (proxy && !['http:', 'https:', 'socks4:', 'socks4a:', 'socks5:', 'socks5h:'].includes(proxy.protocol)) throw new Error('PROXY_URL must use HTTP(S) or SOCKS');
  return { relayUrl, apiOrigin, token, ...(proxy ? { proxy } : {}) };
}

function proxyAgent(proxy: URL | undefined): WebSocket.ClientOptions['agent'] {
  if (!proxy) return undefined;
  if (proxy.protocol.startsWith('socks')) return new SocksProxyAgent(proxy);
  // The Relay connection is always WSS. HTTP(S) proxies must tunnel TLS with
  // CONNECT, including when the proxy itself uses a plain HTTP URL.
  return new HttpsProxyAgent(proxy);
}

class RelayAgent {
  private stopping = false;
  private socket: WebSocket | null = null;
  private registered = false;
  private heartbeat: NodeJS.Timeout | null = null;
  private readonly healthServer = createServer((_request, response) => {
    const connected = this.socket?.readyState === WebSocket.OPEN && this.registered;
    response.writeHead(connected ? 200 : 503, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ status: connected ? 'ok' : 'offline' }));
  });

  constructor(private readonly config: AgentConfig) {}

  async start(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      this.healthServer.once('error', reject);
      this.healthServer.listen(9900, '127.0.0.1', resolve);
    });
    let delay = 1_000;
    while (!this.stopping) {
      try {
        const stable = await this.connectOnce();
        if (stable) delay = 1_000;
      } catch (error) {
        this.log('relay connection closed', error);
      }
      if (this.stopping) break;
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 2, 60_000);
    }
  }

  stop(): void {
    this.stopping = true;
    this.healthServer.close();
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.socket?.close(1000, 'agent stopping');
  }

  private connectOnce(): Promise<boolean> {
    return new Promise((resolve, reject) => {
      const agent = proxyAgent(this.config.proxy);
      const socket = new WebSocket(this.config.relayUrl, {
        headers: { Authorization: `Bearer ${this.config.token}` },
        ...(agent ? { agent } : {}),
        followRedirects: false,
        handshakeTimeout: 10_000,
        maxPayload: 2_000_000,
        perMessageDeflate: false,
      });
      this.socket = socket;
      let opened = false;
      let stableTimer: NodeJS.Timeout | null = null;
      let registrationTimer: NodeJS.Timeout | null = null;
      let lastPongAt = 0;
      let stable = false;
      let registered = false;
      socket.once('open', () => {
        opened = true;
        lastPongAt = Date.now();
        registrationTimer = setTimeout(() => {
          if (!registered && socket.readyState === WebSocket.OPEN) socket.close(1011, 'registration timeout');
        }, 10_000);
        this.heartbeat = setInterval(() => {
          if (socket.readyState !== WebSocket.OPEN) return;
          if (Date.now() - lastPongAt > 60_000) { socket.close(1011, 'heartbeat timeout'); return; }
          socket.send(JSON.stringify({ type: 'ping' }));
        }, 25_000);
        console.info('Relay Agent WebSocket opened; awaiting registration');
      });
      socket.on('message', (data) => {
        try {
          const control = JSON.parse(data.toString()) as { type?: unknown };
          if (control.type === 'connected') {
            registered = true;
            if (registrationTimer) clearTimeout(registrationTimer);
            this.registered = true;
            stableTimer = setTimeout(() => { stable = true; }, 30_000);
            socket.send(JSON.stringify({ type: 'ready' }));
            console.info('Relay Agent registered');
            return;
          }
          if (control.type === 'pong') { lastPongAt = Date.now(); return; }
        } catch { /* parseRelayCommand reports malformed messages without forwarding them. */ }
        const command = parseRelayCommand(data.toString());
        if (!command) { this.log('ignored malformed relay command'); return; }
        void this.forward(socket, command);
      });
      socket.once('error', (error: Error) => { if (!opened) reject(error); });
      socket.once('close', (code, reason) => {
        if (this.heartbeat) clearInterval(this.heartbeat);
        this.heartbeat = null;
        if (registrationTimer) clearTimeout(registrationTimer);
        if (stableTimer) clearTimeout(stableTimer);
        if (this.socket === socket) this.socket = null;
        if (this.socket === null) this.registered = false;
        if (opened && registered) resolve(stable); else reject(new Error(`WebSocket closed (${code}: ${reason.toString().slice(0, 100)})`));
      });
    });
  }

  private async forward(socket: WebSocket, command: RelayCommand): Promise<void> {
    let response: Response;
    try {
      const target = new URL(command.path, this.config.apiOrigin);
      if (target.origin !== this.config.apiOrigin.origin || !isAllowedApiPath(target.pathname + target.search)) throw new Error('invalid local API path');
      response = await fetch(target, {
        method: 'GET',
        headers: { Authorization: command.authorization, Accept: 'application/json' },
        signal: AbortSignal.timeout(15_000),
        redirect: 'error',
      });
      const body = Buffer.from(await response.arrayBuffer());
      if (body.length > 1_000_000) throw new Error('local API response exceeds relay limit');
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({
        type: 'response', id: command.id, status: response.status,
        contentType: response.headers.get('content-type') ?? 'application/json', body: body.toString('base64'),
      }));
    } catch {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({
        type: 'response', id: command.id, status: 502, contentType: 'application/json',
        body: Buffer.from(JSON.stringify({ error: { code: 'LOCAL_API_UNAVAILABLE', message: 'Local API request failed' } })).toString('base64'),
      }));
    }
  }

  private log(message: string, error?: unknown): void {
    const summary = error instanceof Error ? error.name.slice(0, 40) : '';
    console.warn(message, summary);
  }
}

let relay: RelayAgent;
try { relay = new RelayAgent(configFromEnv()); }
catch (error) { console.error(error instanceof Error ? error.message : 'Invalid Relay Agent configuration'); process.exit(1); }

const shutdown = () => relay.stop();
process.once('SIGTERM', shutdown);
process.once('SIGINT', shutdown);
void relay.start();
