# Cloudflare Relay 与 LAN Direct（P4）

## 范围与状态

目标：未来 iOS 在公司内优先使用受信任的 LAN HTTPS API；外网通过 Worker → SQLite Durable Object → 主动连接的 Relay Agent → 固定 Fastify API。两路共享 Fastify/PostgreSQL/API 契约。Web 始终 LAN 直连；Cloudflare 不存储业务数据；Agent 不是通用隧道。

P4-0 草稿审查及本地修补已完成；**P4 真实联通未验收**。当前没有 Cloudflare Wrangler 登录授权（`wrangler whoami` 返回未认证），也没有可从 Docker 网络访问的获批代理；当前容器无法连接宿主机 gateway 上检测到的代理。不能部署 Worker、启动 relay profile 或声称外网请求通过。LAN HTTPS 也未部署：没有确认可用域名、DNS-01 权限、证书及 API TLS reverse proxy。

### 2026-10-21 环境诊断（只读；未部署/未改网络）

- Wrangler `4.151.2`；`npx wrangler whoami` 返回 `You are not authenticated`。因此未检查账户/Workers 列表/计划，无法确认账户当前计划或 `trendradar-intelligence-relay` 名称是否空闲。
- Linux 宿主机环境配置 HTTP proxy 指向 loopback `10801`；只读 socket 检查发现 Xray 运行于宿主机用户下，`127.0.0.1:10800` 和 wildcard `*:10801` listener。没有读取 Xray 配置文件或凭据。宿主机经该代理访问 `https://www.cloudflare.com/cdn-cgi/trace` 返回 HTTP 200，CONNECT 状态 200。
- 使用现有 `trendradar-lan-acceptance_intelligence-edge` 网络（`192.168.144.0/20`，gateway `192.168.144.1`）的临时 curl 容器，从 `host.docker.internal`、gateway 及宿主机 LAN 地址访问 proxy listener 都在连接阶段 timeout；外部 API 容器间访问未受影响。当前观察到的症状符合宿主机防火墙/DOCKER-USER 入站过滤或 Xray 入站 ACL 拒绝 Docker bridge 来源，但无法定因：当前 shell 非 root，不能读取完整 nft/iptables/UFW 状态。未改 firewall、Xray、网络策略，也未用 host network。
- 同一宿主机通过配置代理连接 `wss://ws.postman-echo.com/raw`，WebSocket Upgrade 成功；证明此代理路线支持 CONNECT + WSS 握手，不代表 Docker 内可达或 TrendRadar Cloudflare Worker 的真实 WSS 已通过。公司网络对该 Relay 用途的正式授权无法从主机配置推断，仍需网络所有者确认。
- Relay Agent/TrendRadar/MCP 服务均未启动/重启；Worker 未部署。LAN Web、API、ingest、PostgreSQL 仍 healthy。

当前最小解除方案（需账户/网络管理员授权，未执行）：先用账户所有者在 Dashboard 确认 Worker 名称未被占用并确认 Workers Free；建议所有者在 UI 创建独立 Worker 后，只对该 Worker 授予 API Token `Editor`，避免把 Workers Admin 账号级权限交给部署流程。若需由 CLI 创建 Worker，Cloudflare 官方要求 product-level Workers `Admin`；这比本项目部署所需范围更广，不应长期保留。网络侧优先给 Relay Agent 一个独立、仅它可达的代理容器/受控 Docker ingress；若复用宿主 Xray，则由管理员先审查并设置仅固定 Relay Agent 来源 → proxy 端口的防火墙 allowlist，且确认 wildcard listener 的 LAN 访问边界。不可以直接开放给整个 LAN、改 host network 或绕过策略。

`/home/zwq/.orca-relay` 检查结果仅为本机 shell 启动/状态工具，不是源码 Git 仓库，没有 Worker/DO/Bridge Agent 协议可复用。故本实现借鉴主动反向连接的架构思路，不复用或修改 Orca Relay。

