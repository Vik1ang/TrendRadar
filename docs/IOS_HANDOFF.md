# SwiftUI / iOS 交接（macOS 阶段；本轮未创建工程）

## 当前状态与双 Endpoint

只有一套 Fastify v1 API 和 PostgreSQL。LAN 与 Relay 返回相同业务契约；Web 始终只走 LAN。Relay 已完成 Cloudflare → Durable Object → Relay Agent → Fastify 的真实只读 API 验证。约 30 分钟 soak 和剩余缺口见 `P4_RELAY_STABILITY_REPORT.md`；这不等同于长期稳定性通过。

| 用途 | Endpoint | 状态 |
|---|---|---|
| LAN Direct | `https://<经批准的现有内部 DNS 名称>/api/v1` | 尚未配置域名、DNS-01 证书或 TLS listener；不得当作可用地址。建议使用现有域名下的 split-horizon 名称，在公司 DNS 解析至 `192.168.2.117`。 |
| 外网 Relay | `https://trendradar-intelligence-relay.vik1ang.workers.dev/api/v1` | 已部署并验证；外网设备通过 Worker → Durable Object → Linux Agent → 同一 Fastify。 |

当前 `http://192.168.2.117:8081` 是 LAN Web，不是 iOS API Endpoint。Fastify 宿主机映射仅为 `127.0.0.1:13001`；PostgreSQL 不对 LAN 发布。

## LAN HTTPS 调查结论与最小部署方案

实测现有 Web 容器 Nginx 仅监听容器内 80，`server_name _`；`/api/v1/` 反代至 Docker 内部 `api:3001`，并为现有 Web 读取路径注入共享静态 Bearer。宿主机 Web 绑定为 `192.168.2.117:8081 -> web:80`，Backend API 只绑定 `127.0.0.1:13001`。当前未发现主机/容器 443 listener、项目配置的 API 域名、DNS-01 记录、Let’s Encrypt 证书目录或 `certbot`/`acme.sh` 工具。以上只证明本机仓库和运行环境状态，不证明域名所有权或公司 DNS 权限。

**建议方案，待域名/DNS 所有者批准后实施：**

1. 选用公司已拥有、可由 iPhone 验证的 FQDN，例如 `api-lan.<现有域名>`；不要创建新域名，不使用 `.local` 或内网裸 IP 作为证书名称。
2. 由已有 DNS 服务执行 DNS-01 ACME 验证获取公信 CA 证书。只需写入 ACME TXT 记录，不需把 API 暴露到公网；DNS API 凭据应限制到对应 zone/记录用途并安全保管。没有域名 DNS 控制权时停止，不改 DNS、不申请付费资源。
3. 在现有 Web Nginx 增加独立 `server_name api-lan.<域名>` 的 TLS server block，并将宿主机 `192.168.2.117:443` 映射到该 TLS listener；证书和私钥以只读方式挂载。该 server 只反代 `/api/v1/` 至 `api:3001`，保留请求中的 `Authorization` 供 Fastify 验证，不沿用 Web 专属的静态 Token 注入。只允许可信 LAN 来源访问 443。
4. 保持现有 `192.168.2.117:8081 -> web:80` 绑定、路由和 health check 不变；变更时仅重建/重载 Web 反代容器，不触碰 API、数据库、采集、MCP 或 Relay。
5. 首次部署前先验证证书 SAN/有效链、DNS 内网解析、Nginx 配置及 443 ACL；之后从 iPhone 实测系统信任链和主机名验证。禁止关闭 TLS 校验、永久 ATS 例外或依赖用户安装不受信任的根证书。

当前证书、DNS 控制权和防火墙授权未取得，故上述是实施方案，不是已完成部署。共享 `API_READ_TOKEN` 也不应成为未来 iOS 的凭据；待设备凭据实现后再启用客户端 API。

## 设备级认证方案（仅设计；未替换生产认证）

### 现状

- `backend/src/auth.ts` 使用 `timingSafeEqual` 比较一个共享静态 `API_READ_TOKEN`。Backend `authenticateRead` 保护只读业务路由；健康检查不需要该业务 Bearer。
- Nginx 为现有同源 Web 注入共享 Token，浏览器不保存它。
- Relay Worker 入口当前校验 `CLIENT_API_TOKEN`，并将原始授权头经 Agent 透传；Fastify 做最终校验。
- `RELAY_AGENT_TOKEN` 是独立机器凭据；`INGEST_TOKEN` 用于独立私有导入 listener。二者不与客户端认证混用。

### 最小增量设计

