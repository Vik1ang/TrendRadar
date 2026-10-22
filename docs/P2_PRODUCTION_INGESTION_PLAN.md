# P2 生产接入最小方案

**状态：** 只读审查与方案阶段；尚未更改生产数据库、服务或采集配置。
**日期：** 2026-10-22

## 当前证据

- `trendradar` 正在使用 `wantcat/trendradar:latest`，运行入口为 Supercronic，每 30 分钟触发一次；容器运行镜像不含本仓库的 `run_intelligence_pipeline` hook。直接接入 hook 需要替换/重启原容器，本方案不采用。
- `trendradar` 容器将 `/home/zwq/Workspace/TrendRadar/output` 挂载为 `/app/output`。只读查询当日 SQLite 快照得到热榜 2,553 条、RSS 260 条；最新可见抓取记录至 11:02。它们是已有采集产物，不需要重新请求来源。
- 生产 Ingestion API 使用独立 Compose 项目/容器；生产数据库 Migration 状态只有 `001_initial.sql`。文章 452、分类关系 3、分析记录 3、采集运行记录 7。本轮只读，未触碰旧文章或分析。
- 现有 `002_analysis_queue_and_budget.sql` 只创建新文章队列表和调用审计表；不回填既有文章。Migration runner 在单事务内应用 SQL，并通过 `schema_migrations` 版本表使重复运行跳过已应用版本。生产尚未应用。

## 建议接入方式

新增一个**仅同步、不采集**的独立 `ingest-sync` 服务，不改原版 TrendRadar/MCP：

1. 以只读方式挂载同一个 TrendRadar `output` 与 `config` 路径；只读取最近完成的 SQLite crawl 记录及其数据，不调用 NewsNow/RSS Fetcher。
2. 每分钟检查一次抓取记录游标；新 crawl 记录稳定至少 90 秒后，读取该轮最新 hotlist/RSS 数据，经现有 `build_batch()` 标准化、稳定 ID 去重、规则分类，再调用内网私有 Ingestion API。
3. 成功写入后持久化每种 SQLite 数据源的日期/抓取记录游标。进程崩溃时允许重放最后一轮；稳定文章 ID、数据库唯一约束及队列 `ON CONFLICT DO NOTHING` 保证不重复建文/重复排队。服务单实例、单线程执行；无 Docker socket、无外部端口。
4. `INTELLIGENCE_ENABLED=true` 只启用数据导入；`INTELLIGENCE_ANALYSIS_ENABLED=false`、`INTELLIGENCE_MAX_ANALYSES_PER_DAY=0`、每轮 1、并发 1、最大尝试 2 明确 fail-closed。日预算为 0 时禁止模型调用。
5. 若读取/导入失败，保留上次游标并重试；SQLite 无新 crawl 记录时不产生新 Ingestion run。错误只记录类型/状态，不记录凭据。

此方案只新增消费既有 SQLite 的时钟/轮询机制，不会触发第二套来源抓取；恢复时可从最近已完成 SQLite 快照继续。部署前应先在隔离测试中验证 `get_latest_crawl_data()` / `get_latest_rss_data()` 的记录游标和断点重放。

## Migration、部署与回滚

1. 在独立 PostgreSQL 测试库验证 Migration 001+002、重复执行 Migration runner、队列唯一键、租约到期回收、重试上限和每日预算 0。
2. 对当前**实际运行的** `trendradar-lan-acceptance-postgres-1` 执行只读一致性备份到仓库之外的权限受限目录，并验证 `pg_restore --list` 可读取；不要使用默认 Compose project 备份脚本，以免误启另一套数据库。
3. 部署顺序：备份 → 单独应用 002（事务、只增加两张表和索引）→ 只重建/更新 `ingest-api`（保留原 DB/API/Web/Relay）→ 启动单个只读 `ingest-sync`。不重启/替换 `trendradar` 或 `trendradar-mcp`。
4. 首轮确认新增文章数、分类/未分类数、analysis jobs 数、分析 attempt 数均与输入快照相符；每日预算 0 时 attempt 必须为 0。立即用 LAN API/Web 及被允许的 Relay 只读端点读取。
5. 回滚先停止 `ingest-sync`，再回退 Ingestion API 镜像。若 002 后没有新队列数据需要保留，可在单独确认后 drop 新队列表；若产生新数据则先保留/导出这些表，不用整库 restore。全库恢复只作为重大故障恢复路径，会覆盖备份之后的写入，不能自动执行。

生产接入的预期影响：只增加数据库表/索引、短暂重建 Ingestion API、启动一个内部只读同步器；LAN Web/API、Relay 与原版采集/MCP 不需重启。同步器离线时 SQLite 保留原始记录，恢复后可重放。

## 分类抽样与质量边界

对现有 452 篇做只读规则运行：至少命中一个频道 70 篇，382 篇未命中（84.5%）；各频道命中 AI 31、科技 21、财经 19、社会 10，命中可重叠。人工随机样本中，命中样本多数具有相应主题词；有潜在误分类例（仅因标题出现“手机”而命中科技）。未命中样本里有娱乐、军事/外交及泛社会新闻，也有“前三季度经济含金量”等应再评估的宏观经济标题。

热榜 SQLite 本身只存标题、来源、URL及抓取元数据，不存新闻正文/摘要，因此不能靠标准化恢复不存在的摘要。RSS 表有 summary，Adapter 已把它用于分类。可候选补充低歧义词：英伟达/NVIDIA、CPI/PPI/PMI、社融、人民币汇率、进出口/贸易数据；上线前需先对只读历史样本重新计算命中变化并复核误分类，当前不修改规则、不回填历史。

该小样本不能代表四频道总体准确率；在无标注集前只报告规则命中覆盖和人工抽样，不声称 precision/recall。

## LAN / Relay 403 诊断

- 同一只读请求：`GET /api/v1/news?limit=1`，使用受控有效 Bearer。LAN 返回 200。
- Python `urllib` 默认 User-Agent 的 Relay 请求返回 403，响应为 Cloudflare Error 1010 `browser_signature_banned`，并非 Worker allowlist 或后端认证拒绝。
- 相同 URL/凭据用 curl 默认 User-Agent 重试，Relay 返回 200 JSON。`/api/v1/news` 在 Worker allowlist 内；未访问/开放 Private Ingestion。故不需要代码或 Cloudflare Secret 改动。

## 当前决策门槛

- 本文先作为最小生产接入方案提交；之后的 DB 备份/Migration、Ingestion API 部署及同步器生产启动都尚未执行。
- 原版 TrendRadar/MCP 不需要替换或重启；若后续部署方法变化而需要重建/重启它们，须另行获得明确批准。
- P2 生产验收仍须观察至少两轮真实 Supercronic crawl，并逐项验证新增/更新/重复、分类、持久队列、每日零模型调用、LAN 与 Relay 读取以及源容器健康状态。