## 模块与协议

- `cloudflare/worker/`：HTTPS 入口，独立 `CLIENT_API_TOKEN`、GET 只读 allowlist、query allowlist、最大 URL 2048 字符；`/agent` 仅 Agent WSS 注册，`/health` 报告 relay socket 状态（不是 Fastify health）。Worker 不访问数据库。
- SQLite DO `RelayHub`：Agent 注册/ready、request ID、32 并发、20 秒 timeout、最大响应 1 MB；会话被新 socket 替代时旧 socket close 不得清除新会话请求。DO 使用单一 SQLite 窗口计数限制每分钟最多 30 个通过认证的 API dispatch；持续达到上限理论约 43,200 次 DO 存储写入/日，低于 Free 100k writes/day 但仍需 Dashboard 监控。DO 只允许固定 API path，不接受 Host/URL。
- `relay-agent/`：主动 WSS、独立 `RELAY_AGENT_TOKEN`、支持显式 HTTP(S)/SOCKS 代理、固定 `LOCAL_API_ORIGIN`、仅 GET 白名单；注册 10 秒超时、25 秒 ping / 60 秒 pong watchdog、断线指数退避（最高 60 秒，连接稳定 30 秒才重置），把用户 Bearer 原样转发至 Fastify，拒绝重定向以防 Authorization 泄漏，响应最大 1 MB。容器只接入必要的 intelligence-edge 网络，不连 DB/ingest 网络。
- 协议：Worker 认证客户端后发送 `{method:'GET',path,authorization}`；DO 生成 UUID 后发送给注册 Agent；Agent 向固定 Fastify 发请求并返回 `{type:'response',id,status,contentType,body(base64)}`。请求 ID 独立关联并设超时。
- 不允许任意 host、端口、任意 path、非 GET、私有 ingestion、管理接口、数据库、命令执行或模型配置接口；Agent `fetch` 禁止 redirect。重复 query key 拒绝，避免不同解析器语义不一致。

## 用户认证与信任边界

当前 Backend Bearer 为静态 `API_READ_TOKEN`。如启用 Worker，必须把同一个值设置为 Cloudflare Worker secret `CLIENT_API_TOKEN`；这只是同一用户凭据在边缘先验一次，Fastify 仍最终校验原始 `Authorization`。Agent 只使用独立 `RELAY_AGENT_TOKEN` 做机器身份，不再注入 API 用户 Token；客户端不持有 Agent Token。不得信任 `X-User`/角色等客户端声明头。

重要缺口：现有单用户静态 Bearer 没有过期、独立设备撤销或 Refresh Token。密钥轮换能全局撤销旧值，但不满足设备级撤销。故当前仅形成兼容的第一版通道契约，iOS 正式上线前必须补短期/可撤销凭据或批准明确的周期性全局轮换机制。不要把此差距描述为已解决。

Fastify `/api/v1/health` 返回 `{status,database,service:'trendradar-intelligence'}`。LAN 自动选择需要同时验证预配置 HTTPS host 的有效 TLS 身份和此服务标识；service 字段单独可伪造，不能替代 TLS。Relay 端也固定到用户配置的 Worker HTTPS host 并验证相同服务标识。不得关闭证书校验。

## LAN 和 Relay Endpoint 约定

- LAN：`https://<已有内部域名>/api/v1`，需要有效公信/受信任证书（建议 DNS-01）和内网 DNS/路由。实际域名尚未提供或配置。`http://192.168.2.117:8081` 是 Web，不是长期移动端认证 API。现有 API listener/服务未因本任务改绑/重启。
- Relay：`https://<独立Worker子域或workers.dev主机>/api/v1`；未部署，域名/Worker URL 未确认。
- 两个入口调用相同 Fastify GET API、Bearer 令牌、路径/字段/分页/业务错误。不创建 iOS 专用后端或 D1 数据副本。