1. 添加 `device_credentials` migration：稳定 `id`、设备标签、`token_hash`（唯一索引）、`created_at`、`expires_at`、`revoked_at`、可选 `last_used_at`。每个 iOS 安装单独签发 256-bit 随机 opaque Bearer（例如 `trd_dev_` 前缀）；只在签发时显示明文一次，PostgreSQL 只保存其 SHA-256 摘要。
2. 仅在受信 Linux 主机提供本地管理 CLI/脚本用于签发、列出元数据、撤销、过期清理；不增加可经公网 Relay 调用的签发/管理接口。丢失设备通过 `revoked_at` 立即拒绝；过期凭据返回 401，重新签发新设备凭据。
3. Fastify `authenticateRead` 增量接受两类凭据：现有静态 `API_READ_TOKEN`（保持 Web 兼容）或数据库中未过期/未撤销的设备凭据。两个 Endpoint 最终都由这段 Backend 逻辑授权；不信任客户端身份/角色请求头。私有 ingestion 仍使用独立 `INGEST_TOKEN`。
4. Relay Worker 保留现有 `CLIENT_API_TOKEN` 兼容路径；后续为设备凭据增加严格长度/字符格式识别，只把格式合规且符合既有只读 allowlist 的原始 Bearer 透传给 Fastify。设备 token 的存在、有效期和撤销状态由 Fastify 最终验证，Worker 不保存设备列表或新闻业务数据；沿用 dispatch/并发限制以约束无效凭据流量。Agent 仍只使用独立 `RELAY_AGENT_TOKEN`。
5. 迁移时先部署 DB migration 和 Backend 双认证，再升级 Worker 的兼容转发，执行单台测试设备签发/过期/撤销测试；确认 Web 静态 Token 与 Relay Bridge 不变后，再在 iOS 使用独立设备 Bearer。失败可回滚为仅旧静态 Token，不需要重置 `API_READ_TOKEN`。

此方案当前**未实现**。iOS 不得内置 `API_READ_TOKEN`、`CLIENT_API_TOKEN`、`RELAY_AGENT_TOKEN`、`INGEST_TOKEN`，也不得复用其他设备 Token。设备凭据保存于 iOS Keychain；首版可以由管理员手动签发，不要求建设 SaaS 登录或刷新服务。

## 网络探测和线路选择

统一 `EndpointPolicy/ReachabilityProbe` + `APIClient`：两条线路只交换 origin，共用 model、分页、错误 decoder 和 Repository；ViewModel 不关心线路。网络变化可以由 `NWPathMonitor` 触发重新探测，但 Wi-Fi 类型、IP 网段或“有网络”不能证明 LAN API 可达。

1. App 启动或网络变化后，对预配置 LAN `GET /api/v1/health` 做约 1 秒短超时探测；有效证书链、匹配的 SAN/hostname、预期 service 标识和 `status=ok` 必须都通过，才优先选 LAN。服务标识只作附加检查，不代替 TLS 身份验证。
2. LAN DNS/TCP 不可达或连接超时时，切到已通过 HTTPS 验证的 Relay；返回公司网络后重新探测并切回 LAN，不要求用户重新登录，也不清空业务状态。
3. LAN 证书链/主机名错误：拒绝 LAN 并记录安全错误，可尝试经过独立 TLS 验证的 Relay；不能降级到明文 HTTP。
4. 401/403 是身份/权限问题，业务 400/404 是请求错误，后端 5xx 表示同一个 Fastify/DB 故障；它们不是 LAN 路由故障，不能盲目切 Relay 重试。Relay 503 表示 Agent/Relay 不可用，504 表示转发超时。两路均不可用后有界重试并显示离线状态，可提供只读缓存。
5. 只读 GET 可在明确网络连接失败时有限重试；超时后的请求可能已在后端完成。未来写请求必须有幂等键、服务器去重和未知提交状态处理，不能无条件把超时 POST 发送到另一路。

## 两路共用的 API/OpenAPI 契约

契约只维护在 `docs/API_DESIGN.md` 与 Fastify OpenAPI；客户端不得建立另一套业务 API。接口需以部署 Fastify `/documentation/json` 验证：

- `GET /api/v1/health`：不需要业务 Bearer 的 Fastify/DB readiness 与稳定服务标识；Worker `/health` 是 Relay socket 状态，不是该 API health。
- `GET /api/v1/news`、`GET /api/v1/search`：服务端搜索、频道/来源/日期筛选及 `limit/offset` 分页。
- `GET /api/v1/news/{uuid}`、`GET /api/v1/news/{uuid}/analysis`：只读已采集新闻及预先生成分析；不按需调用模型。
- `GET /api/v1/categories`、`GET /api/v1/sources`、`GET /api/v1/status`。
- 使用 ISO 8601 UTC 时间、稳定文章 UUID、`data` + `pagination{limit,offset,total,hasMore}`、统一 `error{code,message}` + `requestId`。分析状态包括 `pending|processing|completed|failed|not_requested`。
- Relay 只读 GET allowlist 不含 ingestion、数据库、管理或模型密钥接口。Cloudflare 不存储新闻业务数据。

## iOS 局域网权限与真机验收

SwiftUI 工程需提供 `NSLocalNetworkUsageDescription`。用户拒绝局域网权限时，只要外网可用，Relay 仍应正常；不要加入 ATS 全局明文例外。

真机验收至少包括：可信 LAN DNS + 有效 TLS 直连；LAN DNS/TCP 超时转 Relay；不受信任/主机名错误证书被拒；401/403 不触发网络切换循环；Relay 503/504 与 Backend/DB 5xx 有区分；公司内外切换和回切；拒绝 LAN 权限时 Relay 可用；列表/搜索/分页/详情/分析在两路数据一致；设备凭据独立签发、Keychain 保存、过期与撤销；无网络缓存；任何未来写请求幂等。

上述全部 iOS 真机项目仍待 macOS/Xcode 阶段执行。本轮没有创建 SwiftUI 工程。
