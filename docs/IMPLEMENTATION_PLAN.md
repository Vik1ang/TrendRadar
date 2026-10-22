# Linux MVP 实施计划

依据仓库 P0 审查结果制定。按依赖顺序推进，Web 为关键产品里程碑；Relay 排在 Web 验收之后。每阶段结束提交独立变更并运行针对性验证。未成功执行的验证必须如实标为阻塞。

## P0 — 仓库与架构基线

- 已核对分支/版本/目录/依赖/部署；基线范围和本仓库已有能力见 `ARCHITECTURE.md`、`UPSTREAM_INTEGRATION.md`。
- 完成五份 P0 文档；记录基础测试结果和 Orca Relay 源码可用性。
- 验收：源码可复用项、边界、表模型、API、Compose、安全限制明确；不修改生产服务。

## P1 — PostgreSQL + Fastify

1. 创建独立 `backend/`，启用 TS strict、Fastify、参数 schema、日志脱敏和 OpenAPI；小模块 route → service → repository。
2. PostgreSQL Compose service、私有网络、命名持久卷、健康检查；正式版本化 migrations、分类 seed、索引与唯一约束。
3. 实现健康、新闻列表/详情/分析、分类、来源、PostgreSQL 中文子串搜索和仅内部写入接口。
4. 建 unit/API/DB 集成测试，覆盖去重、参数错误、未授权、分页、中文搜索、DB down；提供迁移和备份恢复命令。

验收：Migration 从空库部署成功；strict/typecheck/lint/test 通过；服务能查询 PostgreSQL；DB 不对宿主机/公网开放；OpenAPI 与实际路由一致。

状态（2026-10-20）：基础 schema/migration、Strict Fastify 查询 API、Bearer 认证、分页/中文子串搜索、OpenAPI、独立私有导入 API/凭据和私有 DB Compose 已实现。`npm run build/typecheck/lint/test` 通过（最新 16 项测试）。测试覆盖健康/业务/导入路由在数据库拒绝连接时的稳定 503 响应，且不泄露数据库错误细节；失败分析资源返回稳定 `errorCode`，非失败状态省略空错误码并为可空分析文本保留 JSON null。独立 PostgreSQL Compose 实测重复导入 inserted=1 后 updated=1；新增状态回归检查并用隔离 PostgreSQL 实测已完成分析的文章被 `not_requested` 重导入后，`articles.analysis_status/article_analyses.status` 仍为 `completed/completed`。实际 PostgreSQL 还验证了新闻列表同时返回 `summary` 来源摘要和 `analysisSummary` AI 摘要，以及只在已完成 AI 摘要中出现的 `DeepSeek` 关键词可搜索命中。真实 DataFetcher→Python adapter→PostgreSQL→API 的冒烟也已验证 30 条 NewsNow 百度热搜入库，并由只读 API 查询到。DB/导入服务没有公开映射端口；测试环境均单独创建并清理。

## P2 — TrendRadar 集成

1. 实现专用 Python `trendradar/intelligence/` 适配器，复用 `DataFetcher`、RSS parser、现有 SQLite 事实源与 LiteLLM 客户端。
2. 做稳定 URL/降级键身份、分类结构校验、逐篇分析 JSON 校验；不得把批量报告当逐篇分析。
3. Crawl 结束后按“先保存可用文章，再逐篇分析”的失败隔离方式同步导入；记录 runs，成功结果幂等；Postgres 失败时 SQLite 原始数据保留并可补导。
4. 在 TrendRadar 既有 cron/once 路径增加一个受控适配调用，不破坏报告、通知、MCP；为 URL 去重、AI retry、失败保存和现有流程回归加测试。

验收：真实采集数据已完成写库、API 查询及一次导入；AI 开关关闭的验收路径正确标记 `not_requested`。生产 TrendRadar 容器中发现现有 AI key、私网 OpenAI-compatible base 和模型配置；仅对其 `/models` 做了只读 GET，返回 HTTP 200/57 个 model，不读取或输出 key，不进行生成调用。逐篇真实模型输出、相同文章重采集去重和失败重试仍未执行；生成调用可能消耗现有额度/计费，待确认授权。

