# TrendRadar 上游集成审查

审查对象：当前 checkout（`dev`，最近提交 `657094e`，项目版本 `6.11.0`，Python `>=3.12`）与公开上游 `sansan0/TrendRadar`。当前 origin 是 `Vik1ang/TrendRadar` fork；工作树干净。公开上游仓库首页读取于 2026-10-20，显示 v6.11.0 / Python、Docker、MCP、RSS、NewsNow、AI filter/translation/report 能力，与本地顶层结构一致：[上游仓库](https://github.com/sansan0/TrendRadar)。本审查不修改上游生产部署。

## 当前结构与启动

- `pyproject.toml` + `uv.lock`：应用锁定 `requests`、`PyYAML`、`feedparser`、`litellm`、`fastmcp` 等；安装要求 Python 3.12+。
- `trendradar/__main__.py`：CLI/调度主流程和 `NewsAnalyzer`。`_crawl_data()` 走 `DataFetcher` 并转换 `NewsData`，RSS 流程由 `RSSFetcher` 完成；随后执行原有 AI 报告、HTML 报告和通知。扩展时避免继续加入业务分支。
- `trendradar/context.py`：封装 config、频率词、SQLite 存储、report、notification、翻译/AI筛选。
- `trendradar/crawler/fetcher.py` + `crawler/rss/`：NewsNow API 与 RSS parser/fetcher；可复用，不另写 crawler。
- `trendradar/ai/client.py` + `ai/analyzer.py`：LiteLLM client 与批量新闻报告 AI。`AIAnalysisResult` 是整轮简报（事件摘要/热点分析等），不是每条文章的分析记录；新产品需要独立逐篇输出 schema 和验证，但复用模型 client/配置。
- `trendradar/storage/base.py`、`storage/local.py`、`storage/schema.sql`、`rss_schema.sql`：NewsItem/RSSItem 数据模型与本地按日 SQLite。`news_items` 以 `url + platform_id` 部分唯一，RSS 以 feed/url、可选 guid 去重；另存排名历史、title changes、crawl 状态。此库继续服务原版功能，不替换为 PostgreSQL。
- `trendradar/report/`、`notification/`：既有报告和九类通知输出不由 Intelligence Center 取代。
- `mcp_server/`：既有 MCP 服务和 SQLite 报告查询/分析工具，不改造成 Fastify 业务 API。
- `config/`：上游 `config.yaml`、关键词、AI prompt、RSS 源等；`.dockerignore` / Dockerfile 复制运行依赖。
- `docker/docker-compose.yml`：上游镜像 Compose，挂载 config/output，运行 cron/once 并启动报告 Web server；`docker-compose-build.yml` 可本地 build。`docker/.env` 是本机配置文件，不把它当模板或提交秘密。
- `tests/`：原有功能测试，依赖 Python 项目安装和 pyproject 依赖。

## 可复用能力与新增责任

| 需求 | 复用 | 新增责任 |
|---|---|---|
| 热榜采集 | `DataFetcher` / NewsNow API | 归一化稳定身份投影 |
| RSS | `RSSFetcher`, `RSSParser`, `RSSItem` | 导入合法摘要、来源映射 |
| 已有筛选 | 关键词及 AI filter pipeline | 产品频道分类（AI/科技/财经/社会）映射和可校验结果 |
| 模型 | `AIClient` / LiteLLM config | 逐篇 JSON analysis schema、prompt 版本、状态重试 |
| 采集记录 | SQLite crawl/source records | PostgreSQL `ingestion_runs` 产品级统计 |
| 报告/通知/MCP | 保持原样 | 不在 Fastify/Web 重做这些功能 |
| 长期产品数据 | 不使用日分区 SQLite 作为客户端主库 | PostgreSQL migration、articles/categories/analysis/API |

## 导入时机与失败语义

推荐在原版 crawl 和 SQLite save 成功后、HTML/通知收尾附近调用独立 adapter。adapter 输入 `NewsData`/`RSSData`，先 idempotent upsert article + source + classification，再执行每篇 analysis，最后独立更新 analysis 记录及 run 计数。原版 `__main__.py` 只保留少量 hook/调用；批量报告分析仍按原来配置运行。数据库暂时不可用时，不阻止已有 SQLite、HTML、通知；恢复后可用 `python -m trendradar.intelligence.backfill --days N` 从 SQLite 受控补导，默认不调用模型；`--analyze` 才会重新分析。模型失败不丢 article，记录可重试状态；已完成的相同模型/prompt 版本跳过。

需要实际实测其“采集后”挂载位置与原版 once/cron 调度，不把报告分析误认为后台逐篇分析。默认配置 `AI_ANALYSIS` 只有启用后才进行报告分析；本地未提供模型 Key 时无法完成真实 AI 验收。数据源 RSS summary 可持久化，非 RSS 原文不得因链接可达就自动全文抓取/长期保存。

## Orca Relay 可用性

历史 macOS 路径 `/Users/vik1ang/orca-relay` 在当前 Linux 机器不可见。已搜索 `/home/zwq/Workspace` 和 `/home/zwq`，只发现 `/home/zwq/.orca-relay`，内容仅为 OMX shell launcher/runtime extensions，不是 Cloudflare Worker、Durable Object 或 Bridge Agent 源码。故目前无法审查历史路由、协议、身份认证、请求关联、心跳/代理/重连，也没有复用证据。严格不接触正在使用的 Orca 服务；P4 要么等待只读源码副本，要么将新 Relay 作为独立实现，使用独立账号/凭据和 Worker namespace。

## P0 基线验证

- `git status --short --branch`：`## dev`，干净。
- `git log -1 --oneline`：`657094e docs: 更新赞助与社区展示`。
- 初始裸 `pytest -q`：项目没装入系统 Python，收集失败；随后创建项目本地 uv 环境并按锁文件安装依赖完成测试。
- 基线确认版本为 TrendRadar 6.11.0、Python >=3.12，分支 `dev`、起始工作树干净。

## P1 实施验证（2026-10-20）

- 新增 `backend/` Fastify read API、独立私有 `ingest-api`、`backend/migrations/001_initial.sql`、独立根目录 Compose 文件和 `.env.example`；没有改动 `docker/docker-compose.yml`、现有 SQLite schema 或正在运行的 `trendradar` / `trendradar-mcp` 容器。仅在 `__main__.py` 增加 feature-flag adapter 调用。
- Backend `npm run build`、`npm run typecheck`、`npm run lint`、`npm test` 均通过（当前 11 tests）。独立临时 PostgreSQL Compose 中重复导入 inserted=1 后 updated=1；新增重导入状态回归验证：已完成分析在 `not_requested` 重导入后仍保持 completed；实际 PostgreSQL 新闻列表可分别读取来源 `summary` 与 AI `analysisSummary`，且仅出现在已完成 AI 摘要中的关键词可被实际 PostgreSQL 搜索命中；公开 listener 的写入路由返回 404；私有导入 API 的非法 JSON 返回统一 400 错误结构。
- 在独立临时 PostgreSQL 16 Compose project 部署迁移；用非生产 fixture 经私有 `ingest-api` 导入两次，计数分别为 inserted=1、updated=1；公开 API listener 的写入路由返回 404。读 API 验证中文搜索、分类筛选、新闻详情、pending 分析、health、Bearer 拒绝和 OpenAPI。
- `docker compose -p trendradar-intelligence-p1test ... up --build --wait` 启动 PostgreSQL、read API、private ingest API 成功，均 healthy；PostgreSQL 和 ingest API 都无 host port publish；随后清理该临时项目和测试 volume。既有 `trendradar` 与 `trendradar-mcp` 容器仍在运行。
- P2/P3 临时 PostgreSQL/Web Compose 使用真实 `DataFetcher.crawl_websites` NewsNow 百度热搜采集 30 条，经新 Python normalizer 和 internal ingestion 入库；Web 同源反代验证 30 条列表、当日日期筛选 30 条、动态新闻标题子串搜索 1 条、4 类分类、详情、`not_requested` 分析和最近 run；直接 API 未授权 401，公开写路由 404，浏览器 bundle 无业务 tokens。模型调用明确关闭，这不是模型实际生成分析的证据。
- Python `uv run --locked pytest -q`: 33 passed + 9 subtests；Ruff 和 `mypy trendradar/intelligence` 通过。Web build/typecheck/lint 通过、Vitest 2 项通过。
- 临时测试端口仅绑定 loopback；测试 volume/服务已删除。现有 TrendRadar/MCP 容器没有重启或变更。
