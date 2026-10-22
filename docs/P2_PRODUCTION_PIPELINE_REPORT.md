# P2 生产自动化闭环实施报告

**日期：** 2026-10-22
**分支：** `dev`
**结论：** 生产定时闭环未通过；AI 自动分析默认关闭。代码侧已准备可持久化的新文章候选队列和服务端预算/并发/重试保护，但尚未部署到生产 DB/API，且运行中的原版 TrendRadar 明确未接入仓库内 Adapter hook。本轮不修改/重启原始 TrendRadar/MCP，不创建第二套采集调度器。

## 1. 生产状态审计

| 项目 | 实测状态 |
|---|---|
| 原版 `trendradar` | `running`，镜像 `wantcat/trendradar:latest`，运行 4 周，restart count 0 |
| 原版 `trendradar-mcp` | `running`，运行 4 周，restart count 0 |
| 调度模式 | `RUN_MODE=cron`、`CRON_SCHEDULE=*/30 * * * *`、`IMMEDIATE_RUN=true` |
| overlap | `/entrypoint.sh` 执行 Supercronic 未带 `-overlapping`；Supercronic 默认同一 job 未完成时不启动重叠实例，参见[上游文档](https://github.com/aptible/supercronic#duplicate-jobs) |
| 运行中的 Adapter | 容器 `/app/trendradar` 中未发现 `run_intelligence_pipeline` hook；`INTELLIGENCE_ENABLED` 与分析开关未设置 |
| 生产 Ingestion API | healthy，连接现有 PostgreSQL；本轮未更新或重启 |
| 生产 PostgreSQL | 本轮只读检查；未应用 migration 或写入数据 |

仓库本地 `trendradar/__main__.py` 已有 crawl 后可选 Adapter 调用，但该代码不在当前运行的 `wantcat/trendradar:latest` 容器里。生产开启需要重建/更新并重启原 TrendRadar 容器；本轮明确不做，因此不能声称当前生产 Cron 已接入 Adapter。

最近容器日志尾部仍可见 NewsNow 请求 HTTP 500。读取当前日期 SQLite 快照时，热榜为 0 条、RSS 为 0 条。本轮没有制造或伪造新文章增量验收。

## 2. PostgreSQL 只读数据基线

- Articles：452。
- 分类关系：3（AI 1、财经 1、社会 1、科技 0）。
- 分析记录：3；文章分析状态：completed 2、failed 1、not_requested 175、pending 274。
- 历史规则 dry-run（未写回 452 篇）：AI 命中 31、科技 21、财经 19、社会 10；频道允许重叠；至少命中一个频道 70 篇，382 篇（84.5%）未命中。

规则 dry-run 样例仅用于识别覆盖：AI 有 DeepSeek/AI 新闻；科技有开源模型/芯片；财经有国债/营收；社会有灾害/公共事件。现有规则是标题/摘要关键词兜底，未命中不代表文章不属于频道；未批量更新既有分类。

## 3. 本轮实现

### 现有调度复用

没有新建 cron、sidecar scheduler 或第二条采集链。正式接通应复用 TrendRadar 现有每 30 分钟 Supercronic crawl 完成后的 Adapter hook。当前容器不含此 hook，故仍待受控升级。

### 基础分类与幂等

- 新文章仍由 Python `build_batch()` 在 AI 调用前执行轻量规则分类。
- Backend 在入库时仅对真正新插入的文章应用基础规则标签。
- 普通重复导入不再清除/替换已有分类；只有显式 `replaceCategories=true` 的 AI 成功结果可更新标签。
- 历史文章不因 `002` migration 自动进入队列；历史分类也没有回填。

### 分析队列与预算保护

- 新 migration `backend/migrations/002_analysis_queue_and_budget.sql` 增加新文章队列及逐次调用审计表。
- 队列只在新文章入库时创建；队列按首次采集时间处理，超预算的文章仍可延期。
- `analysis-start` 在 PostgreSQL transaction advisory lock 内原子执行日额度、单次 run 额度、并发、文章重试次数与 lease 检查；模型调用开始前先记录 attempt，进程崩溃也占用日预算。
- 成功文章按模型/Prompt/分析版本幂等跳过；失败作业一小时后可重试，默认最多 2 次；processing lease 为 10 分钟，过期后可恢复。
- Python 自动分析默认关闭；示例默认每 run 1 条、每日 0 条（即没有明确预算时拒绝一切模型调用）、并发 1、最多尝试 2 次。每日限额 0 是有意 fail-closed，不是漏配置后无限调用。

未启用任何真实 AI 调用。日额度、模型单价与费用上限需由用户后续明确后才能把 daily limit 从 0 改为正数。

## 4. 验收结果

| 验收 | 结果 | 证据/限制 |
|---|---|---|
| 现有调度周期/重叠控制 | 通过审查 | 原 Cron 每 30 分钟；Supercronic 默认不重叠；容器未修改 |
| Adapter 生产自动触发 | 未通过 | 运行镜像无 hook，Adapter env 未设置；本轮按要求未更新/重启原容器 |
| 新增文章基础分类 | 代码测试通过，生产未验证 | Python 规则测试；生产没有当前日期新文章 |
| 历史分类回填 dry-run | 通过只读 dry-run | 452 篇的命中统计见上；没有 UPDATE/INSERT 历史标签 |
| 真实增量与去重 | 未验证 | 当前日期 SQLite 无可导入新条目，NewsNow 最近错误 500；没有从别处伪造样本 |
| 每 run/每日预算、并发、Deferred | SQL/单测通过，生产未启用 | 后端预算测试 5 项；Python per-run/deferred/fail-closed 测试；真实生产 cap 为 0 |
| AI 失败、重试、幂等 | 代码测试与隔离 PostgreSQL SQL 往返通过 | 无本轮真实模型请求；历史文章未排队 |
| Web / Relay 一致性 | 当前检查失败 | 使用受控 token 文件做同一只读请求，LAN API 返回 200、Relay 返回 403；未修改 Worker Secret，未宣称通过 |
| 原版 TrendRadar/MCP 不受影响 | 通过 | 两容器保持运行且 restart count 不变；未重启/修改 |

### 测试命令

- `cd backend && npm test` — 26 tests passed（其中预算/队列测试 5 项）。
- `cd backend && npm run build` — passed。
- `cd backend && npm run typecheck` — passed。
- `cd backend && npm run lint` — passed。
- `./.venv/bin/python -m pytest -q` — 45 passed，12 subtests passed；相关 Adapter/分析/补导定向测试 21 passed。
- `./.venv/bin/ruff check trendradar/intelligence tests/test_intelligence_pipeline.py tests/test_intelligence_normalize.py tests/test_intelligence_backfill.py tests/test_intelligence_analyzer.py` 与 `./.venv/bin/mypy trendradar/intelligence` — passed。
- 迁移 `001_initial.sql` + `002_analysis_queue_and_budget.sql` 在一次性隔离 PostgreSQL 中通过；实际 repository 往返验证新文章入队、单次预算允许 1 次、第二次在日限额处被拒绝、分析完成后 attempt/job 状态变为 completed。测试容器已清理。
- 数据库现状：452 articles / 3 article_categories / 3 article_analyses / 7 ingestion_runs；本轮 migration 没有应用到生产库，未改变上述基线。

## 5. 文件和部署边界

改动集中于 `.env.example`、`docker-compose.intelligence.yml`、`docker/trendradar-intelligence.override.yml`、`backend/` migration/API/测试，以及 `trendradar/intelligence/`、相关测试和本文档。生产没有部署这些代码；原版 TrendRadar/MCP、Relay、Web、PostgreSQL、Cloudflare Secrets 均未修改。

## 6. 结论和后续门槛

**P2 生产自动化闭环目前不能判定通过。** 当前已有的实际自动化证据是 2026-10-21 受限一次性试跑，不是生产 Cron；这轮不能补出新真实增量，因为当前 SQLite 无当天条目且采集日志有 NewsNow 500。新代码必须先部署 migration 和 Ingestion API，再通过原版 TrendRadar 受控更新让既有调度 hook 生效。该操作会重启原 TrendRadar，且当前 `IMMEDIATE_RUN=true` 会马上执行一次；本轮用户明确要求不修改/重启该容器，所以停止在此处。

继续生产验收前需满足：

1. 明确 AI 每日调用上限和费用预算；在此之前继续保持分析关闭、日额度 0。
2. 安排并授权一次原版 TrendRadar 更新/重启窗口，确保只用现有 Cron，不双开第二调度器。
3. 验证新的真实采集批次 inserted/updated、分类落库和重复采集不重复建文章；不要对现有 452 篇做历史回填。
4. Relay 当前返回 403 的认证/入口问题需按独立 P4 任务核查；本轮不更改 Cloudflare Secrets。
