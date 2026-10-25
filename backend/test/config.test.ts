import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadConfig, loadIngestionConfig } from '../src/config.js';

afterEach(() => vi.unstubAllEnvs());

describe('ingestion analysis budget defaults', () => {
  it('defaults daily calls to zero and concurrency to one', () => {
    vi.stubEnv('DATABASE_URL', 'postgres://test');
    vi.stubEnv('INGEST_TOKEN', 'test-token');
    vi.stubEnv('INTELLIGENCE_MAX_ANALYSES_PER_RUN', '');
    vi.stubEnv('INTELLIGENCE_MAX_ANALYSES_PER_DAY', '');
    vi.stubEnv('INTELLIGENCE_ANALYSIS_MAX_CONCURRENT', '');
    vi.stubEnv('INTELLIGENCE_ANALYSIS_MAX_ATTEMPTS', '');

    const config = loadIngestionConfig();

    expect(config.analysisMaxPerRun).toBe(1);
    expect(config.analysisMaxPerDay).toBe(0);
    expect(config.analysisMaxConcurrent).toBe(1);
    expect(config.analysisMaxAttempts).toBe(2);
  });

  it('rejects an invalid or unbounded daily limit', () => {
    vi.stubEnv('DATABASE_URL', 'postgres://test');
    vi.stubEnv('INGEST_TOKEN', 'test-token');
    vi.stubEnv('INTELLIGENCE_MAX_ANALYSES_PER_DAY', '1001');
    expect(() => loadIngestionConfig()).toThrow('INTELLIGENCE_MAX_ANALYSES_PER_DAY must be an integer from 0 to 1000');
  });
});

describe('trusted proxy configuration', () => {
  it('accepts only exact proxy IP addresses and rejects wildcard trust', () => {
    vi.stubEnv('DATABASE_URL', 'postgres://test');
    vi.stubEnv('API_READ_TOKEN', 'test-token');
    vi.stubEnv('API_TRUSTED_PROXY_IPS', '127.0.0.1,::1');
    expect(loadConfig().trustedProxyIps).toEqual(['127.0.0.1', '::1']);
    vi.stubEnv('API_TRUSTED_PROXY_IPS', '*');
    expect(() => loadConfig()).toThrow('API_TRUSTED_PROXY_IPS must contain only exact IPv4 or IPv6 addresses');
  });
});
