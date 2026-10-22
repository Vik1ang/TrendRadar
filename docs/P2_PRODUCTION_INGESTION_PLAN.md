# P2 生产 SQLite 同步方案

**当前状态（2026-10-22）：**方案 B 的只读导出器、增量游标和 dry-run 已实现并验证；生产 PostgreSQL Migration、导入写入和定时任务尚未执行/启用。等待明确的生产变更授权。

## 活动 SQLite 事实源

- 运行中的原版容器 `trendradar` 将 SQLite 持续写入 `/app/output/news/YYYY-MM-DD.db` 与 `/app/output/rss/YYYY-MM-DD.db`。它们是容器当前 mount namespace 中的 ext4 bind mount，不是容器 writable layer。
- `/app/output` 活动目录 inode 为 `30964528`、出生时间 2026-01-27；当前仓库宿主 `output/` inode 为 `31235382`、出生时间 2026-10-20。容器 mountinfo 保留旧目录根 `/zwq/Workspace/TrendRadar-origin/output`，该旧路径已不存在。容器中的数据与宿主当前同名路径不是同一个目录对象；推断为原路径被替换/仓库迁移后，运行容器仍持有旧 bind mount。
- `--volumes-from trendradar:ro` 实测仍解析到宿主当前 `output/`，看不到活动数据库，故不能作为数据通道。原版容器不重建。
- 本轮只读观察到 `/app/output` 总量约 4.7 GiB，其中 news 约 1.3 GiB、RSS 约 120 MiB、HTML 约 3.4 GiB。当前日期热榜与 RSS SQLite 约 3 MiB；同步读取每日数据库中的最新/游标后记录，不复制整棵目录。
- 数据库采用 SQLite `DELETE` journal mode，未发现当前 WAL/SHM sidecar。仍然不裸拷贝运行中的 `.db`；当前方案以只读连接短事务查询。若未来确实需要文件快照，必须使用 SQLite Online Backup API。

## 已实现方案 B

### 读取与协议

`scripts/p2_sqlite_sync.py` 在宿主机运行并通过受控 `docker exec -i trendradar python -` 向现有 Python 解释器传入 `scripts/p2_sqlite_export.py`。不在 TrendRadar 容器安装依赖、写脚本文件或改配置。

导出器对 SQLite 使用 `mode=ro`、`PRAGMA query_only=ON`、500 ms `busy_timeout`，打开短只读事务，并只选择标准化所需的标题、来源、URL、摘要、发布时间及首次/最后采集时间。每个 SQLite 数据库按最新已记录 crawl 与游标边界筛取，范围覆盖上海时区的前一日和当日。新闻项的 `last_crawl_time` / RSS 项的 `last_crawl_time` 用于有限回看；`crawl_records` / `rss_crawl_records` 的单调记录 ID、日期、时间和 `created_at` 组成稳定游标标记。排名历史不用于判定文章增量，不假设它是 PostgreSQL 导入的可靠身份。

导出经带版本、snapshot header、逐项记录和 end marker 的 JSONL 传输；校验完整性、条数与日期边界。每个来源/日期快照最多 2,000 项、整个导出最多 3,000 项、总流量 16 MiB；超限或源锁等待/超时会失败关闭，游标不变，由下次任务重试。未使用 `immutable=1`。

### 标准化、写入、去重与恢复

- 复用 `build_batch()`、稳定 UUID/dedupe key 和规则分类，不调用 NewsNow/RSS Fetcher。
- 每个日期形成唯一 `run_id`；`ingestion_runs` 现有列记录文章数、插入数、更新数，`metadata.sqliteSync` 记录源 SQLite 日期、来源/记录 ID/采集时间、读取数、去重数、源失败数。
- 导入完成后才推进各来源/日期游标；API 失败或进程中断时游标保持原值，重放依赖文章稳定 ID、`dedupe_key` 唯一键和队列 article_id 唯一键保持幂等。
- 若某个来源采集失败但 API 已成功保存本次可用数据，则 run 记 `partial`、保存 `failedCount` 并推进该已提交快照游标；下一次新 crawl 使用 `last_crawl_time >= 游标时间` 回看，恢复的来源可补入。这样避免每 5 分钟重写同一 partial run。
- 宿主 `flock` 阻止重叠运行；系统服务超时和每次 `docker exec` 超时限制挂起。私有 Ingestion API 通过第二次 `docker exec` 在现有 ingest-api 容器内本地调用；Bearer 只从该容器环境读取，不传给宿主、命令参数、日志或新容器。没有应用容器挂载 Docker Socket。
- AI 调用在宿主 pipeline 明确关闭，并在导入前只读校验 API 侧每轮 1 / 每日 0 / 并发 1 / 最大尝试 2。Migration 002 缺失时 `--apply` fail-closed。

