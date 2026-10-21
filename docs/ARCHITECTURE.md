# TrendRadar Intelligence Center 架构基线

状态：P0/P1/P2/P3 MVP 工作树实现（以当前 `dev` 分支未提交工作区为依据；P4/P5 未完成）

## 目标和边界

这是单用户、内网优先的模块化单体。TrendRadar 仍负责抓取、既有筛选/翻译/报告/通知/MCP；新增 Python 集成适配器在采集完成后把标准新闻和逐篇 AI 分析写入 PostgreSQL。Fastify 是唯一面向客户端的业务 API，React 只通过该 API 读数据。SQLite 继续服务原版 TrendRadar，不承担新产品的主库职责。Cloudflare Relay 仅在 Web MVP 通过后实施，转发被允许的只读业务请求，不保存业务数据。Linux 阶段不创建 SwiftUI 工程。

```text
NewsNow / RSS
      │
TrendRadar crawler + existing SQLite / reports / notifications / MCP
      │ normalized article + existing LiteLLM client for per-article analysis
      ▼
Python intelligence adapter ── private import endpoint / adapter ──► PostgreSQL
                                                                      ▲
React Web ── Nginx same-origin /api/v1 ──► Fastify read API (route → service → repository)
TrendRadar container ── private network / ingest token ──► ingestion API ──► PostgreSQL

Future only, after P3: iOS ── LAN HTTPS Direct ───────────────────────────► Fastify API
                         └─ external: Worker → SQLite DO ↔ Relay Agent ─► same Fastify API
```

## 现有代码边界及复用

- `trendradar/crawler/fetcher.py` 的 `DataFetcher` 复用 NewsNow 请求、重试、代理及链接安全校验；`trendradar/crawler/rss/` 复用 RSS 拉取和解析。
- `trendradar/storage/base.py` 中 `NewsItem` / `RSSItem` / `NewsData` 已有标准化结构；`trendradar/storage/local.py` 与 `trendradar/storage/schema.sql`、`rss_schema.sql` 继续保留按日 SQLite、抓取/排名历史及 RSS 存储。
- `trendradar/ai/client.py` 提供 LiteLLM 客户端，`trendradar/ai/analyzer.py` 提供批量简报分析；`config/ai_analysis_prompt.txt` 服务现有报告。现有简报并非逐篇文章分析，不能直接假装是文章分析。新适配层复用模型调用/配置机制，使用独立的结构化逐篇 Prompt 和 JSON 校验。
- `trendradar/__main__.py` 是现有大型调度入口，包含 crawl、RSS、AI 报告和报告/通知流程。扩展点应是受控的 post-crawl adapter hook / command，不把 PostgreSQL 或 HTTP 业务逻辑堆入该文件。
- `trendradar/context.py` 组装原版配置、SQLite 存储、报告、通知及 AI 工具；不将其升级为新产品 service locator。
- `mcp_server/` 只保留现有 TrendRadar MCP 功能；不由其承担客户端业务 API。
- Docker 当前定义在 `docker/docker-compose.yml`，上游容器启动脚本维护原版 cron 与静态报告。新增 Compose 配置应独立且保留这条运行链，不覆盖既有 `.env`。

## 服务和依赖方向

`postgres` 是唯一新产品业务主库，只加入内部 Docker 网络，不 publish 数据库端口。`api` 公开只读 Fastify API，连接 `postgres`，host 映射默认 loopback、LAN 发布需显式设置。`ingest-api` 是隔离的内部导入服务，只加入 `intelligence-db` internal network、不 publish 端口；使用独立 `INGEST_TOKEN`，其写路由不注册在公开 `api` listener。P2 的 TrendRadar 容器加入同一 internal network 后调用它；不需要直接连接数据库。`web` 静态文件经同源反向代理转发 `/api/v1` 至 `api`；页面不连接数据库、不复刻筛选。`ingestor` Python 适配逻辑与 TrendRadar 同进程，负责标准化/幂等投影和后台分析，然后调用私有导入服务。PostgreSQL 故障时，原版 SQLite 与报告链应继续可用，导入失败记为可重试，而不能删除源数据。

当前新增实现：

```text
backend/src/{app,routes,services,repositories,db}
backend/migrations/                 # PostgreSQL schema migrations
web/src/{api,App,styles}             # P3 React 阅读工作台
trendradar/intelligence/            # P2 适配、标准化和逐篇分析
```

## 可靠性和安全

- URL 规范化后哈希形成稳定 article ID；URL 缺失时使用来源 ID + 规范化标题作为有限降级身份。数据库唯一约束配合 upsert 防重复。
- 每次采集/导入有 run 状态；文章内容导入和关联分类使用事务。模型失败不回滚文章，状态设为 `failed` 并可后台重试；成功的相同 prompt/model 版本不重复请求。
- API 参数采用运行时 schema 校验；统一分页、ISO 8601 UTC 时间和错误 envelope。当前单用户读 API 使用静态 Bearer `API_READ_TOKEN`；若启用 Relay，Worker 的 `CLIENT_API_TOKEN` 必须与它是同一用户令牌，Agent 只持有独立机器级 `RELAY_AGENT_TOKEN`，转发原始客户端 Authorization，Fastify 最终校验。该静态令牌没有 TTL/设备撤销能力，必须在未来上线前补上短期/可撤销凭据或轮换/撤销流程。health 增加稳定 `service: trendradar-intelligence` 标识用于和 TLS host 一起验证服务；标识自身不是安全凭据。私有导入接口单独凭据并仅内网可达。
- Relay 实现已添加但尚无外网验收；仅允许 GET 文章/分类/来源/搜索/health 路由，不能转发写入或任意目标 URL。Web 不经 Relay。未来 iOS 的 LAN Endpoint 必须是有效、受信任的 HTTPS 域名，不可使用 Web 的 `192.168.2.117:8081` 明文入口作为认证 API。
- 不引入 Redis、消息队列、搜索服务或托管设施。PostgreSQL `pg_trgm` 中文标题/摘要检索是否启用需以真实中文样本验收，不能以英文全文分词能力替代证据。

## 部署拓扑

当前 Compose 配置分为上游 `docker/docker-compose.yml` 与新增根目录 `docker-compose.intelligence.yml`；`docs/DEPLOYMENT.md` 给出已经 `docker compose config --quiet` 验证的全栈多文件根命令，带路径/网络 overlay。没有在当前活动 TrendRadar/MCP 容器旁启动重复采集服务。数据库使用持久 Volume 与备份命令；migration 在 API 容器启动时先应用。`.env.example` 是模板；真实密钥不入库。开发阶段不启用任何新付费计划。

## 当前状态 / 基线限制

P1/P2/P3 代码和临时 Compose 端到端冒烟已完成：独立 PostgreSQL、read API、私有 ingest API、React 静态 Web/Nginx 同源代理；真实采集 30 条新闻进入临时数据库，列表/当日日期筛选/动态关键词搜索/详情/分类/状态成功；未授权 API 为 401，公开写端点为 404，JS bundle 不含 token。验证后临时 volume 已清理，正式库不因此包含 smoke 文章。模型 API 未调用；`not_requested` 状态用于显式关闭 AI 验收路径。P4 dry-run 完成但无真实外网凭据；P5 unified Compose/生产网络故障验收未完成。

工作树的 Orca Relay 源码未找到：历史路径 `/Users/vik1ang/orca-relay` 在此 Linux 环境不存在；`/home/zwq/.orca-relay` 只含本机 shell 启动器/状态扩展，不是 Orca 源码。不可据此声称复用了其协议或实现。