状态（2026-10-20 至 2026-10-21）：`trendradar/intelligence/` adapter 和提示词已实现；复用现有 NewsNow/RSS 数据结构及 LiteLLM `AIClient`；新增 URL 规范化与稳定 UUID、历史首次采集时间保留、文章级结构化 JSON 校验、先存文章后分析、按模型/Prompt 版本请求候选文章、失败记录/重试窗口。新增 SQLite 受控补导命令 `python -m trendradar.intelligence.backfill --days N`（范围 1–90，默认跳过 AI；显式 `--analyze` 才调用模型），PostgreSQL 恢复后可用原始 SQLite 记录补入；通过隔离 PostgreSQL + 实际 TrendRadar LocalStorageBackend SQLite 的 Docker 网络端到端验证，补导结果 inserted=1，Fastify 查回 1 条 `not_requested` 新闻。另实际调用 TrendRadar DataFetcher 获取配置中的 11 个 NewsNow 源，共 255 条，经 `run_intelligence_pipeline`→`build_batch`→私有 ingestion API 导入独立 PostgreSQL；首轮 inserted=225/updated=30，重复采集百度源 30 条后 inserted=0/updated=30，文章总数保持 255。此前该批分析开关关闭，状态为 `not_requested`。单篇真实采集标题在隔离 PostgreSQL 中完成错误恢复 E2E：假客户端模拟 provider outage 后，原文保留且 analysis/run 标记 failed/partial；将失败记录推进到重试窗口外后，确定性假响应成功重试，文章 ID 未变，analysis 转 completed，分类写入；该测试不代表真实模型输出，临时 DB/volume 已清理。2026-10-21 按用户指示使用现有 TrendRadar 模型配置 `openai/opencode-go/deepseek-v4.1-flash`，对一条真实采集且仅有标题的 DeepSeek 相关新闻调用一次生成（`NUM_RETRIES=0`、单篇上限）；第一次临时 runner 只连 `internal` 数据网络，无法访问私网模型 endpoint，记录一条 `MODEL_REQUEST_FAILED`。随后 runner 同时连接原 TrendRadar 的可出站网络与私有 ingestion 网络，`/models` 返回 HTTP 200（57 个模型），再次对另一条真实新闻执行一次生成并经真实 `run_intelligence_pipeline` 写入 PostgreSQL：同一稳定文章 ID 更新、run `completed`、`analysisSucceeded=1`、`analysisFailed=0`；Fastify 详情 API 返回 `completed`，分类为 AI/社会，AI 摘要、要点、背景、影响和不确定项均已持久化，`evidenceScope=title`，模型明确指出输入只有标题、不把未提供细节伪装为事实。该单篇真实生成证明模型调用与持久化路径，不代表整批或定时采集自动分析验收。实际 PostgreSQL 关键词查询样本中 `DeepSeek` 命中 5、`英伟达` 命中 2，`国债`、`人工智能`、`大模型`、`AI Agent` 当前样本为 0（不是用空结果冒充关键词匹配）。补充修复空采集不记 ingestion run、部分采集源失败仍报告 completed 的状态问题；新增空采集/部分源失败/模型未配置保留文章/补导范围及 SQLite 日期回放等回归测试。Python 全套 `uv run --locked pytest -q` 33 passed + 9 subtests；新增模块范围检查 `uv run --locked ruff check trendradar/intelligence` 与 `uv run --locked mypy trendradar/intelligence` 通过。全仓库 `ruff check .` 当前失败（2,161 条既有问题主要落在未改动的 TrendRadar/`docker/manage.py` 代码），未对原版核心进行大规模清理。现有 TrendRadar/MCP 容器未触碰，仅启动并清理独立临时 runner。

