export interface AppConfig {
  host: string;
  port: number;
  databaseUrl: string;
  apiReadToken: string;
}

export interface IngestionConfig {
  host: string;
  port: number;
  databaseUrl: string;
  ingestionToken: string;
  analysisMaxPerRun: number;
  analysisMaxPerDay: number;
  analysisMaxConcurrent: number;
  analysisMaxAttempts: number;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

function integer(name: string, fallback: number): number {
  const value = process.env[name];
  if (value === undefined || value === '') return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(`${name} must be an integer from 1 to 65535`);
  }
  return parsed;
}

function boundedInteger(name: string, fallback: number, max: number): number {
  const value = process.env[name];
  if (value === undefined || value === '') return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > max) {
    throw new Error(`${name} must be an integer from 0 to ${max}`);
  }
  return parsed;
}

export function loadConfig(): AppConfig {
  return {
    host: process.env.API_HOST ?? '127.0.0.1',
    port: integer('API_PORT', 3001),
    databaseUrl: required('DATABASE_URL'),
    apiReadToken: required('API_READ_TOKEN'),
  };
}

export function loadIngestionConfig(): IngestionConfig {
  return {
    host: process.env.INGEST_HOST ?? '0.0.0.0',
    port: integer('INGEST_PORT', 3002),
    databaseUrl: required('DATABASE_URL'),
    ingestionToken: required('INGEST_TOKEN'),
    analysisMaxPerRun: boundedInteger('INTELLIGENCE_MAX_ANALYSES_PER_RUN', 1, 100),
    // Fail closed until an explicit daily spend/call budget is configured.
    analysisMaxPerDay: boundedInteger('INTELLIGENCE_MAX_ANALYSES_PER_DAY', 0, 1000),
    analysisMaxConcurrent: boundedInteger('INTELLIGENCE_ANALYSIS_MAX_CONCURRENT', 1, 5),
    analysisMaxAttempts: boundedInteger('INTELLIGENCE_ANALYSIS_MAX_ATTEMPTS', 2, 5),
  };
}
