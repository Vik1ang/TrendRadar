# P2-CUTOVER-02：旧 spool 队列语义与生产切换准备

日期：2026-10-23。此报告只包含只读生产检查、隔离 PostgreSQL/API 演练和本地代码变更；**没有生产写入、生产部署、spool 修改、Timer 或 AI 调用**。

## 结论摘要

- 生产 B1/B2 与先前隔离 B3 使用了不同 Ingestion API 版本。不是同步模拟脚本覆盖 `analysisStatus`：当前生产冻结 spool 的 1,187 条文章均显式标为 `not_requested`，同步器创建 spool 时也明确设置该值。
- 生产 Ingestion API 镜像 `sha256:f266ecde…` 创建于 2026-10-23 13:40 +08；队列条件与 `523ac71` 前的源代码一致，会对**所有新文章**创建 job。`523ac71` 于 15:13 +08 将规则收紧为仅新 `pending` 文章创建 job。先前隔离 B3 使用新代码，所以 446 篇新 `not_requested` 文章没有新增 job。
- 在生产版本 API 的隔离复现中，同一 447 条旧 spool 增加 446 个 job；同时，1 篇已存在的 `pending` 文章被旧 upsert 错降为 `not_requested`。这是当前生产 B3 的真实风险。
- 用当前 `8f91320` API 在同一恢复基线上重跑：新增 446 篇、更新 1 篇，队列仍为 733，分析仍为 3、attempts 为 0；既有 `pending` 状态保持不变。history 首条真实 record 从指定窗口最早记录开始，使用独立状态，不依赖旧游标。
- 因而建议调整上线顺序：**先单独部署当前 Ingestion API 修复，再执行 B3**。本轮未做该生产部署；生产 B3 仍需单独授权。

## 数据、版本和测试边界

- 分支 `dev`，起始 HEAD `8f913201fddc3d3c6a91cab3043b1e39c45ab4a5`；iOS API V1 Tag `ios-mvp-baseline-20261023` 未变，`523ac71` 是该 Tag 的祖先。
- 生产 spool 只读 SHA-256：`bcb045830aeee2acbecd1bbbc6b57bee24ede1737fc975dda481c9dfa143fb0c`；Run ID `beadd52d-893e-4460-9d0d-96f1e04dbda4`；`nextIndex=740/1187`；剩余 447 条。所有 article payload 显式 `analysisStatus=not_requested`。
- 生产只读计数：1,185 articles、3 analyses、733 jobs、0 attempts。原始 TrendRadar/MCP、PostgreSQL、Backend API、Web、Relay 均未更改或重启。
- 备份 `trendradar-intelligence-pg16-20261023T061644Z-4179827.dump` 可恢复，但它恢复为 690 articles、3 analyses、238 jobs、2 migrations，不是当前 1,185/733 基线，故没有把它冒充为正确演练基线。为精确隔离演练，通过运行中 PostgreSQL 只读 `pg_dump -Fc` 得到临时快照，完整恢复验证后为 1,185/3/733/0，Migration 001/002 均存在；临时 dump 不在仓库。
- 原 production API 镜像在私有隔离网络实际运行。另从起始源码构建 `8f91320` API 镜像，二者使用同一份恢复快照和 spool 副本。

## B3 精确复现

两轮均从 `nextIndex=740` 处理 447 候选、每页最多 20，复制 spool 到隔离目录；注入首个 API 事务已提交但 ACK 丢失，确认进度仍在 740，同一 Run ID 重放后继续至 1187。

| 隔离 API | 新文章 | 已有文章更新 | 最终 articles | 最终 jobs | `pending` 状态 | attempts |
|---|---:|---:|---:|---:|---:|---:|
| 生产旧镜像 `f266ecde…` | 446 | 1 | 1,631 | 1,179（增加 446） | 267 → 266 | 0 |
| 当前 `8f91320` 源码镜像 | 446 | 1 | 1,631 | 733（不变） | 267 → 267 | 0 |

API 统计包含一次 ACK 丢失后重放：最终数据库仍只增加 446 个 article，更新同一已存在身份 1 篇；重复提交没有重复文章。旧镜像确实把其中唯一已存在的 pending 文章降级。当前实现遇到 `EXCLUDED.analysis_status='not_requested'` 时保留数据库原状态；新 pending 文章仍会 enqueue，任务唯一约束仍使用 `ON CONFLICT (article_id) DO NOTHING`。

当前实现还在隔离库用实际冻结 spool 的现存 pending 文章做了一次 `not_requested` history upsert：响应为 inserted=0/updated=1，文章状态仍为 pending、队列数量不变。只针对新 pending 入队的 Backend 回归测试也通过。生产旧版本对照已证明其行为不同。

## History 日期窗口、总量上限与游标

