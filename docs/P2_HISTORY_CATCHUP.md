# P2 历史 SQLite 逐采集追赶设计

状态：代码与隔离验证完成；未用于生产写入、未部署、未启用 Timer。

## 数据源事实与可恢复边界

- News SQLite 的 `crawl_records.id` 是数据库内单调 `AUTOINCREMENT` 主键，`crawl_time` 有唯一约束。每轮存储在事务尾部写入 crawl record 与来源状态。`rank_history` 没有到 `crawl_records` 的外键，但通过唯一 `crawl_time` 可以精确重建该次采集的**正排名观测集合**；`rank=0` 表示脱榜，不属于该次正向新闻集合。该集合能找回跨轮次短暂出现的新闻，但新闻正文/标题/URL使用 `news_items` 当前保留值，不能恢复被后续更新覆盖的历史字段版本。
- `crawl_source_status` 标记每来源 `success/failed`。失败来源可识别并以 `partial` 终止状态留痕；旧 SQLite 不保留失败来源可能曾抓到但未落库的数据，故无法恢复。应用层捕获并继续的逐条存储错误也未必留下独立错误记录。
- RSS 的 `rss_items` 没有指向 `rss_crawl_records.id` 的成员关系。`first_crawl_time`、`last_crawl_time` 与 `crawl_count` 只表示观测边界/次数，不足以恢复每个 RSS crawl record 的精确成员，也不能恢复已覆盖的中间内容版本。导出器用 `last_crawl_time == crawl_record.crawl_time` 形成可重复扫描的**末次观测 delta**；每条任务标记 `rss_last_observation_delta`，不能解释为精确快照。

## 追赶状态与提交语义

新增 `trendradar.intelligence.history_sync`，本地私有状态文件 `~/.local/state/trendradar-p2-sync/history-state-v1.json`（目录 `0700`、文件 `0600`），无生产数据库 Migration。

1. 从源库发现各 `source/date` 的 capture ID，按日期、创建时间、来源、ID 的稳定顺序处理；一个追赶周期先冻结各库的上界与该上界内 record 清单。后续新采集留给下一周期。
2. 每条 capture 单独从只读 SQLite 导出、标准化、稳定去重，然后先原子持久化待提交文章页和 `nextIndex`。每个 API 页最大 20 篇，Run ID 由 source/date/record/page 确定。
3. API 成功且响应计数与页面数一致后才增加 `nextIndex`。整条 capture 完成后才推进其 `source/date` 游标。API 提交成功但响应丢失时，重放同一 run ID、相同文章 ID/去重键；API 数据库唯一约束保证文章/任务幂等。
4. 进程重启继续已冻结周期；源库中尚未处理的冻结记录消失、上界回退、游标锚点丢失或状态文件损坏均 fail-closed，不跳过、不推进。全局 `flock` 与旧快照同步共用一个锁，避免重叠。
5. 旧 `pending-sync.json` 非空时，新 `--history-apply` 拒绝混用；旧 spool 格式没有改写。旧 spool 完成后，新算法从可读 SQLite 历史 ID 起点独立重放，依靠稳定业务 ID 幂等匹配，不信任旧的 `source/date` 游标。

History apply 必须显式指定 `--history-start-date`、`--history-end-date` 与 `--max-total-articles`。日期窗口为闭区间，最多 31 天；无日期的 `--history-dry-run` 仍可用于只读全量 inventory，但 apply 不会默认扫描全部历史。`--max-total-articles` 是一个冻结追赶周期累计提交的规范化候选上限（跨 capture 的重复也计数），保存在 history state；每次调用另受 `--max-articles`、`--max-records`、`--max-seconds` 限制，每个 API 页最多 20 篇。达到总量上限会保留 pending/cursor，不能跳过；重复使用相同上限不再推进，只有显式提高上限才能继续。

示例（仅说明参数，不表示生产已批准）：

```bash
uv run python scripts/p2_sqlite_sync.py --history-apply \
  --history-start-date 2026-10-21 --history-end-date 2026-10-23 \
  --max-total-articles 75000 --max-articles 20 --max-records 1 --max-seconds 240
```

History 导入使用 `analysisStatus=not_requested`。Ingestion API 现在仅在新增文章明确标为 `pending` 时创建分析任务；历史元数据默认不会制造未授权的 AI 队列。`not_requested` 文章不自动分析。Timer 与生产配置未改变。

