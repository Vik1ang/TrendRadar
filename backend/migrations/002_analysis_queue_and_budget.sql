-- Queue only articles inserted after this migration. Deliberately do not
-- enqueue existing history: enabling analysis later must not batch-process it.
CREATE TABLE article_analysis_jobs (
  article_id uuid PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  model_name text,
  prompt_version text,
  analysis_version integer,
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_expires_at timestamptz,
  last_error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX article_analysis_jobs_ready_idx
  ON article_analysis_jobs(status, available_at, created_at)
  WHERE status IN ('queued', 'failed', 'processing');

CREATE TABLE article_analysis_attempts (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES ingestion_runs(id) ON DELETE CASCADE,
  article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  model_name text NOT NULL,
  prompt_version text NOT NULL,
  analysis_version integer NOT NULL,
  status text NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
  error_code text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (run_id, article_id, model_name, prompt_version, analysis_version)
);
CREATE INDEX article_analysis_attempts_started_idx ON article_analysis_attempts(started_at DESC);
CREATE INDEX article_analysis_attempts_article_idx
  ON article_analysis_attempts(article_id, model_name, prompt_version, analysis_version, started_at DESC);
