# P2 生产接入最小方案

**状态：** 生产接入被 SQLite bind mount 不一致阻塞；尚未更改生产数据库、服务或采集配置。
**日期：** 2026-10-22

## 当前证据

- `trendradar` 正在使用 `wantcat/trendradar:latest`，运行入口为 Supercronic，每 30 分钟触发一次；容器运行镜像不含本仓库的 `run_intelligence_pipeline` hook。直接接入 hook 需要替换/重启原容器，本方案不采用。
- `docker inspect` 显示 `trendradar` 将 `/home/zwq/Workspace/TrendRadar/output` 挂载为 `/app/output`。但宿主当前 `output` 与运行容器 `/app/output` 的 device 相同、inode 不同（容器 inode 30964528，当前宿主 inode 31235382）；运行容器保留的是旧 bind 目录。容器内只读查询当日快照有热榜 2,553 条、RSS 260 条，最新抓取记录至 11:02；新建诊断容器挂同一路径却看不到该数据。`--volumes-from trendradar:ro` 也重新绑定当前路径，不能读到旧挂载。因此不应直接启用普通 bind-mount 侧车。
- 生产 Ingestion API 使用独立 Compose 项目/容器；生产数据库 Migration 状态只有 `001_initial.sql`。文章 452、分类关系 3、分析记录 3、采集运行记录 7。本轮只读，未触碰旧文章或分析。
- 现有 `002_analysis_queue_and_budget.sql` 只创建新文章队列表和调用审计表；不回填既有文章。Migration runner 在单事务内应用 SQL，并通过 `schema_migrations` 版本表使重复运行跳过已应用版本。生产尚未应用。

## 建议接入方式

在输出目录对齐前，以下服务方案**不可部署**。修复路径后的目标仍是新增一个**仅同步、不采集**的独立 `ingest-sync` 服务：

1. 以只读方式挂载同一个 TrendRadar `output` 与 `config` 路径；只读取最近完成的 SQLite crawl 记录及其数据，不调用 NewsNow/RSS Fetcher。
2. 每分钟检查 SQLite 中已提交的最新 crawl 记录；读取该轮最新 hotlist/RSS 数据，经现有 `build_batch()` 标准化、稳定 ID 去重、规则分类，再调用内网私有 Ingestion API。SQLite 抓取记录在每轮采集事务提交时才可见。
3. 成功写入后持久化每种 SQLite 数据源的日期/抓取记录游标。进程崩溃时允许重放最后一轮；稳定文章 ID、数据库唯一约束及队列 `ON CONFLICT DO NOTHING` 保证不重复建文/重复排队。服务单实例、单线程执行；无 Docker socket、无外部端口。
4. `INTELLIGENCE_ENABLED=true` 只启用数据导入；`INTELLIGENCE_ANALYSIS_ENABLED=false`、`INTELLIGENCE_MAX_ANALYSES_PER_DAY=0`、每轮 1、并发 1、最大尝试 2 明确 fail-closed。日预算为 0 时禁止模型调用。
5. 若读取/导入失败，保留上次游标并重试；SQLite 无新 crawl 记录时不产生新 Ingestion run。错误只记录类型/状态，不记录凭据。

此方案只新增消费既有 SQLite 的时钟/轮询机制，不会触发第二套来源抓取。`sync.py` 已实现，并在只读临时容器中验证了状态游标读写和失败重放的单测；但因为当前新挂载看不到原容器所持有的 SQLite inode，生产路径测试未通过，尚不能认为部署就绪。

### 必须先解决的 bind mount 分叉

普通重建会让原容器重新绑定当前宿主路径，而当前活动 SQLite 数据仍在旧目录中；不能直接重建，否则可能使活动容器的 1.3 GiB 热榜与 120 MiB RSS 历史数据不可访问。只读全量 SQLite 目录复制约 1.42 GiB，且要避免复制期间撞上采集写入。建议先由用户批准以下受控窗口：检查目标盘空间 → 在采集间隔内对活动 SQLite 做一致性导出/安全归档 → 保留当前宿主旧目录 → 将活动数据与当前宿主数据分开归档并核对 → 仅重建 TrendRadar 使 bind source 重新对齐（临时令 `IMMEDIATE_RUN=false`，避免额外立即抓取；cron 保持原 30 分钟）→ 确认文件数/行数/最新 run 与归档一致 → 再启动只读 sync 服务。原版 MCP 不需重启。未获明确批准前，不执行导出、覆盖、重建或网络改动。

## Migration、部署与回滚

1. 在独立 PostgreSQL 测试库验证 Migration 001+002、重复执行 Migration runner、队列唯一键、租约到期回收、重试上限和每日预算 0。
2. 对当前**实际运行的** `trendradar-lan-acceptance-postgres-1` 已完成只读一致性备份：`~/.local/share/trendradar/backups/p2-pre-migration-20261022T031855Z.dump`，模式 0600、85,223 字节，`pg_restore --list` 可读取。不要使用默认 Compose project 备份脚本，以免误启另一套数据库。
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

## 当前验证与决策门槛

- 方案先提交于 Commit `83f1329`，随后验证发现 bind mount inode 不一致，故修正了最初“直接只读挂载即可”这一假设。生产 DB 备份已完成；Migration 001+002 在隔离 PostgreSQL 上连续运行两次，第二次为 no-op。生产 Migration、Ingestion API 部署、同步器启动仍未执行。
- `sync.py` 的专项测试 12 项通过、Ruff 与 mypy 通过；同步器镜像构建成功，但只读挂载实际生产数据路径的 smoke 得到空快照，证明当前路径不可用。生产 PostgreSQL 数据保持 452 articles / 3 category relations / 3 analyses / 7 ingestion runs。
- `GET /api/v1/news?limit=1` 同一有效认证：LAN 200；Python urllib 默认 UA 的 Relay 403 响应是 Cloudflare Error 1010 `browser_signature_banned`，Worker allowlist 接受该公开只读路由；同 URL 使用 curl 默认 UA 返回 200 JSON。没有 Cloudflare/Relay 配置变更。
- 分类规则 452 条 dry-run 为 AI 31、科技 21、财经 19、社会 10，至少一个频道 70，382 未命中。人工样本 8 个命中大多主题合理，其中“手机”导致科技命中存在歧义；8 个未命中样本含娱乐/军事外交等无关主题，也出现“前三季度经济含金量”这一宏观漏判。热榜 SQLite 只有标题，无正文/摘要；RSS 有摘要。当前不改历史标签，不将小样本视为准确率评估。
- 当前没有找到不修改原版容器且可访问其旧 bind inode 的安全数据通道。若用户不批准 TrendRadar 单容器受控重建，P2 生产入库需暂停；不使用 Docker socket、扩大网络连接或额外 NewsNow 抓取来绕过。
- P2 生产验收仍须观察至少两轮真实 Supercronic crawl，并逐项验证新增/更新/重复、分类、持久队列、每日零模型调用、LAN 与 Relay 读取以及源容器健康状态。
