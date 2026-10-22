# P2 批量导入与生产上线准备记录

日期：2026-10-22
分支：`dev`
生产写入 / 服务部署 / Timer：本轮均未执行

## 批量同步实现

- `scripts/p2_sqlite_sync.py --apply --max-articles 20` 每次最多导入 20 篇**标准化去重后的文章**；默认上限也是 20，允许 1–1000。
- 同步器将规范化后的稳定排序文章写入私有、原子替换的 `pending-sync.json`（目录 `0700`、文件 `0600`），固定当前待同步快照；不是截断 JSONL 后把遗漏数据当作完成。
- 每个批次成功提交到 Ingestion API 后才保存批次位置；所有批次完成后才推进对应的 source/date 游标。
- API 提交成功但响应丢失时，批次重发使用相同文章身份和确定性 Run ID；数据库文章与分析队列采用现有幂等写入。
- 若 SQLite 在待处理快照期间继续更新，先完成冻结快照；游标只推进到该快照 marker，后续运行再导入较新的 marker。新闻、RSS、跨日期和午夜回看使用同一机制。
- 降低恢复运行的 `--max-articles` 上限会采用更小值，不会超过本次调用上限。
- 导入显式设为 `analysisStatus=not_requested`；生产自动 AI 仍关闭，日预算 0。

## 验证证据

- Python 全量测试：`uv run pytest -q` → 60 passed，12 subtests passed。
- Ruff / Mypy：变更文件检查通过。
- Backend：Vitest 27 passed；typecheck、lint、build 通过。
- 从活动 TrendRadar SQLite 只读导出并规范化 40 篇真实文章，在隔离 PostgreSQL 16 执行恢复、Migration 002 和 API 导入：
  - 初始恢复：452 篇文章、3 条分析；Migration 002 后数量保持 452 / 3，队列和调用记录均为 0。
  - 第 1 批提交 20 篇后模拟“服务端提交成功、客户端未收到响应”；重试后文章仍为 20 篇新增、队列仍为 20，无重复。
  - 第 2 批新增 20 篇；最终为 492 篇文章、3 条分析、40 个队列任务、0 条分析尝试。每日预算为 0，真实模型调用 0。
  - 该隔离演练使用从真实 SQLite 选出的 40 篇规范化样本，不代表完整生产快照已导入。
- 备份命令 `scripts/p2-safe-postgres-backup.sh` 通过活动 Compose 项目 `trendradar-lan-acceptance` 的精确服务标签定位 PostgreSQL，不依赖根目录 `.env`；生成 PostgreSQL 16 custom-format 到 `~/.local/share/trendradar/backups/`，不覆盖既有归档。
- 新备份归档权限 `0600`，目录 `0700`；`pg_restore --list` 完整性检查通过。随后使用同一生产快照归档恢复到隔离 PostgreSQL 16 并完成 Migration 002 与导入演练。
- Compose 插值在内存中校验通过：项目名、Token 注入目标、PostgreSQL 依赖、Ingestion 私有网络、Web 8081 映射及每日预算 0 正确；未输出或记录凭据。

## 生产边界与下一步

- 生产状态未变：452 篇文章、3 条分析、仅 Migration 001。PostgreSQL、API、Ingestion API、Web 均 healthy，restart count 为 0。
- 本轮没有执行生产 Migration、生产同步、服务重建、Timer 安装/启用或模型调用；原版 TrendRadar / MCP、Relay 和 LAN 服务未修改。
- 新版 Ingestion API 只完成源码 build / 先前的本地镜像 build；运行中的容器仍是旧版本。部署必须先应用 Migration 002，再只重建 `ingest-api`，然后做有界导入。不得先部署依赖队列表的 API。
- `ingest-token` 已保存至 `~/.config/trendradar/ingest-token`，取值沿用当前健康 Ingestion API 的活动凭据，未轮换；此前未找到可信备份副本。API Read Token 保留原值。两者及 `compose.env` 保持用户私有权限。
- 下一生产步骤仍需独立明确授权：新生产备份、Migration 002、仅部署 `ingest-api`、有界首次导入和幂等复跑。本轮结论为**技术准备通过，生产写入未授权且未执行**。