- 原只读 history discovery 在传入日期参数后，实际筛选为 `2026-10-21` 至 `2026-10-23`，未遍历其余 234 个日期。第一次读取见 264 records；稍后源 Supercronic 又增加记录，第二次为 266 records。这证明 live 源会变化，正式首次 apply 必须冻结上界；不能将两次 discovery 的记录数混为同一快照。
- 当前窗口六个 `source/date` SQLite 文件的上界为 News 10/21=48、10/22=48、10/23=37；RSS 同为 48、48、37。只读本轮 aggregate `totalItems` 合计 102,669，非规范化/唯一文章数，不能当作导入数量。history 状态冻结六个 source/date 上界；历史源与旧 spool 进度文件分离。
- 增加 `--history-start-date`、`--history-end-date`；`--history-apply` 必须显式提供日期范围和 `--max-total-articles`。日期最多 31 天、范围闭合且格式严格为 ISO 日期。总量上限计**该冻结周期实际提交的规范化候选条目数**（跨 capture 重复也计数），持久写入状态；每次 apply 再受 `--max-articles`、`--max-records`、`--max-seconds` 约束，每个 HTTP 页最多 20。达到总量后停在 pending record，不推进 source/date 游标；重复执行相同上限不会越界。只有显式提高 cap 才能续跑。
- 真实隔离 CLI apply 限定在三天，设置总量 20、单次最多 20：首次处理 `news:2026-10-21` 的最早 record ID 1，20 条候选（15 新增、5 更新），`nextIndex=20/521`，source/date cursor 仍为空；第二次同 cap 处理 0 条，仍停在同一 record。六个 source/date 上界冻结在独立 history state，旧 spool cursor 不影响起点。历史导入均是 `not_requested`，queue 为 733 不变，attempts 为 0。
- 调用命令验证了三日范围过滤及游标；不代表完成该三日补录。当前历史同步器没有真实模型调用路径，此外预算 `perDay=0`；无 AI 调用。

## 三日历史追赶建议

建议仅做**全量轻量元数据、默认 `not_requested`、不建 AI job**，不做筛选入队。之前三日审计的 12,728 个缺口身份、14,030 唯一身份及 67,815 个 normalized observations 是 254 条 capture 的数据；本次只读窗口已增长至 266 条，不能把旧唯一数当作当前准确导入量。本轮不重复 237 天 inventory，也未重算三日唯一缺口。

建议后续授权时采用三天闭区间、第一次 apply 冻结的上界、`--max-total-articles=75,000` 的第一阶段候选提交上限（这是基于上次 67,815 observations 的保守分段建议，不是当前精确预计；如达到上限就暂停并重新评估，不自动扩容），`--max-articles=20`、单次 `--max-records=1` 或小批数、`--max-seconds=240`。先针对冻结上界只读标准化出精确唯一与缺口预览，再确认总量 cap 是否足够；若预览候选数超过上限，须另行授权提高 cap，且代码绝对上限为 100,000。数值为建议，不是本轮生产授权。

## 生产切换方案和停止边界

当前生产 Ingestion API 仍是旧版本，故不建议直接按旧顺序“先 B3、再更新 API”。应拆为：

1. **准备/备份**：确认 spool hash、`nextIndex=740`、新备份/隔离恢复、每日 AI 0、Timer 关闭。
2. **先部署队列修复**（单独授权）：只更新 Compose 项目 `trendradar-lan-acceptance` 的 `ingest-api`，用 health、nullable/invalid Schema、队列预算和状态保护检查，不动 PostgreSQL、Backend API、Web、Relay、TrendRadar/MCP。
3. **B3**（另一个授权）：当前 spool remainder 447、单页 20，完成至 1187；校验新增/更新/去重、jobs、分类、进度与 LAN/Relay 读取，保留既有队列，不做回滚覆盖。
4. **三日 history**（再次单独授权）：只含 `2026-10-21`—`2026-10-23`，freeze 上界，先 dry-run，再使用 history-state、cycle 总量 cap 和每页 20 小批导入。范围外日期/未来日期不能进入冻结清单。
5. **增量与 Timer**：history 上界接续验证、至少两个真实 Supercronic 周期分别验收；Timer 安装/启用另行授权。

本轮停止于生产写入边界：没有生产 B3、API 部署、历史写入、Timer 或 AI 调用。由于生产旧镜像在隔离复现中额外排队 446 并降级一个 pending，**当前 B3 有明确技术阻塞**；安全切换需要先部署当前已测 API 版本，不能直接用旧镜像 B3。此结论不替代部署授权。

## 修改与验证

- 修改：`scripts/p2_sqlite_sync.py`、`trendradar/intelligence/history_sync.py`、`tests/test_p2_sqlite_sync_cli.py`、`tests/test_intelligence_history_sync.py`、`backend/test/ingestion-app.test.ts`、本文档及 `docs/P2_HISTORY_CATCHUP.md`。
- 验证：全量 `uv run pytest -q` 为 74 passed、12 subtests passed；Python Ruff、Mypy 通过。Backend Vitest 35 passed、ESLint、TypeScript typecheck、build 通过。Web 未修改。
- 清理隔离 PostgreSQL/API 容器、临时私有目录、临时网络及测试镜像后，生产 Docker 服务仍均运行，Ingestion/API/Web/PostgreSQL/Relay healthy，原 TrendRadar/MCP running；Timer unit count 为 0。
