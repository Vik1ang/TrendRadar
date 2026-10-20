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

状态（2026-10-20）：基础 schema/migration、Strict Fastify 查询 API、Bearer 认证、分页/中文子串搜索、OpenAPI、独立私有导入 API/凭据和私有 DB Compose 已实现。`npm run build/typecheck/lint/test` 通过（最新 15 项测试）。测试覆盖健康/业务/导入路由在数据库拒绝连接时的稳定 503 响应，且不泄露数据库错误细节；失败分析资源返回稳定 `errorCode`。独立 PostgreSQL Compose 实测重复导入 inserted=1 后 updated=1；新增状态回归检查并用隔离 PostgreSQL 实测已完成分析的文章被 `not_requested` 重导入后，`articles.analysis_status/article_analyses.status` 仍为 `completed/completed`。实际 PostgreSQL 还验证了新闻列表同时返回 `summary` 来源摘要和 `analysisSummary` AI 摘要，以及只在已完成 AI 摘要中出现的 `DeepSeek` 关键词可搜索命中。真实 DataFetcher→Python adapter→PostgreSQL→API 的冒烟也已验证 30 条 NewsNow 百度热搜入库，并由只读 API 查询到。DB/导入服务没有公开映射端口；测试环境均单独创建并清理。

## P2 — TrendRadar 集成

1. 实现专用 Python `trendradar/intelligence/` 适配器，复用 `DataFetcher`、RSS parser、现有 SQLite 事实源与 LiteLLM 客户端。
2. 做稳定 URL/降级键身份、分类结构校验、逐篇分析 JSON 校验；不得把批量报告当逐篇分析。
3. Crawl 结束后按“先保存可用文章，再逐篇分析”的失败隔离方式同步导入；记录 runs，成功结果幂等；Postgres 失败时 SQLite 原始数据保留并可补导。
4. 在 TrendRadar 既有 cron/once 路径增加一个受控适配调用，不破坏报告、通知、MCP；为 URL 去重、AI retry、失败保存和现有流程回归加测试。

验收：真实采集数据已完成写库、API 查询及一次导入；AI 开关关闭的验收路径正确标记 `not_requested`。生产 TrendRadar 容器中发现现有 AI key、私网 OpenAI-compatible base 和模型配置；仅对其 `/models` 做了只读 GET，返回 HTTP 200/57 个 model，不读取或输出 key，不进行生成调用。逐篇真实模型输出、相同文章重采集去重和失败重试仍未执行；生成调用可能消耗现有额度/计费，待确认授权。

状态（2026-10-20）：`trendradar/intelligence/` adapter 和提示词已实现；复用现有 NewsNow/RSS 数据结构及 LiteLLM `AIClient`；新增 URL 规范化与稳定 UUID、历史首次采集时间保留、文章级结构化 JSON 校验、先存文章后分析、按模型/Prompt 版本请求候选文章、失败记录/重试窗口。新增 SQLite 受控补导命令 `python -m trendradar.intelligence.backfill --days N`（范围 1–90，默认跳过 AI；显式 `--analyze` 才调用模型），PostgreSQL 恢复后可用原始 SQLite 记录补入；通过隔离 PostgreSQL + 实际 TrendRadar LocalStorageBackend SQLite 的 Docker 网络端到端验证，补导结果 inserted=1，Fastify 查回 1 条 `not_requested` 新闻。另实际调用 TrendRadar DataFetcher 获取配置中的 11 个 NewsNow 源，共 255 条，经 `run_intelligence_pipeline`→`build_batch`→私有 ingestion API 导入独立 PostgreSQL；首轮 inserted=225/updated=30，重复采集百度源 30 条后 inserted=0/updated=30，文章总数保持 255。分析开关明确关闭，状态为 `not_requested`，没有模型生成调用。单篇真实采集标题在隔离 PostgreSQL 中完成错误恢复 E2E：假客户端模拟 provider outage 后，原文保留且 analysis/run 标记 failed/partial；将失败记录推进到重试窗口外后，确定性假响应成功重试，文章 ID 未变，analysis 转 completed，分类写入；该测试不代表真实模型输出，临时 DB/volume 已清理。实际 PostgreSQL 关键词查询样本中 `DeepSeek` 命中 5、`英伟达` 命中 2，`国债`、`人工智能`、`大模型`、`AI Agent` 当前样本为 0（不是用空结果冒充关键词匹配）。补充修复空采集不记 ingestion run、部分采集源失败仍报告 completed 的状态问题；新增空采集/部分源失败/模型未配置保留文章/补导范围及 SQLite 日期回放等回归测试。Python 全套 `uv run --locked pytest -q` 33 passed + 9 subtests，Ruff 与 mypy 通过。现有 TrendRadar/MCP 运行容器未触碰。当前另外确认运行容器存在一个私网 OpenAI-compatible 模型端点且 models-list 可读；未运行生成调用。真实文章 AI 分析仍需授权后验收。

## P3 — React Web