## 本地配置与安全部署步骤（未执行）

1. 账户持有人先授权/登录自己的 Cloudflare 账户，核对 Workers Free / SQLite DO Free 当前额度；不升级计划。
2. 使用密码管理器生成客户端/Backend Bearer 与 Agent token。Backend `API_READ_TOKEN` 与 Worker `CLIENT_API_TOKEN` 完全同值；`RELAY_AGENT_TOKEN` 单独同值配置给 Worker 和 Agent。不要提交 `.env`、`.dev.vars` 或真实凭据。
3. 将获准代理配置为容器可访问的 `RELAY_PROXY_URL`；不得因代理不可达改用 host networking、关闭防火墙或绕过公司网络策略。要先在实际 Docker 网络内验证代理 HTTPS CONNECT，再验证 WSS。
4. `wrangler deploy` 仅使用 TrendRadar 独立 Worker 名称、SQLite DO migration 和上述独立 Secrets；不接触 Orca 配置。
5. 仅在上述凭据、网络授权和 TLS 准备好后，显式启动 `--profile relay`。保持 TrendRadar/MCP 与正在运行的 LAN stack 不重启；先记录 compose config、网络、端口及 healthy 服务。

## 重试、错误及切换约定

未来 iOS 先以约 1 秒短超时请求配置好的 LAN HTTPS `/health`；要求 TLS 校验通过、`service` 匹配且状态 OK 才选 LAN。LAN DNS/TCP/连接超时可退至 Relay；LAN TLS 错误须记录并拒绝该线路；401/403、业务 4xx 和后端 5xx 不是线路故障，不盲目切换；回到公司后网络变化触发重新探测，成功后转回 LAN。Relay 离线 503、Relay 请求超时 504；两路失败后停止重试并允许缓存。只读 GET 可在明确网络故障时有限重试；写请求未来需幂等键/去重，不能无条件转发重试。

## Cloudflare 免费额度与风险

官方当前说明：Workers Free 为每日 100,000 请求、每次 CPU 10ms；DO Free 限 SQLite 后端、100,000 DO 请求/日、13,000 GB-s/日、5M SQLite 读取/日、100k 写入/日、5GB 存储；超限维度可能失败而非自动升级。实际部署日重新核实计划/Dashboard，禁止擅自升级。[Workers 定价](https://developers.cloudflare.com/workers/platform/pricing/)，[Durable Objects 定价](https://developers.cloudflare.com/durable-objects/platform/pricing/)。

DO 已使用 Hibernation WebSocket API。Hibernation 时内存会丢失，连接会从 socket attachment 重建；应避免长时间 pending promise 和普通 timer。现有 25 秒 ping/pong 会唤醒 DO 处理应用消息，真实部署后应比较 `setWebSocketAutoResponse` 与当前心跳实现及用量，不能假设长期连接零成本。[Hibernation 官方说明](https://developers.cloudflare.com/durable-objects/best-practices/websockets/)。当前没有实际 Dashboard 用量、小时级稳定运行或代理中断恢复证据。

## 已有验证和待完成验收

已审查 Worker/DO/Agent/Compose/allowlist/现有测试；本地改动后应执行 Worker typecheck/test/deploy dry-run、Agent build/test、Backend test/typecheck、Compose config 与现有服务健康检查。`wrangler deploy --dry-run` 只验证构建，不代表部署或外网 WSS。

真实 P4 尚需凭据/授权后验证：WSS 注册/无效 Agent token、持续连接/断线重连/容器重启、外网真实 `/api/v1/health/news/search/detail/analysis`、并发乱序关联/timeout/离线错误、非法路径/method/query、LAN/Relay 同一文章和搜索结果一致性、Docker 代理断连恢复、数小时运行、Dashboard 免费额度、LAN HTTPS DNS/TLS 和 iOS 两路探测模拟。当前 P4 不通过，不可开始 iOS 实现。
