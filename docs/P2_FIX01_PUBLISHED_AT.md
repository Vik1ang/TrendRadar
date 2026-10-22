# P2 FIX-01 — `publishedAt` 空值契约与断点恢复

日期：2026-10-22

## 结论

根因是 Ingestion API 将 `publishedAt` 定义成可选的 date-time 字符串，但 Python 标准化器对未提供的 RSS 发布时间保留 `null`。数据库列本来允许 NULL；Fastify 在 API Schema 校验阶段拒绝了该批次，因此事务尚未开始。

修复后的写入契约：

- `publishedAt` 可缺省、为 `null`，或为合法 ISO 8601/RFC 3339 date-time。
- 非法日期时间字符串仍返回 HTTP 400。
- `summary` 同样允许缺省或 `null`：冻结 spool 中有 15 条显式 `null`，原表和 PostgreSQL 列均允许未知摘要。
- TypeBox union 将 `null` 放在字符串之前，避免 Ajv 类型强制把 `null` 转成空字符串。
- 不用采集时间、入库时间或当前时间填充发布时间。`firstSeenAt` 保持独立；列表排序可用它作排序回退，展示字段仍保留 `publishedAt: null`。

## 数据契约审查

- 原版 SQLite `rss_items.published_at` 是可空 TEXT，注释语义为 ISO 发布时间。TrendRadar RSS parser 在缺失或解析失败时返回 `None`；同步标准化后未知发布时间为 `null`。
- JSONL/冻结 spool 不重写 ID、URL、来源或分类。spool 内剩余 1,127 篇包含：合法 `publishedAt` 214、显式 `null` 11、字段缺省 902；非法日期时间字符串 0。
- 同一批剩余数据包含 `summary: null` 15、`summary` 缺省 902。其余必填字段、格式、长度、URI、UUID、来源引用、分类结构和 dedupe key 检查未发现异常；spool 内文章 ID 与 dedupe key 均唯一。
- 对照原版 SQLite 两个活动日期文件只读匹配 RSS URL，共观察到 429 个行匹配（候选可能跨日期重复），其中 409 个存储值为合法 ISO 日期时间、20 个为空、0 个非空格式错误。由于 RSS parser 会在落库前把缺失或解析失败的日期归为 `None`，不能从既有 SQLite/spool 反推上游原始 feed 字符串究竟是空缺还是曾解析失败。
- PostgreSQL `articles.published_at`、`articles.summary` 均为 nullable；Backend API response schema 与 TypeScript `ArticleSummary` 已允许 `publishedAt: string | null`。Web 类型已允许空值，详情保留独立的发布时间/首次采集信息，卡片时间回退使用 `firstSeenAt`。
- 不需要数据库 Migration。

## 修改与回归

- `backend/src/routes/ingestion.ts`：`publishedAt`、`summary` 使用可空 schema；其他格式、长度及必填验证未放宽。
- `backend/src/repositories/ingestion-repository.ts`：输入类型承认 nullable 字段，SQL 仍将未知值存为 NULL。
- `backend/test/ingestion-app.test.ts`：有效日期、null、缺省、混合批次、非法日期 400 且不打开事务。
- `tests/test_intelligence_normalize.py`：发布时间未知时仍为 null，采集时间只写入 `firstSeenAt`。

全量只读 Schema 预检将冻结 spool 剩余 1,127 篇按最多 20 篇切成 57 个真实 Fastify 注入请求；57 个请求均通过 Schema 校验并在认证阶段返回 401，没有写数据库。修复前的 400 验证也已通过路由测试确保不会打开数据库事务。

## 隔离恢复演练

使用最新 PostgreSQL 16 custom-format 备份恢复到隔离数据库：文章 492、分析 3、队列 40、分析尝试 0，Migration 001/002 在备份中。隔离环境运行修复后的实际 Ingestion API 与真实同步函数，未访问生产数据库或 spool：

1. 导入候选 40–59 后，隔离库变为 512 篇文章、60 个队列任务；spool 进度为 60。
2. 在隔离副本中把第 73 条候选时间临时改为非法字符串；API 返回 400，文章/队列计数不变，进度保持 60。
3. 恢复该隔离副本为冻结 spool 原值后，候选 60–79 成功导入 20 篇，进度到 80。
4. 以相同 spool Run ID/offset 重放该批：相同 `runId`、新增 0、更新 20，文章/队列/分析计数不变，无重复队列任务。
5. 后续候选 80–99 正常导入，进度到 100。冻结快照未完成，source/date cursor 始终未推进。
6. 隔离库最后为文章 552、分析 3、队列 100、分析尝试 0；模型调用为 0。隔离容器与网络已清理。生产 spool SHA-256 始终未变。

## 测试与生产边界

- Backend：Vitest 29 passed；`npm run typecheck`、`npm run lint`、`npm run build` 通过。
- Python：相关 pytest 15 passed；Ruff 检查通过。
- 生产未部署新版 Ingestion API，未写生产 PostgreSQL，未触碰生产 spool、cursor、Timer、AI 配置、TrendRadar/MCP 或 Relay。
- 生产恢复点仍为 Run ID `beadd52d-893e-4460-9d0d-96f1e04dbda4`、`nextIndex=60`；本次生产数据库仍为 512 篇文章、60 个队列任务、0 次分析尝试。

结论：此修复已通过隔离端到端断点恢复与幂等重放，可作为生产 `ingest-api` 更新及从 `nextIndex=60` 续传的候选版本。仍须在单独的生产部署授权后更新 Ingestion API；本报告不授权生产续传或 Timer 启用。