1. 创建 `web/` React + TypeScript、统一 API client 和路由布局。
2. 新闻工作台、四类频道筛选、来源/日期/关键词 server-side 搜索、分页、详情与已生成分析、采集/API/DB 状态。
3. Compose 静态服务 + same-origin `/api/v1` reverse proxy；页面具备 loading/error/empty/分析状态，不用 mock 替代数据。
4. React 核心流程测试、build/typecheck/lint；本机浏览器/HTTP smoke 检查接口与页面刷新。

状态（2026-10-20）：React + TypeScript 工作台、服务端关键词/来源/日期筛选和分页、四频道、新闻详情/分析状态、系统采集状态、错误/空/加载界面、同源 Nginx reverse proxy 和 Docker Web 服务已实现。新闻列表响应新增独立的 `analysisSummary`，来源 `summary` 与后台 AI 摘要分开显示；真实 PostgreSQL→Fastify 冒烟确认两个字段分别返回正确内容。`npm run build/typecheck/lint/test` 通过（2 项 API client 测试）。在独立 LAN 验收 Compose 中，真实 DataFetcher→`run_intelligence_pipeline`→标准 `build_batch`→私有 ingestion API→PostgreSQL→Fastify，11 源共入库 255 条；Web 只绑定服务器 LAN 地址 `192.168.2.117:8081`，API 仅 loopback `127.0.0.1:13001`，DB 和 ingestion 无宿主机端口。通过该 LAN 地址的 HTTP smoke 确认页面、health、真实新闻列表（255 条）、动态标题搜索、四分类、详情、`not_requested` 分析状态、采集状态均正确，未认证的公网写入路径返回 404。完整 Compose restart 后 PostgreSQL、Fastify、ingestion、Web 四服务健康，仍可读取 255 条持久化新闻（不代表宿主机重启验收）。该 LAN Compose 当前保持运行，以便另一台公司 LAN 设备进行最终浏览器/防火墙验收；本机经 LAN 地址的 HTTP 请求仍不等价于从另一台设备实际浏览器访问。未运行 AI 生成调用。

## P4 — Cloudflare Relay（P3 通过后）

先审查实际 Orca 源码副本；当前 Linux 找到的 `/home/zwq/.orca-relay` 不是项目源码。若未拿到副本则单独设计新协议，不声称复用。Worker/DO/Node Agent 独立配置及认证；只读路由 allowlist、请求 ID/超时/大小限制、WSS/代理/重连测试。部署必须用实际 Cloudflare 凭据和真实 WSS 请求验证，不以 mock 代替。免费额度/费用与 Workers、DO 现行官方计划核验后记录，不擅自升级计划。

实现状态（2026-10-20）：Worker/SQLite DO + Node Agent 已添加；`wrangler deploy --dry-run`、Worker typecheck/3 tests、Agent build/protocol test、Agent production dependency audit 通过。Worker 仅 10ms CPU cap，使用 DO Hibernation API、分离 client/agent/backend tokens、GET allowlist、request ID 乱序关联、20s timeout、32 并发/1MB响应限制。官方额度/计划与部署步骤记录于 `docs/RELAY.md`。尚未实际部署/测试外网 WSS：缺少 Cloudflare credentials/account grant、代理和公司出站授权；不得声称 P4 验收通过。

## P5 — 集成交付

完善 `.env.example`、Compose、运行/停止、迁移、日志、备份恢复、代理和 iOS 交接文档；故障场景逐项测试。P4 若凭据或网络授权不足，不阻止 P1-P3 完成，但须显式保留外网验证 blocker。

实现状态（2026-10-20）：独立 Intelligence Compose 管理 PostgreSQL/Fastify/private ingest/React Web，并以非默认 `relay` profile 定义 Node Agent；备份/恢复脚本和部署、iOS 交接文档已添加。全栈 root Compose 多文件命令与 path/network overlay 已通过 `docker compose config --quiet`；未对当前服务运行它（避免与仍在运行的固定名 TrendRadar/MCP 容器冲突）。备份/恢复已在临时隔离 PostgreSQL project 做 `pg_dump`→drop table→`pg_restore` 回读 `before` 值往返验证；恢复确认 guard 拒绝缺省确认。临时全栈 Compose build/health smoke 通过，测试资源清理；既有 TrendRadar/MCP 服务仍运行且未变更。正式环境容器重启、DB故障注入、模型失败、LAN firewall 等验收未完成。

## 已知风险 / 下一步

- `.env.example` 中是示例值；上线前必须替换独立强随机密钥并确认 LAN 防火墙仅允许可信设备访问 Web。
- 当前缺少 Orca Relay 源码，无法复用历史实现；需要后续用独立凭据自建并测试，不影响 P1-P3。
- 未对现有私网模型服务发起生成调用（费用/额度授权待确认）；`not_requested`/失败状态测试及模型列表可用均不等于真实逐篇分析验收。
- 未提供 Cloudflare 凭据/代理网络授权，尚未部署 Worker/DO 或做真实 WSS 外网访问；P4 需先核实计划用量和运行成本。
- 数据库备份/恢复已在临时隔离 PostgreSQL 完成往返演练；仍需在正式部署环境安排例行备份、重启/数据库故障注入、LAN 防火墙及模型失败验收。
