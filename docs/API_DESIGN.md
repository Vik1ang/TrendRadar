# Intelligence Center API 设计

版本前缀 `/api/v1`。Fastify 是唯一业务 API；Web 和未来 iOS 使用同一契约。响应时间字段均为 ISO 8601 UTC 字符串。OpenAPI 从已实现的 Fastify schema 生成/校验，不能把未实现接口写成现状。

## 认证和错误

- `/api/v1/health` 返回 `200`（API 可用、DB ready）或 `503`（DB 不可用），字段包含稳定 `service: "trendradar-intelligence"` 服务身份，不返回连接串/密钥。客户端须同时验证预配置 TLS 主机身份和该预期服务标识；该标识不能单独作为身份凭据。
- 其他只读业务路由使用 Bearer `API_READ_TOKEN`；同源 Web 由反向代理安全注入/转发，浏览器配置不写入长期秘密。启用 Relay 时 Worker `CLIENT_API_TOKEN` 必须与后端 `API_READ_TOKEN` 同值，Agent 转发客户端原始 Authorization，后端再次校验，不信任客户端自报角色/身份头。Agent 另持有独立机器级 `RELAY_AGENT_TOKEN`。现有用户令牌为单用户静态凭据，暂不支持到期/设备级撤销；发布 iOS 前必须补足凭据生命周期或明确安全轮换方案。单用户 LAN 不是公开认证边界。
- `POST /api/v1/internal/ingestion` 由独立 `ingest-api:3002` listener 提供，只加入 Compose internal network，不发布主机端口；要求独立 `INGEST_TOKEN`。它不注册在公开 Fastify API listener，也不能进入 Cloudflare Relay 路径表。仅 TrendRadar 可信容器调用。
- 错误结构：`{"error":{"code":"VALIDATION_ERROR","message":"...","details":[]},"requestId":"..."}`。稳定码包含 `UNAUTHORIZED`、`NOT_FOUND`、`VALIDATION_ERROR`、`DATABASE_UNAVAILABLE`、`INTERNAL_ERROR`；不暴露堆栈/SQL。

## 分页

统一 query `limit`（默认 30，最大 100）与 `offset`（默认 0）；列表返回 `{"data":[],"pagination":{"limit":30,"offset":0,"total":0,"hasMore":false}}`。按 `coalesce(published_at,first_seen_at) desc,id` 稳定排序；有原文发布时间时按该时间排序/过滤，未提供发布时间的热榜使用首次采集时间，避免日期筛选把热榜新闻全部排除。返回 `total` 为同一筛选条件下的总数；后续若换游标分页须作为契约变更。

## 资源

| 路由 | 功能 |
|---|---|
| `GET /api/v1/health` | API/数据库 readiness |
| `GET /api/v1/status` | 受 Bearer 保护的近期文章总数、最近更新时间、最近 ingestion run 与分析计数 |
| `GET /api/v1/news` | 最新新闻；支持 `category`、`source`、`from`、`to`、`q`、分页；按 `coalesce(published_at,first_seen_at) desc, id` 稳定排序 |
| `GET /api/v1/news/{id}` | 文章元数据、来源、分类、最新分析状态；不存在返回 404 |
| `GET /api/v1/news/{id}/analysis` | 最新成功分析及分析状态/版本；未完成时返回明确 `pending/processing/failed/not_requested`，不生成按需推理 |
| `GET /api/v1/categories` | 分类 ID、名称、顺序 |
| `GET /api/v1/sources` | 来源 ID、名称、类型 |
| `GET /api/v1/search` | 服务端 PostgreSQL 标题、来源摘要及已完成 AI 摘要搜索；条件与分页参数同 news，支持中文子串匹配 |
| `POST /api/v1/internal/ingestion`（仅 `ingest-api:3002`） | 幂等批次导入 sources/articles/categories/analysis/run；要求独立 Bearer token，响应 run ID 及 inserted/updated 计数；只对内部网络可达 |
| `POST /api/v1/internal/analysis-candidates`（仅 `ingest-api:3002`） | 为给定文章 ID 集合筛出缺少指定 model/prompt/analysis 版本结果的文章；短暂失败有重试抑制窗口；只对内部网络可达 |

文章对象包含 `id,title,source,publishedAt,firstSeenAt,url,summary,analysisSummary,contentStatus,categories,analysisStatus`。`summary` 是原 RSS 来源摘要；`analysisSummary` 是最近一次已完成 AI 分析的摘要，非 `completed` 时为 `null`。分析资源包含 `status,errorCode,summary,keyFacts,background,potentialImpacts,uncertainties,evidenceScope,modelName,promptVersion,analysisVersion,analyzedAt`；`errorCode` 只在分析失败时提供稳定的机器可读错误码。结构化列表字段始终为数组，空缺内容为 `null`/空数组并保持契约一致。

## 兼容和权限

新增可选响应字段为向后兼容；改名/改类型/删除字段须新版本。客户端忽略未知字段。Relay 将来仅允许 GET 上述新闻/分类/来源/搜索/health 路由，拒绝任意路径、写方法、`internal` 前缀和任意目标主机。Web 不经 Relay；PostgreSQL 永不 publish 到公网。
