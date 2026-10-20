CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE sources (
  id text PRIMARY KEY,
  name text NOT NULL,
  type text NOT NULL CHECK (type IN ('hotlist', 'rss')),
  platform_info jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE categories (
  id text PRIMARY KEY,
  name text NOT NULL UNIQUE,
  description text,
  sort_order integer NOT NULL DEFAULT 0
);

INSERT INTO categories(id, name, sort_order) VALUES
  ('ai', 'AI', 1),
  ('technology', '科技', 2),
  ('finance', '财经', 3),
  ('society', '社会', 4)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE articles (
  id uuid PRIMARY KEY,
  source_id text NOT NULL REFERENCES sources(id),
  title text NOT NULL,
  url text,
  normalized_url text,
  dedupe_key text NOT NULL UNIQUE,
  summary text,
  published_at timestamptz,
  first_seen_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  content_status text NOT NULL DEFAULT 'title_only'
    CHECK (content_status IN ('title_only', 'summary_available', 'content_available')),
  analysis_status text NOT NULL DEFAULT 'pending'
    CHECK (analysis_status IN ('pending', 'processing', 'completed', 'failed', 'not_requested')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX articles_normalized_url_unique ON articles(normalized_url)
  WHERE normalized_url IS NOT NULL AND normalized_url <> '';
CREATE INDEX articles_published_idx ON articles(published_at DESC NULLS LAST, id);
CREATE INDEX articles_source_published_idx ON articles(source_id, published_at DESC NULLS LAST, id);
CREATE INDEX articles_title_trgm_idx ON articles USING gin(title gin_trgm_ops);
CREATE INDEX articles_summary_trgm_idx ON articles USING gin(summary gin_trgm_ops);

CREATE TABLE article_categories (
  article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  category_id text NOT NULL REFERENCES categories(id),
  confidence real,
  assigned_by text NOT NULL DEFAULT 'model',
  PRIMARY KEY (article_id, category_id),
  CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);
CREATE INDEX article_categories_category_idx ON article_categories(category_id, article_id);

CREATE TABLE article_analyses (
  id uuid PRIMARY KEY,
  article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  summary text,
  key_facts jsonb NOT NULL DEFAULT '[]'::jsonb,
  background text,
  potential_impacts jsonb NOT NULL DEFAULT '[]'::jsonb,
  uncertainties jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence_scope text NOT NULL CHECK (evidence_scope IN ('title', 'summary', 'content')),
  model_name text NOT NULL,
  prompt_version text NOT NULL,
  analysis_version integer NOT NULL DEFAULT 1,
  status text NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
  error_code text,
  analyzed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (article_id, model_name, prompt_version, analysis_version)
);
CREATE INDEX article_analyses_latest_idx ON article_analyses(article_id, analyzed_at DESC NULLS LAST);

CREATE TABLE ingestion_runs (
  id uuid PRIMARY KEY,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  status text NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
  source_count integer NOT NULL DEFAULT 0,
  article_count integer NOT NULL DEFAULT 0,
  inserted_count integer NOT NULL DEFAULT 0,
  updated_count integer NOT NULL DEFAULT 0,
  analysis_succeeded integer NOT NULL DEFAULT 0,
  analysis_failed integer NOT NULL DEFAULT 0,
  error_summary text,
  metadata jsonb
);
CREATE INDEX ingestion_runs_started_idx ON ingestion_runs(started_at DESC);
