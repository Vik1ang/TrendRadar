# Cloudflare Relay 与 LAN Direct（P4）

## 范围与状态

目标：未来 iOS 在公司内优先使用受信任的 LAN HTTPS API；外网通过 Worker → SQLite Durable Object → 主动连接的 Relay Agent → 固定 Fastify API。两路共享 Fastify/PostgreSQL/API 契约。Web 始终 LAN 直连；Cloudflare 不存储业务数据；Agent 不是通用隧道。

**P4 Relay MVP 已真实部署并端到端验证；归档日期 2026-10-22。** Worker `trendradar-intelligence-relay`：`https://trendradar-intelligence-relay.vik1ang.workers.dev`；首次代码部署版本 `796cb5b3-3a69-48f0-8785-640eeca9a522`，配置 Secrets 后版本 `5a9f8ea2-c9c8-44aa-8cfa-c9e4ca09d7ee`。SQLite DO `RelayHub` 已注册 Agent。Docker Relay Agent 经 `http://192.168.1.90:10801` 建立 WSS。LAN 与 Relay 的健康、新闻列表分页、DeepSeek 搜索、详情和已完成分析响应一致；未认证 API/错误 Bridge、非法路径/写方法/私有 ingestion 被拒绝。Agent 离线时 API 返回 503，LAN Web 仍为 200，Agent 重启后恢复。2026-10-22 低频公网 soak 实测 29 分 52 秒、30/30 请求成功，Worker/DO GraphQL 窗口 errors 为 0；**Relay MVP 功能验收通过，但长期稳定性验收未完成**（未达到两小时）。Cloudflare 窗口与当日实际统计及免费额度对照见 `P4_RELAY_STABILITY_REPORT.md`；Dashboard 剩余额度和 SQLite 实际存储字节数未取得。代理中断恢复、在线取消/在途断线注入、内存长期趋势、LAN HTTPS 和 iOS 真机双线路测试仍待完成。

仅 Worker Secrets 保存 `CLIENT_API_TOKEN` 与 `RELAY_AGENT_TOKEN`；值未写入仓库。Agent runtime env 位于仓库外 `/home/zwq/.config/trendradar/relay-agent.runtime.env`（权限 0600）。本次未触碰 Orca Relay、原始 TrendRadar/MCP、PostgreSQL、Backend API 或 LAN Web 容器。

**凭据事件状态：**先前诊断输出曾暴露 API Bearer；后续已完成 API Token 轮换，并验证 LAN 与 Relay 读取恢复、旧凭据失效。真实值不记录于文档。当前设备级独立凭据尚未实现，见下文认证缺口和 `docs/IOS_HANDOFF.md`。

### 先前环境诊断（部署前历史状态；新的代理/认证验证已取代阻塞结论）

- Wrangler `4.151.2`；`npx wrangler whoami` 返回 `You are not authenticated`。因此未检查账户/Workers 列表/计划，无法确认账户当前计划或 `trendradar-intelligence-relay` 名称是否空闲。
- Linux 宿主机环境配置 HTTP proxy 指向 loopback `10801`；只读 socket 检查发现 Xray 运行于宿主机用户下，`127.0.0.1:10800` 和 wildcard `*:10801` listener。没有读取 Xray 配置文件或凭据。宿主机经该代理访问 `https://www.cloudflare.com/cdn-cgi/trace` 返回 HTTP 200，CONNECT 状态 200。
- 使用现有 `trendradar-lan-acceptance_intelligence-edge` 网络（`192.168.144.0/20`，gateway `192.168.144.1`）的临时 curl 容器，从 `host.docker.internal`、gateway 及宿主机 LAN 地址访问 proxy listener 都在连接阶段 timeout；外部 API 容器间访问未受影响。当前观察到的症状符合宿主机防火墙/DOCKER-USER 入站过滤或 Xray 入站 ACL 拒绝 Docker bridge 来源，但无法定因：当前 shell 非 root，不能读取完整 nft/iptables/UFW 状态。未改 firewall、Xray、网络策略，也未用 host network。
- 同一宿主机通过配置代理连接 `wss://ws.postman-echo.com/raw`，WebSocket Upgrade 成功；证明此代理路线支持 CONNECT + WSS 握手，不代表 Docker 内可达或 TrendRadar Cloudflare Worker 的真实 WSS 已通过。公司网络对该 Relay 用途的正式授权无法从主机配置推断，仍需网络所有者确认。
- Relay Agent/TrendRadar/MCP 服务均未启动/重启；Worker 未部署。LAN Web、API、ingest、PostgreSQL 仍 healthy。