## 2026-10-21 至 2026-10-23 覆盖复核

复核原审计文件 `~/.local/share/trendradar/audits/p2-history-coverage-20261021-23.json` 中 12,477 个缺口身份，并从当前原版容器只读重新枚举/导出各次 crawl record，使用生产 `build_batch` 逻辑规范化。新采集记录令 10 月 23 日记录数从审计时的 29 增至 31。

| 日期 | News capture | News 正排名观测 | orphan / 缺字段 | News unique ID | RSS capture | RSS 末次观测 delta / unique ID |
|---|---:|---:|---:|---:|---:|---:|
| 2026-10-21 | 48 | 26,881 | 0 / 0 | 5,818 | 48 | 306 |
| 2026-10-22 | 48 | 26,878 | 0 / 0 | 5,749 | 48 | 290 |
| 2026-10-23（当前） | 31 | 17,252 | 0 / 0 | 3,022 | 31 | 265 |

当前窗口合计 14,030 个规范化唯一 ID；其中 856 已在当前生产 PostgreSQL，1,187 在冻结 spool，741 同时存在；相对生产库与 spool，仍有 12,728 个唯一 ID 未覆盖。原审计的 12,477 个缺口 ID 均仍可从当前 SQLite 导出（12,477/12,477）；额外 251 个唯一 ID 来自之后新增采集记录。当前未覆盖项中，规则分类至少命中一个频道 3,825（30.1%），未命中 8,903（69.9%）。所有未覆盖项均有非空标题、来源 ID 和规范 URL 或 RSS GUID；但“内容完整”不等于可恢复被覆盖的历史版本。窗口实际发现 139 个 crawl record 存在失败来源状态，共 167 条失败状态行：News 10/21、10/22、10/23 分别 9、2、3 条；RSS 分别 60、58、35 条。这些源失败之前未保存的项目不可恢复。

当前 12,728 缺口按本窗口首次观测日期分布为 10/21 5,259、10/22 4,877、10/23 2,592；来源类型 News 12,569、RSS 159。News 缺口主要来源为 jin10 2,554、mktnews 2,083、cls-telegraph 1,646、fastbull-express 1,507、wallstreetcn-quick 1,287；另有 RSS hacker-news 138。原审计清单逐项保留 12,477 条历史缺口的首次采集日期、来源和 record ID；RSS 的 record ID 是基于首次观测时间推断，不是精确成员关系。新增 251 个 ID 来自审计后追加的采集记录，分布在 10/23 新记录。

RSS 的上述数字是去重后的可重扫观测集合，不代表确切逐轮成员；不能以此证明每个 RSS 采集时点完全覆盖。News 热榜的 `rank_history` 可恢复每轮当时排名大于零的保留新闻集合。

对当前三日 RSS 源库另做只读完整性核查：分别 306/290/265 条 `rss_items`，其 `last_crawl_time` 均能匹配同库的 `rss_crawl_records.crawl_time`（未匹配数均为 0）。因此这些保留的 RSS 项均能通过末次观察时间被扫描；仍无法恢复精确的中间轮次成员、被覆盖内容版本，或入库失败而从未落入 `rss_items` 的记录。

## 补录策略评估（仅评估，不执行）

对当前 12,728 个缺口身份：

| 策略 | 新文章量 | 预计存储 | AI 队列量 | 维护成本 |
|---|---:|---:|---:|---|
| 全量追补并全部请求分析 | 12,728 | 约 26.6 MB 元数据 + 约 1–3 MB 队列估算 | 12,728 | 最高：队列/重试/模型预算持续管理；预算为 0 时队列积压，不建议默认采用。 |
| 只补规则命中兴趣频道 | 约 3,825 | 约 8 MB 元数据 + 约 0.6–1 MB 队列估算 | 约 3,825 | 较低运行量，但永久舍弃 8,903 条未命中/证据不足的元数据，未来规则变化无法重筛。 |
| 全量轻量元数据、仅筛选项建立 AI 任务 | 12,728 | 约 26.6 MB 元数据 + 约 0.6–1 MB 队列估算 | 约 3,825（若获批） | 中等：完整保留可检索元数据，另需维护规则版本、筛选及队列预算。当前算法只支持元数据 `not_requested`，未提供批准后筛选任务的开关。 |

