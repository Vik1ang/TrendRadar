# SwiftUI / iOS 交接（待 macOS 阶段）

本轮 Linux 阶段不创建 SwiftUI/Xcode 工程。iOS 必须访问同一 Fastify v1 API 与 PostgreSQL；Web 不经过 Cloudflare。Relay 与 LAN 都不是独立业务后端。

## 双 Endpoint

- LAN Direct：`https://<已有内部域名>/api/v1`。域名、内网 DNS、TLS reverse proxy、有效证书和 DNS-01 权限尚未提供/部署。不可把 `http://192.168.2.117:8081`（Web 静态服务）或明文 IP API 用作生产认证 Endpoint。
- 外网 Relay：`https://<TrendRadar 专属 Worker host>/api/v1`。Worker 尚未部署，主机名待配置。
- 两路同一 Fastify API、资源路径、分页、响应与错误结构；不连 Postgres，不建 D1，也不调用模型。

## 认证与服务身份

当前 Backend 只支持静态单用户 Bearer `API_READ_TOKEN`。Relay Worker 在入口先检查 `CLIENT_API_TOKEN`，其值必须与 Backend 同一个令牌；Agent 用独立 `RELAY_AGENT_TOKEN` 做机器认证，并透传客户端原始 `Authorization`，Fastify 再次进行最终校验。客户端不持有机器 token，不信任自报用户身份/角色头。LAN 直接发送相同 Bearer 到 Fastify。

静态令牌轮换可全局撤销旧 token，但当前无 expiry、per-device revoke 或 refresh token。此为 iOS 正式上线前的未满足安全要求；须实现短期/可撤销凭据，或经安全审查明确全局轮换与泄露响应操作。本轮未实现完整登录/凭据系统。

探测 `/api/v1/health`：预期 JSON 有 `service: "trendradar-intelligence"`、`status`、`database`。必须同时验证固定 Endpoint 的有效 TLS 主机身份与 service 标识；标识本身不能证明身份。禁止关闭 TLS 校验或配置永久 ATS 全局例外。

## 推荐 SwiftUI API Client 分层（未来实现）

`EndpointPolicy/ReachabilityProbe` 负责线路状态和短超时探测；`APIClient` 负责统一 URLSession、Bearer、错误解码、分页和一次性 GET 重试；Feature ViewModel 只处理产品状态；Keychain 存用户凭据；可选本地 cache 仅用于只读离线展示。底层 Endpoint 仅 origin 不同，API models 和 repository 不能复制成 LAN/Relay 两套。

用 `NWPathMonitor` 触发重新探测，但绝不能用 Wi-Fi 类型/IP 网段作为可达证据。建议初始 LAN 探测 deadline ~1 秒，需真机测量后调整：

1. App 启动/网络变化时检查网络可用。
2. 请求预配置 LAN HTTPS `/health`，限制约 1 秒；TLS 校验、服务身份、`status=ok` 全通过才选择 LAN。
3. DNS/TCP 不可达或连接超时则选 Relay；LAN TLS 身份失败拒绝 LAN 并记录安全错误。
4. LAN 401/403 是认证错误，4xx 是业务错误，5xx 是同一后端异常，不应当作线路故障盲切 Relay。
5. 外网 Relay 503 表示 Agent/Relay 不可用；504 表示 Relay timeout。两端都失败后有限重试并显示离线/缓存。
6. 网络变化后重新探测；回公司且 LAN health 通过时切回 LAN。切换只改变 endpoint，不清空/重建业务状态或重新登录。

只读 GET 可在明确 DNS/TCP/连接超时、且不会无边界重试时切换到另一线路；响应丢失后的 GET 重试仍要考虑服务端状态变化。未来写 API 必须使用幂等键、后端去重和不确定提交状态处理；不可无条件把 timeout POST 再发送一次。

## 当前 API 契约

- `GET /api/v1/health`：未认证 readiness/service id；200 后端与 DB ready，503 DB unavailable。
- `GET /api/v1/news?q=人工智能&category=ai&limit=30&offset=0`；同一筛选语义支持 `source/from/to`，使用服务端分页。
- `GET /api/v1/search`：同条件服务端搜索。
- `GET /api/v1/news/{uuid}` 与 `GET /api/v1/news/{uuid}/analysis`：分析只读已经生成结果，不按需触发 AI。
- `GET /api/v1/categories`、`GET /api/v1/sources`、`GET /api/v1/status`。
- 新闻响应 `data` + `pagination{limit,offset,total,hasMore}`；时间 ISO 8601 UTC。详情分析状态可为 `pending|processing|completed|failed|not_requested`；仅 `completed` 显示 `summary/keyFacts/background/potentialImpacts/uncertainties/evidenceScope/modelName/promptVersion/analysisVersion/analyzedAt`。
- 统一错误 envelope `error{code,message}` 和 `requestId`；401/403 不循环重试，503/504 有界退避。

## 局域网权限与真机验收

SwiftUI 工程需提供 iOS Local Network 使用说明（`NSLocalNetworkUsageDescription`）；用户拒绝时保留 Relay 线路。只添加必要的用途描述，不设置广泛 ATS 明文例外。

真机至少验收：LAN有效证书/成功、LAN证书不受信任时拒绝、LAN 超时转 Relay、Relay offline/timeout、无效 Bearer 不错误切换、DB/backend 500、公司内外切换与回切、用户拒绝 LAN 权限后 Relay 可用、并发分页/search 业务结果一致、分析 `pending/failed`、无网 cache、凭据轮换/撤销、写操作幂等。当前这些均未在 iOS 真机验证。

契约详见 `API_DESIGN.md`；Relay 配置/状态详见 `RELAY.md`。OpenAPI 最终以部署 Fastify `/documentation/json` 为准。
