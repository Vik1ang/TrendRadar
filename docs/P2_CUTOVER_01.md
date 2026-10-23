# P2-CUTOVER-01：旧 spool 到历史追赶器切换验证

日期：2026-10-23。**本报告只涉及代码、只读源 SQLite 和隔离 PostgreSQL；没有生产写入、部署、Timer 或 AI 调用。**

## 版本及契约

- 分支 `dev`；开始审查时 HEAD `a842cfc`。`523ac71` 是 `060df1c8` 的祖先。annotated tag `ios-mvp-baseline-20261023` 仍指向 `060df1c8`，未改动。
- `docs/ios/openapi-v1.json` 与当前 Fastify 生成的规范化 OpenAPI 在路由/完整 JSON 上一致（8 paths）。本次只改 ingestion repository 的 upsert 状态策略，不改 HTTP Schema 或 OpenAPI。
- 开始时存在的 untracked override、部署文档、SQLite 输出及备份/恢复脚本均保留，未暂存或覆盖。

## 隔离切换演练

### 数据基线与旧 spool

- 将生产 PostgreSQL 通过只读 `pg_dump -Fc` 流恢复到独立 PostgreSQL 16 容器，未落盘生产 dump；恢复基线为 **1,185 articles、3 analyses、733 jobs**，Migration 001/002 均在。
- 复制而非移动生产 spool 到临时私有目录。源 spool SHA-256 为 `bcb045830aeee2acbecd1bbbc6b57bee24ede1737fc975dda481c9dfa143fb0c`，Run ID `beadd52d-893e-4460-9d0d-96f1e04dbda4`，原 `nextIndex=740/1187`。
- 旧同步逻辑在隔离 API/PG 处理剩余 **447** 篇：23 页，新增 446、更新 1；结束 `nextIndex=1187`，临时 pending spool 删除，4 个 source/date marker 正确写入。隔离 DB 为 **1,631 articles、733 jobs、0 attempts**。生产 spool SHA 与 nextIndex 在演练前后不变。

### History 接管与恢复

- 实际只读 discovery 当前枚举 **467 个 date/source 数据库、237 个日期、63,210 条采集记录**。2026-10-21、10-22、10-23 当前分别为 96、96、70 条。此前 14,030 唯一身份/12,728 缺口是旧审计窗口（254 条 capture）的已知基线；本轮没有重算当前 262 条 capture 的新身份总量。
- 发现并修复 `_history_discover()` 的运行缺陷：只把 `P2_HISTORY_EXPORT_MODE=discover` 放在宿主进程环境，未用 `docker exec -e` 传入 TrendRadar 容器，导致真实 history discovery 返回 `ValueError`。修复后实际只读 discovery 成功；新增命令构造回归测试。
- 隔离运行使用旧 spool 完成时产生的 4 个 legacy source/date marker（10/21、10/22 记录 ID 48/31），但 history 使用独立空状态。用实际 SQLite 中每个 10/21–23 source/date 的前两个连续 record，共 12 条 capture，完成后 history cursor 均为 ID 2；展开到真实完整 inventory 后，从 10/21 news record ID 3 继续，没有被 legacy 高水位跳过。完成 record 3 后 cursor 到 3；重放已完成的 12 条范围为 0 条处理。
- 注入 Fastify 已提交、客户端 ACK 丢失：history 状态保留 `pending.nextIndex=0`、source/date cursor 为空；进程恢复使用相同 Run ID 重放并继续。
- 一个仍有 queue job 的文章被模拟为实时 `pending`，后续 history 请求携带 `not_requested`。发现并修复 upsert 会将 `pending` 降级的问题：现在重复的 metadata-only history import 保留已有状态及其 job。隔离验证状态仍为 `pending`，原 job 仍恰好 1 个。
- 历史批次全部为 `not_requested`，演练期间 job 数不增加，analysis attempts 始终 0。另经隔离 API 插入一篇普通新 `pending` 文章，仍生成恰好一个 job；因此实时入队规则不变。
- 本轮有一次更大范围隔离探索在测试 harness 的 30 次调用保护处主动停止（不是算法错误）；随后重置隔离数据库并重新从当前生产快照恢复，再以有界真实 capture 范围完成上述切换验证。本轮**没有**对当前 262 条 capture 做完整三日导入。

## 三日历史回补估算（不执行）

按既有审计基线 12,728 个未覆盖身份、规则命中 3,825、未分类 8,903；分类可重叠，估算沿用 `docs/P2_HISTORY_CATCHUP.md`，不是本轮对更新后 262 条 capture 的重算：

| 方案 | 文章增长估算 | 新增队列 | 存储估算 | 时间与维护 |
|---|---:|---:|---:|---|
| A. 三日全量轻量元数据，不排 AI | 约 12,728 | 0 | 约 26.6 MB | 预计 4–15 分钟；中等，按批次/源失败审计 |
| B. 只导入规则命中项 | 约 3,825 | 0 | 约 8 MB | 预计 1–5 分钟；较低，但永久丢弃其余可重筛元数据 |
| C. 全量元数据，另只为符合条件的文章排队 | 约 12,728 | 约 3,825 | 元数据约 26.6 MB，队列约 0.6–1 MB | 预计 4–15 分钟 + 队列写入；中等，需审计规则版本和队列幂等 |

时间估算是规划范围：旧 254-capture 隔离测试实测 14,030 unique/67,815 normalized observations 总计 246 秒、峰值同步器 RSS 46.6 MiB；本轮的 Fastify per-page `docker exec` 路径明显更慢，更新窗口的完整吞吐未测，必须先小批 dry-run 校准。C 需单独、显式的“只给历史筛选项 enqueue”能力（幂等 `INSERT … ON CONFLICT DO NOTHING`），不能把全量历史文章改成 pending，也不能改变实时 pending 入队。AI 每日预算 0 时即使建立任务也不得产生模型调用。

237 天不纳入三日回补。当前 inventory 已有 63,210 条 capture records，但尚无全量唯一数、总存储、失败率/运行时估算；应另做分段只读容量审计，再决定范围。

## 分阶段生产切换提案（逐段独立授权）

1. **阶段 A — 旧 spool B3（447 篇上限）：** 先备份、确认 SHA/`nextIndex=740`、Migration/Token/预算；每页 20，确认文章/jobs/进度。失败停止并保留 spool；通过相同 Run ID 幂等重放，不自动回滚生产数据。
2. **阶段 B — 部署历史追赶代码和队列契约：** 仅更新 Ingestion API 与宿主同步脚本；本次发现修复及 SQL 状态保护无 DB Migration。备份、灰度只读 discovery/单 capture 验证；health/schema/队列不符则回滚代码镜像，DB 不做破坏性恢复。
3. **阶段 C — 三日历史回补：** 另批准策略与数量。先只读生成去重/分类/磁盘预览，再小批每批最多 20、逐批校验游标与队列；异常暂停，靠 history pending state/确定性 Run ID 恢复。不得清空状态或回滚覆盖并发业务写入。
4. **阶段 D — 后续增量：** 新采集记录受冻结上界约束，历史 cursor 到当前上界后再验证下一条真实 capture 可进入新周期；至少两个真实采集周期并核对 LAN/Relay 内容。
5. **阶段 E — systemd Timer：** 单独授权后方可安装/启用；先核对 Supercronic 间隔、锁、超时、跨午夜、启动恢复与日志；故障时 disable Timer，不清游标/spool。

每段未获批准不得执行。生产 B3、三日/全历史回补、Timer 在本轮均未执行。隔离结果表明旧 spool→新 history 的技术路径可行，**但不构成生产授权或生产 B3 已通过的声明**。