`deploy/systemd/trendradar-p2-sync.service` 与 `.timer` 是待批准的安装模板，计划每 5 分钟只检查现存 SQLite，不启动爬虫；本轮未安装、未启动。原版 Supercronic 仍是唯一采集调度器。

## 只读 Dry-run 结果

命令：`uv run --locked python scripts/p2_sqlite_sync.py --dry-run`。连续执行两次，统计输出完全一致。

| 指标 | 结果 |
|---|---:|
| SQLite 快照 | 4（2026-10-21、2026-10-22 的 news/RSS）|
| 源行读取 | 1,530 |
| 规范化去重后候选 | 1,185 |
| 快照/批次重复记录 | 345 |
| 与现有 PostgreSQL ID 重合（预计 upsert） | 8 |
| 预计新增 | 1,171 |
| 规则分类（可重叠） | AI 111、科技 98、财经 114、社会 12 |
| 未分类 | 891 |
| SQLite 采集源失败数 | 4（上一日 news 1、RSS 2；当日 RSS 1）|
| 真实模型调用 / PostgreSQL 写入 | 0 / 0 |

现有 PostgreSQL 共 452 篇，Dry-run 没有修改历史分类、文章或分析。分类计数是本次候选覆盖，不是准确率指标。两个日期各自最新快照可能不含已从榜单消失的旧条目；游标回看用于重试期间仍保存在 daily DB 中、`last_crawl_time` 晚于边界的记录，不宣称可恢复 SQLite 已不存在的数据。

## Migration 与生产门槛

- 生产 `schema_migrations` 只含 `001_initial.sql`；`article_analysis_jobs` 不存在。生产库仍是 452 篇文章，未执行 002。导入仓库会为新文章创建队列行，因此生产 Migration 是写入前置条件。
- 备份 `~/.local/share/trendradar/backups/p2-pre-migration-20261022T031855Z.dump`：模式 0600、85,223 字节；本轮用隔离临时容器只读运行 `pg_restore --list`，能识别为 PostgreSQL 16 custom archive、42 个 TOC 项。未对生产库恢复或写入。
- 在一次性 PostgreSQL 16 容器上运行 Backend migration runner 两次：第一次应用 001/002，第二次不重放；`schema_migrations` 显示两个版本，队列表存在。该结果只证明隔离库幂等，不代表生产已迁移。
- 002 有 `article_analysis_jobs.article_id` 主键/外键及 ready 部分索引，attempts 有 `(run_id, article_id, model_name, prompt_version, analysis_version)` 唯一约束与查询索引；不回填旧数据。Migration 回滚不是自动 drop；需先停同步，再评估新表数据后单独审批。
- 宿主只读验证了生产 API 预算值为 per-run 1、per-day 0、concurrency 1、attempts 2。当前每天上限 0；`INTELLIGENCE_ANALYSIS_ENABLED=false`，真实模型调用为 0。

**待授权步骤：**生产 Migration 002、更新/重建 ingest-api（若镜像需更新）、首次 PostgreSQL 导入，以及安装/启用 systemd timer。Dry-run 不构成这些写操作的授权。生产 Migration 未通过前 `--apply` 会直接失败退出；本轮没有运行 `--apply`。

## 检查与回归

- `uv run --locked pytest -q tests/test_intelligence_sync.py tests/test_intelligence_stream.py tests/test_intelligence_normalize.py`：16 passed。
- `uv run --locked ruff check trendradar/intelligence scripts/p2_sqlite_sync.py scripts/p2_sqlite_export.py tests/test_intelligence_stream.py tests/test_intelligence_normalize.py`：通过。
- `uv run --locked mypy trendradar/intelligence scripts/p2_sqlite_sync.py scripts/p2_sqlite_export.py`：通过。
- Backend `npm run typecheck`、`npm test`（27 tests）、`npm run lint`、`npm run build`：通过。
- 容器检查：原版 TrendRadar、MCP、PostgreSQL、API、Ingestion、Web、Relay 均保持运行，RestartCount=0；没有部署同步器、运行 Migration 或重启任何生产容器。

## Relay 1010（独立问题）

`GET /api/v1/news?limit=1` 的相同认证与路径：LAN Python 默认 User-Agent 返回 200；Relay Python 默认 User-Agent 返回 Cloudflare 403/Error 1010；设置 `TrendRadar-P2-Diagnostic/1.0` 后 Relay 返回 200。记录为 Cloudflare 边缘客户端签名兼容问题；未改 Worker、Secret、整体安全配置或 API allowlist。具体账户 Security Event/规则 ID 尚未核实。
