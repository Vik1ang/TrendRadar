# Intelligence Center 数据模型

PostgreSQL 是新产品唯一业务主库；原版 SQLite schema 不迁移、不删除。所有时间用 `timestamptz` 存 UTC，API 输出 ISO 8601。主键使用 UUID（由导入端稳定派生/保存），不依赖 SQLite 自增 ID。

## MVP 表

### `sources`

`id text primary key`（TrendRadar platform/feed ID）、`name text not null`、`type text not null check (type in ('hotlist','rss'))`、可选 `platform_info jsonb`、`created_at`、`updated_at`。来源展示名变化不改变 ID。

### `articles`

`id uuid primary key`；`source_id text not null references sources(id)`；`title text not null`；`url text`；`normalized_url text`；`summary text`（仅可合法保存的 RSS 摘要/采集文本；不抓取或持久化未授权全文）；`published_at timestamptz`；`first_seen_at timestamptz not null`；`updated_at timestamptz not null`；`content_status text`（`title_only|summary_available|content_available`）；`analysis_status text`（`pending|processing|completed|failed|not_requested`）；`created_at`。

幂等身份：优先 `normalized_url`，其次 `(source_id, normalized_title)`。迁移中对规范 URL 建唯一约束（非空），以及对缺 URL 降级键建唯一约束。规范 URL 去 fragment、移除常见 tracking query、规范 host/scheme；保留可能改变资源身份的 query。URL 规范化和 hash 规则写入适配层测试，不依赖标题变化改变已有 URL 文章 ID。

### `categories` / `article_categories`

`categories(id text primary key, name text not null unique, description text, sort_order int)`；种子分类 `ai`、`technology`、`finance`、`society`。`article_categories(article_id uuid references articles on delete cascade, category_id text references categories, confidence real, assigned_by text, primary key(article_id,category_id))`。分类筛选通过关系表，AI 分类失败时允许文章未分类/待分类，不编造类别。

### `article_analyses`

版本化记录，不覆盖文章主表：`id uuid primary key`、`article_id uuid references articles on delete cascade`、`summary text`、`key_facts jsonb`、`background text`、`potential_impacts jsonb`、`uncertainties jsonb`、`evidence_scope text`（title/summary/content）、`model_name text`、`prompt_version text`、`analysis_version int`、`status text check (pending|processing|completed|failed)`、`error_code text`、`analyzed_at timestamptz`、`created_at timestamptz`。幂等唯一键 `(article_id, model_name, prompt_version, analysis_version)`；新版本保留历史，查询 API 默认返回该文章最新成功分析，并单独呈现状态。

### `ingestion_runs`

`id uuid primary key`、`started_at timestamptz`、`finished_at timestamptz`、`status text check (running|completed|partial|failed)`、`source_count int`、`article_count int`、`inserted_count int`、`updated_count int`、`analysis_succeeded int`、`analysis_failed int`、`error_summary text`、可选 `metadata jsonb`。对详细错误只存必要的截断信息，不存密钥/完整敏感请求。

## 索引、事务和备份

- articles `(published_at desc, id)`、`(source_id,published_at desc)`、稳定 URL 唯一键、降级去重键。
- `article_categories(category_id,article_id)`、`article_analyses(article_id,analyzed_at desc)`、`ingestion_runs(started_at desc)`。
- 搜索先用参数化 ILIKE 子串满足中文可预测性；用真实样本（国债、人工智能、大模型、DeepSeek、AI Agent、英伟达）验收后再决定 `pg_trgm` 扩展/GIN 索引，避免未经验证声称全文搜索可用。
- 单文章 upsert、分类关联和对应 run 计数在数据库事务内；AI 调用在事务外执行，结果通过版本幂等键单独写入。失败重跑不重复文章也不重复已成功的分析。
- PostgreSQL Volume 是运行时持久化，不等于备份。交付提供 `pg_dump` 与恢复演练命令，并要求备份文件放在 Volume 外。