2026-10-21 自动化闭环受限试跑：用一次性临时 TrendRadar runner 执行与 cron 相同的 `python -m trendradar`（`RUN_MODE=once`）命令，未重启/修改原始 `trendradar` 或 `trendradar-mcp`，并保持模型调用上限 1、顺序执行、`ai.num_retries=0`、无 fallback，关闭旧报告 AI 与通知；采集数据来自当次真实 NewsNow 获取，不分析 255 条历史批次。API `/api/v1/status` 核实最新 run `partial`：1 个来源采集失败；275 篇当次候选中新增 197/更新 78，1 篇真实模型分析成功、0 篇分析失败，274 篇因单次限额延后；数据库文章总数 452，health/database 均为 `ok`。`partial` 符合源失败与限额延迟的预期状态，不把失败藏为成功。临时容器已退出且不存在；容器写入的临时输出目录曾导致宿主侧 `PermissionError`，随后已通过限定到该临时目录的 root 容器完成清理。该试跑证明 cron/once 共用的代码路径可将一次真实采集接入自动分析并持久化，不等同于已启用或观察过生产 cron 周期；当前原服务仍未启用 intelligence flag。试跑后验收 Compose 四个服务均 healthy，原 `trendradar` 与 `trendradar-mcp` 容器仍 Up 且未触碰。

### P2 生产自动化预算与启用状态审计（2026-10-22）