隔离 PostgreSQL 16 用 254 条真实 capture 记录、71,872 条原始观测完成导入：规范化去重后共提交 67,815 个观测条目、14,030 个唯一文章，0 个分析任务、分析或模型调用。关系总大小实测：`articles` 25,862,144 B、`article_categories` 925,696 B、`sources` 73,728 B、`ingestion_runs` 2,457,600 B。该导入写入 3,535 个 run 行（同一文章会在多次 capture 中幂等 upsert）。按关系大小和当前 12,728 个缺口线性折算，全量元数据约 26.6 MB（含该窗口同样密度的 run 记录）；这不是生产磁盘容量保证，未含 WAL/备份/膨胀，且只外推三日样本。队列字节未单独实测，策略表中按数据库行与索引规模粗估；去重后每篇标准化 JSON 的大小约 0.5 KiB，非 PostgreSQL 磁盘占用。真实三日隔离追赶含源导出、故障恢复和导入总计 246 秒，提交吞吐约 276 个规范化观测条目/秒；主机侧同步器进程峰值 RSS 46.6 MiB，原 TrendRadar 容器内短命导出 Python 进程的独立内存未单独测量。

若给兴趣命中文章排队，规则命中的唯一文章数为 4,153（其中缺口 3,825），queue 预计分别约 12,728 或 3,825 条（以仅新缺口项排队计算）；实测元数据导入的默认 job 数为 0。任务表/索引字节未实测，不将估算伪装成实测用量。算法每次仅缓存一个 capture 的规范化结果（源导出限制每条 2,000 项、16 MiB），持久待提交页按最多 20 条。完整隔离追赶包含两次源样本读取、失败恢复、Fastify/PG导入与幂等重放，墙钟 246 秒；这不是单次生产 timer 的预计耗时，也不是历史全量性能承诺。

## 隔离真实 SQLite → Fastify → PostgreSQL 演练

三日范围的 254 个真实 SQLite `crawl_records`/`rss_crawl_records` 被只读发现并逐条导出，使用后端实际 Ingestion Fastify 和 PostgreSQL 16 隔离容器（先跑 001、002 migrations）。其中包括连续同日记录、热榜短暂新闻、重复出现和跨日新闻。演练注入：前 40 篇成功提交后第三页提交前失败（pending index 留在 40）；恢复后再让下一页数据库提交成功但丢弃客户端响应（pending index 留在 80），随后按同 run ID 重放并完成。最终 14,030 个唯一文章 ID 与规范化 oracle 一致，6 个 source/date 游标、254 个 capture 状态完成；结束后重放处理量为 0。孤立 DB 有 14,030 articles、3,535 ingestion runs、0 analysis jobs、0 analyses、0 attempts；重复导入未增文章。`not_requested` 元数据导入不建立 AI 队列。隔离容器在测试后移除；生产 PostgreSQL、冻结 spool 和原版 SQLite 均未写入。

当前原版容器全量只读 inventory 为 467 个 date/source SQLite 库、237 个日期、63,202 个 capture records（News 31,697、RSS 31,505），inventory JSON 约 5.55 MB；全部 RSS 日期的未匹配 `last_crawl_time` 项为 0。它证明代码能枚举跨月历史，不代表 63,202 条都已同步；三日演练也不能可靠估算全量所有记录的总文章数/工期。`--max-records` / `--max-articles` 会分段工作，生产 catch-up 仍需分批容量规划与专门授权，不能直接按 30 分钟 Timer 开始全量追赶。

## 生产启用前的剩余事项

- 当前 frozen spool 仍处于生产 `nextIndex=740/1187`，不得运行 history apply；必须先经单独授权完成旧 spool。
- 新状态文件独立于旧 `cursors.json`，不修改、也不迁移生产游标。生产 dry-run/部署/run 需要单独授权和备份。
- 本轮真实数据演练覆盖三日源数据。生产历史追赶应分批限额执行、监控 `partial` 记录并建立审计，不能将容器最新日期文件丢失当成可跳过条件。
- 建议先采用策略三，但在用户决定是否为兴趣项排队、给出每日模型预算之前，保持 `not_requested` 与 AI 预算 0。