以上是部署前的解除建议，现已完成账户授权、空 Worker 名称核验及代理接通；没有修改 Xray、防火墙或公司网络策略。

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
- Relay：`https://trendradar-intelligence-relay.vik1ang.workers.dev/api/v1`；已真实部署。
- 两个入口调用相同 Fastify GET API、Bearer 令牌、路径/字段/分页/业务错误。不创建 iOS 专用后端或 D1 数据副本。

## 已执行的安全部署步骤

1. 使用已授权的 Wrangler 会话部署独立 Worker 名称；没有触碰 Orca Worker。部署错误确认当前 Workers Free 不支持显式配置 `limits.cpu_ms`，移除该配置后部署成功，没有升级计划。
2. 使用密码管理器生成客户端/Backend Bearer 与 Agent token。Backend `API_READ_TOKEN` 与 Worker `CLIENT_API_TOKEN` 完全同值；`RELAY_AGENT_TOKEN` 单独同值配置给 Worker 和 Agent。不要提交 `.env`、`.dev.vars` 或真实凭据。
3. 指定代理 `http://192.168.1.90:10801` 在实际 Docker network 完成 TCP、CONNECT、公共 WSS 和 Worker WSS 握手；Relay Agent 使用 `HttpsProxyAgent`，没有发布宿主机端口。
4. SQLite DO migration `v1` 已实际应用；Worker Secrets 仅为 `CLIENT_API_TOKEN` 和机器级 `RELAY_AGENT_TOKEN`，值未提交到 Git。
5. Agent 容器单独启动并使用 `unless-stopped`；未重启 Backend API、Web、PostgreSQL、TrendRadar 或 MCP。

## 重试、错误及切换约定

未来 iOS 先以约 1 秒短超时请求配置好的 LAN HTTPS `/health`；要求 TLS 校验通过、`service` 匹配且状态 OK 才选 LAN。LAN DNS/TCP/连接超时可退至 Relay；LAN TLS 错误须记录并拒绝该线路；401/403、业务 4xx 和后端 5xx 不是线路故障，不盲目切换；回到公司后网络变化触发重新探测，成功后转回 LAN。Relay 离线 503、Relay 请求超时 504；两路失败后停止重试并允许缓存。只读 GET 可在明确网络故障时有限重试；写请求未来需幂等键/去重，不能无条件转发重试。

## Cloudflare 免费额度与风险

官方当前说明：Workers Free 为每日 100,000 请求、每次 CPU 10ms；DO Free 限 SQLite 后端、100,000 DO 请求/日、13,000 GB-s/日、5M SQLite 读取/日、100k 写入/日、5GB 存储；超限维度可能失败而非自动升级。实际部署日重新核实计划/Dashboard，禁止擅自升级。[Workers 定价](https://developers.cloudflare.com/workers/platform/pricing/)，[Durable Objects 定价](https://developers.cloudflare.com/durable-objects/platform/pricing/)。

DO 已使用 Hibernation WebSocket API。Hibernation 时内存会丢失，连接会从 socket attachment 重建；应避免长时间 pending promise 和普通 timer。现有 25 秒 ping/pong 会唤醒 DO 处理应用消息，不能假设长期连接零成本。[Hibernation 官方说明](https://developers.cloudflare.com/durable-objects/best-practices/websockets/)。当前没有实际 Dashboard 用量、小时级稳定运行或代理中断恢复证据。

## 已有验证和待完成验收

Worker test/typecheck/deploy dry-run、Agent build/test 均通过；并已实际部署、建立 WSS/注册、请求真实 Fastify API 并对照 LAN/Relay 响应。`wrangler deploy --dry-run` 本身只验证构建，不作为外网验收证据。

剩余验收：两小时或更长持续观察；线上客户端取消和在途请求断线注入；共享代理中断恢复；Agent 内存长期趋势；Dashboard 剩余额度/SQLite 存储字节数复核；LAN HTTPS/DNS-01；iOS 双线路模拟与真机测试。P4 Relay MVP 功能已归档通过，但长期稳定性、LAN HTTPS 和 P4/P5 全量交付未完成，本轮没有开发 iOS。