- **现有调度器，不新增第二套：** 原版 TrendRadar 容器当前 `RUN_MODE=cron`、`CRON_SCHEDULE=*/30 * * * *`、`IMMEDIATE_RUN=true`。其 `/entrypoint.sh` 使用 Supercronic，未传 `-overlapping`；Supercronic 默认等待同一 job 结束后才启动下一次（上游说明：[aptible/supercronic — Duplicate Jobs](https://github.com/aptible/supercronic#duplicate-jobs)）。
- **生产 Adapter 目前没有接通：** 正在运行的镜像是 `wantcat/trendradar:latest`，容器内没有 `run_intelligence_pipeline` hook；`INTELLIGENCE_ENABLED`、`INTELLIGENCE_ANALYSIS_ENABLED` 等变量未设置。仓库当前源码已有 crawl 结束后的可选 Adapter hook，但只有在更新/重建并重启原 TrendRadar 容器后才会进入生产调度。本轮按要求没有修改或重启该容器，也没有另外创建 cron/sidecar，因此定时采集仍在跑，PostgreSQL 自动导入/分类/AI 分析仍**未启用**。
- **现存数据基线（只读）：** PostgreSQL 有 452 篇文章、3 条分类关系（AI 1、财经 1、社会 1、科技 0）、3 条分析记录；分析状态为 completed 2、failed 1、not_requested 175、pending 274。最近一个 ingestion run 是历史 `partial`，记有 274 篇 deferred。现存 452 篇规则分类 dry-run 命中 AI 31、科技 21、财经 19、社会 10，至少命中一个频道 70 篇，382 篇（84.5%）未命中；命中可重叠。该 dry-run 未写回历史。当前日期的本地 SQLite 读取没有热榜或 RSS 记录，最近 TrendRadar 日志尾部仍可见 NewsNow 源 HTTP 500，所以本轮没有伪造“真实增量采集成功”。
- **本轮代码侧闭环准备：** `002_analysis_queue_and_budget.sql` 为迁移后新插入文章建立待分析队列，不回填旧 452 篇；尝试表记录 run/article/model/Prompt/version、结果与时间。PostgreSQL 事务 advisory lock 原子限制每 run/每日配额和并发；租约、最多重试次数、失败后一小时 backoff 支持隔离和恢复。规则分类在入库时只作用于新增文章，普通重复导入不会覆盖既有分类，只有明确 `replaceCategories` 的成功 AI 结果可替换标签。Python 默认 `INTELLIGENCE_ANALYSIS_ENABLED=false`；Compose 示例的每 run 默认 1、每日默认 0（fail closed）、并发 1、最多尝试 2。已成功的版本不会再次排队；超预算作业保留在数据库队列中。
- **本轮未启用/未做：** 没有应用新 migration 到生产 PostgreSQL、没有更新/重启 Ingestion API 或原 TrendRadar/MCP，没有调用任何真实模型，没有对 452 篇历史记录写入分类，没有执行生产定时任务，也没有新文章可用于验证真实增量去重。由于受保护 Worker 业务读取本次返回 403，LAN 业务 API 为 200，未能完成当前时点的 Relay 内容一致性检查；未修改 Cloudflare Secret/Relay。
- **验证：** Backend Vitest 26 项（其中预算/队列测试 5 项）、build、TypeScript typecheck、ESLint 通过；Python 全套 pytest 45 项 + 12 subtests、Adapter/分析/补导相关定向测试 21 项、Ruff 与 mypy 通过。将原 migration 001 与本轮 migration 002 应用于一次性隔离 PostgreSQL，并用实际 repository 完成新增文章→队列→日限额拒绝第二次→分析完成写回的 SQL 往返；临时测试容器已清理。原生产服务容器没有重启。
- **阶段判断：P2 生产闭环未通过。** 缺少 Adapter 与运行中 Cron 的生产接通、实时增量/分类/去重证据，且 migration/API 代码尚未部署。生产启用还需要经批准安排一次原版 TrendRadar 容器更新/重启（该服务 `IMMEDIATE_RUN=true`，会立即采集一次）；AI 长期预算需要明确每日调用量/费用上限。自动分析保持关闭，不能用单测或历史单篇分析冒充生产定时闭环。
- 详细检查、测试和待办见 `docs/P2_PRODUCTION_PIPELINE_REPORT.md`。

## P3 — React Web

1. 创建 `web/` React + TypeScript、统一 API client 和路由布局。
2. 新闻工作台、四类频道筛选、来源/日期/关键词 server-side 搜索、分页、详情与已生成分析、采集/API/DB 状态。
3. Compose 静态服务 + same-origin `/api/v1` reverse proxy；页面具备 loading/error/empty/分析状态，不用 mock 替代数据。
4. React 核心流程测试、build/typecheck/lint；本机浏览器/HTTP smoke 检查接口与页面刷新。

状态（2026-10-20）：React + TypeScript 工作台、服务端关键词/来源/日期筛选和分页、四频道、新闻详情/分析状态、系统采集状态、错误/空/加载界面、同源 Nginx reverse proxy 和 Docker Web 服务已实现。新闻列表响应新增独立的 `analysisSummary`，来源 `summary` 与后台 AI 摘要分开显示；真实 PostgreSQL→Fastify 冒烟确认两个字段分别返回正确内容。`npm run build/typecheck/lint/test` 通过（2 项 API client 测试）。在独立 LAN 验收 Compose 中，真实 DataFetcher→`run_intelligence_pipeline`→标准 `build_batch`→私有 ingestion API→PostgreSQL→Fastify，11 源共入库 255 条；Web 只绑定服务器 LAN 地址 `192.168.2.117:8081`，API 仅 loopback `127.0.0.1:13001`，DB 和 ingestion 无宿主机端口。通过该 LAN 地址的 HTTP smoke 确认页面、health、真实新闻列表（255 条）、动态标题搜索、四分类、详情、`not_requested` 分析状态、采集状态均正确，未认证的公网写入路径返回 404。完整 Compose restart 后 PostgreSQL、Fastify、ingestion、Web 四服务健康，仍可读取 255 条持久化新闻（不代表宿主机重启验收）。该 LAN Compose 当前保持运行，以便另一台公司 LAN 设备进行最终浏览器/防火墙验收；本机经 LAN 地址的 HTTP 请求仍不等价于从另一台设备实际浏览器访问。未运行 AI 生成调用。

2026-10-21 P3 外部设备验收仍待人工：本机检查确认服务器接口为 `192.168.2.117/24`，Web listener 绑定 `192.168.2.117:8081`，API 只绑定 `127.0.0.1:13001`，PostgreSQL 与 ingest 没有宿主机端口；本轮未用第二台 LAN 设备访问，因此不宣称 LAN 浏览器验收通过。请在同一可信局域网的另一台设备浏览器访问 `http://192.168.2.117:8081`，实际确认首页加载和刷新、四分类筛选、关键词搜索（可试 `DeepSeek`）、新闻详情和已完成的分析展示、系统状态；再报告可访问性/错误。宿主机防火墙及公司 Wi-Fi 客户端隔离/ACL 尚未由当前环境验证。

2026-10-21 P2 分类调查与实现：本地数据库中 452 篇只有 3 篇具备频道关系（AI 1、财经 1、社会 1、科技 0）；175 篇分析状态 `not_requested`，274 篇 `pending`。根因是标准化 `build_batch` 一直输出空分类，只有逐篇 AI 成功结果才带分类；超出分析上限、未启用分析或分析失败的新闻因而不带频道。TrendRadar 来源是平台/Feed ID，不是可靠领域标签；当前 `frequency_words.txt` 兴趣词组也没有到产品四频道的权威映射。新增轻量、可解释的标题/已有摘要规则分类，基线在 AI 调用前生成；AI 成功可补充/更新标签，失败不会删除规则标签。只改导入代码及测试，不执行历史回填、不改这 452 篇数据库记录、不调用模型。只读规则 dry-run 覆盖当前样本 31 篇 AI、21 篇科技、19 篇财经、10 篇社会（频道可重叠），另有 382 篇无明确关键词；规则只是基础兜底，后续仍需结合真实采集样本校准，不将它等同于高覆盖率语义分类。

2026-10-21 P3 缺陷修复：BUG-01 根因在统一筛选更新函数无条件删除 `offset`，分页 handler 虽设置下一页 offset、随后立即把它清除；修复为分页变更保留 offset、其他筛选变更重置页码，详情返回显式携带原列表 URL 状态。BUG-02 原 Web 完全未请求既有 `/api/v1/health`；现在区分 Backend API 可达与 PostgreSQL 状态，进入状态页及手动刷新时重试，检查期间先离开绿色状态，网络失败显示不可达；任务 run 的 `partial` 与 errorSummary/deferred/source failure 原样保留。开发验证后仅更新独立 LAN 验收 Compose 的 Web 容器，未重建/重启 API、ingest、PostgreSQL 或原版 TrendRadar/MCP；需 Mac mini 再做真实 Edge/Playwright 页面回归，特别是按钮点击、回退、最后一页。

P3 最终验收（2026-10-21）：用户报告 Mac mini 使用真实 Microsoft Edge + Playwright 验收为 PASS；BUG-01/BUG-02、列表/组合筛选分页、详情返回状态、预生成分析均通过，Console/Page Error/失败请求未观察到。修复提交 `0ba09e6`。验收报告按用户提供路径记录为 `trendradar-p3-acceptance/P3_FINAL_REGRESSION_REPORT.md`（当前 Linux 工作树中该目录/报告及截图不可读取，故本次仅采信用户提供结论，不声称复核原始证据）。API/数据库故障注入未执行，不属于此 PASS 范围。

## P4 — Cloudflare Relay + LAN Direct（P3 通过后）

架构补充：未来 iOS 在公司内优先直连有效 HTTPS 的 LAN API，外网使用 Worker → SQLite DO → Relay Agent → 同一 Fastify API；Web 始终内网直连。无第二 API/数据库或 D1。P4 阶段不创建 SwiftUI 项目。

P4-0 审查结论（2026-10-21）：Relay 草稿 Worker/DO、Agent、allowlist、相关测试、独立 Compose profile 已存在；已修补 session ready/stale-close、透传同一客户端 Bearer 至 Fastify、Agent Bearer/固定目标验证、重定向拒绝和重复 query 限制。Orca 检查仅发现 `/home/zwq/.orca-relay` 的本机 shell 启动/状态工具，不含源码仓库；借鉴主动连接的架构经验但未复用原始源码。生成物 `node_modules/`、`dist/`、`.wrangler/`、缓存与凭据不入版本控制。详见 `docs/RELAY.md`。

历史阻塞记录（2026-10-21）：当时 Wrangler 未认证，宿主机代理可达但 Docker 代理不可达，因此尚未部署 Worker/DO。该状态随后解除：使用独立 TrendRadar Worker、DO SQLite migration 及获授权 HTTP 代理完成真实部署；没有修改 Xray、防火墙、Orca Relay 或公司网络策略。

### P4 MVP 归档（2026-10-22）

- **Relay MVP 功能验收：通过。** Worker `https://trendradar-intelligence-relay.vik1ang.workers.dev` → SQLite DO `RelayHub` → Linux Docker Relay Agent → Fastify 的真实链路已验证；已读健康、新闻列表/分页、搜索、详情和预生成分析；LAN 与 Relay 使用同一 API 契约。未授权/非法路由及写路径受拒绝；Agent 离线返回 503，LAN Web 独立可用，Agent 恢复后 Relay 恢复。
- **长期稳定性验收：未完成。** 详见 `P4_RELAY_STABILITY_REPORT.md`。按用户指示停止观察时，公网只读 soak 为 29 分 52 秒、30/30 HTTP 200、无超时，不能标记为两小时或长期稳定性通过。
- 观察窗口内 Cloudflare GraphQL 数据报告 Worker/DO errors 为 0；Agent 全部 30 次容器状态采样健康、未重启；窗口后单次内存读数并不能代表趋势。GraphQL Worker 请求数与本地探针相差 2 次，报告中保留为未解释的统计差异。
- 未完成：两小时 soak；Agent RSS 起点/变化/峰值（原 `docker stats` 解析器把 `20.16MiB` 当数值和单位空格分隔，已在 `scripts/p4_relay_memory.py` 修复并以离线样例及当前容器读数验证，但不追补长时间采样）；线上客户端取消和在途断线注入；共享代理断线恢复；实际 Cloudflare Dashboard 剩余额度/DO SQLite 存储字节数；iOS 真机双线路验收。
- 当前 Relay 业务代码、认证密钥和运行服务未因本次归档改动；没有触发生产批量 AI 分析。

### LAN HTTPS 入口预审（2026-10-22；未部署）

运行中的 Web 容器 Nginx 仅 `listen 80`、`server_name _`，将 `/api/v1/` 反代到 Docker 内部 `api:3001`，并为浏览器注入现有静态 Bearer；宿主机仅发布 `192.168.2.117:8081 -> web:80` 与 `127.0.0.1:13001 -> api:3001`。当前未发现 TLS 443 listener、项目配置的内部域名、DNS-01 记录、证书目录或已安装的 certbot/acme.sh。端口 443 当前可用，但不代表获准绑定。最小方案与权限前置条件见 `docs/IOS_HANDOFF.md`；在现有域名/DNS 控制权和公开受信证书就绪前，不发布 LAN HTTPS Endpoint、不修改 DNS 或重载 Nginx。LAN Web `8081` 保持原样。

### iOS 设备认证预案（仅设计）

当前 Backend `backend/src/auth.ts` 仅通过常量时间比较校验静态 `API_READ_TOKEN`；Web Nginx 注入该共享值，Relay Worker 当前入口也校验同一兼容 token，Agent 的 `RELAY_AGENT_TOKEN` 独立。iOS 发布前应采用增量设备凭据：Fastify 增加按设备独立签发/到期/撤销的高熵 Bearer 凭据及哈希存储，Backend 成为 LAN/Relay 最终校验方；保留现有静态 Bearer 供 Web 兼容，Bridge 凭据不变。Worker 只放行格式受限的设备凭据到只读 allowlist，真实有效性仍由同一 Fastify 校验，限流/拒绝路径仍在 Worker；不写设备业务数据到 Cloudflare。发行和撤销先用仅本机/运维可执行的管理命令，不暴露公网 Relay 管理接口。完整迁移步骤见 `docs/IOS_HANDOFF.md`；本轮未改生产认证体系。

## P5 — 集成交付

完善 `.env.example`、Compose、运行/停止、迁移、日志、备份恢复、代理和 iOS 交接文档；故障场景逐项测试。P4 若凭据或网络授权不足，不阻止 P1-P3 完成，但须显式保留外网验证 blocker。

实现状态（2026-10-20）：独立 Intelligence Compose 管理 PostgreSQL/Fastify/private ingest/React Web，并以非默认 `relay` profile 定义 Node Agent；备份/恢复脚本和部署、iOS 交接文档已添加。全栈 root Compose 多文件命令与 path/network overlay 已通过 `docker compose config --quiet`；未对当前服务运行它（避免与仍在运行的固定名 TrendRadar/MCP 容器冲突）。备份/恢复已在临时隔离 PostgreSQL project 做 `pg_dump`→drop table→`pg_restore` 回读 `before` 值往返验证；恢复确认 guard 拒绝缺省确认。临时全栈 Compose build/health smoke 通过，测试资源清理；既有 TrendRadar/MCP 服务仍运行且未变更。正式环境容器重启、DB故障注入、模型失败、LAN firewall 等验收未完成。

## 已知风险 / 下一步

- `.env.example` 中是示例值；上线前必须替换独立强随机密钥并确认 LAN 防火墙仅允许可信设备访问 Web。
- 当前缺少 Orca Relay 源码，无法复用历史实现；需要后续用独立凭据自建并测试，不影响 P1-P3。
- 真实模型生成已对单篇标题级新闻验收并持久化；另外已在一次性 runner 中以“每次最多 1 篇、无重试/无 fallback、顺序执行”真实试跑 `python -m trendradar` 自动采集→分析→数据库闭环，确认 274 条当次候选延后；原生产 cron/TrendRadar 服务的 intelligence 开关仍关闭，未观察 cron 定时触发。不得直接对全部 255 条存量新闻批量分析；生产启用前仍需明确日额度/成本预算、失败重试策略与调度限额，并由运维显式调整部署配置。
- 未提供 Cloudflare 凭据/代理网络授权，尚未部署 Worker/DO 或做真实 WSS 外网访问；P4 需先核实计划用量和运行成本。
- 数据库备份/恢复已在临时隔离 PostgreSQL 完成往返演练；仍需在正式部署环境安排例行备份、重启/数据库故障注入、LAN 防火墙及模型失败验收。

### P2 生产接入续查（2026-10-22）

- 现有 Supercronic 仍是唯一来源采集调度，每 30 分钟执行一次；原版容器未重建。活动 SQLite 位于运行中 `trendradar:/app/output` 的旧 bind mount inode，当前仓库 `output/` 是不同目录对象。
- 已实现方案 B：宿主机受控 `docker exec` → 原容器只读 SQLite 查询 → 有界 JSONL → 标准化/规则分类 → 既有私有 Ingestion API。无源容器文件改动、无 Docker Socket 挂入、无第二新闻抓取器；systemd 5 分钟触发模板仅落盘，未安装/启用。
- 真实 SQLite 只读 dry-run 连跑两次结果一致：4 个前一日/当日 hotlist/RSS 快照，1,530 行读取，1,185 规范化候选、345 去重，预计对现有 452 篇中 8 篇幂等更新、预计新增 1,177 篇；规则标签 AI 111、科技 98、财经 114、社会 12（可重叠），未分类 891。源读取失败 4 项。数据库写入与模型调用均为 0。
- Migration 002 在临时 PostgreSQL 16 上由 runner 连跑两次，第二次无重放；生产仍只有 Migration 001、452 篇。备份 custom dump 0600、85,223 字节，隔离容器 `pg_restore --list` 验证可读。生产 Migration 002、首次导入与定时启用仍需明确授权；`--apply` 在 Migration 缺失时 fail-closed。
- 新文章 run metadata 持久化源库日期、crawl record ID/时间、读取/去重/失败数；数据库现有列记录插入/更新数，run ID 每次唯一。游标按 source/date/crawl record，前一日与当日双窗口，导入失败不推进；文章稳定 ID/唯一 dedupe key 保障重放幂等。
- AI 保持关闭：生产 API 只读预算确认 per-run 1、per-day 0、并发 1、最多尝试 2；未执行真实模型调用。原版 TrendRadar、MCP、PostgreSQL、API、Ingestion、Web、Relay 均运行且 RestartCount=0。
- Relay Python urllib 默认 UA 的 1010 通过显式 `TrendRadar-P2-Diagnostic/1.0` 复测为 200；归为 Cloudflare 边缘客户端签名兼容，未改 Worker 或安全策略。具体账户规则 ID 未核实。
- **P2 生产闭环未通过。** 当前只完成读取/规范化 dry-run 与导入执行器准备；尚未执行生产 Migration、写入同步、自动调度或两次真实定时周期验收。详见 `docs/P2_PRODUCTION_INGESTION_PLAN.md`。
