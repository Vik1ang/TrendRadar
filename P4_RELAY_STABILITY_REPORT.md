# TrendRadar P4 Relay 稳定性与 Cloudflare 资源验收

**日期：** 2026-10-22
**分支：** `dev`
**结论：** 本轮短时稳定性、并发关联、离线恢复和免费资源量级检查通过；由于用户要求“到现在就行”，实际 soak 为 **29 分 52 秒**，未达到原定两小时，不能据此关闭长期稳定性验收。

## 1. 当前状态基线 — 已验证

观察时，PostgreSQL、Backend API、Web、Ingestion API、Relay Agent 均为 `running/healthy`；原版 `trendradar` 与 `trendradar-mcp` 容器处于运行状态（未配置 Docker healthcheck）。本轮未重启或修改原版 TrendRadar/MCP、PostgreSQL、Backend API、Web、Ingestion 或共享代理。

- Relay Worker `/health`：HTTP 200，`relay=connected`。
- LAN API `/api/v1/health`：HTTP 200。
- LAN Web `/healthz`：HTTP 200。
- API Token 文件来自 `~/.config/trendradar/api-read-token`，目录权限 `0700`、文件权限 `0600`；Backend API 与 Web 容器运行时配置与该文件一致（仅比较布尔值，不输出值）。Relay 的认证读取请求成功。
- 本轮 API/Web/Agent 日志没有 Bearer/Authorization 字段、Token 字符串或 Bearer 模式命中；跟踪文件中的长 Bearer 模式匹配为 0。未提取或重放旧 Token。

## 2. 稳定性 soak — 部分通过，未达到两小时

探测日志保存在受控配置目录：`~/.config/trendradar/p4-relay-soak-20261022T014414Z.jsonl`（权限 `0600`；只含时间、HTTP 状态、时延、容器状态及内存采样字段，不含请求正文或凭据）。使用每 60 秒一次的公网只读 `GET /api/v1/news?limit=1`，用户要求结束时停止观察。

| 指标 | 实测 |
|---|---:|
| 实际观察时长 | 1,792.3 秒（29 分 52.3 秒） |
| 本地探测请求 | 30 |
| HTTP 200 / 失败 / 超时 | 30 / 0 / 0 |
| 成功率 | 100% |
| P50 / P95 / P99 延迟 | 1,146.7 / 2,423.9 / 2,992.9 ms |
| Agent 状态采样 | 30 次均 `running/healthy` |
| Agent Docker restart count | 0 |
| Agent 心跳超时日志 | 0 |

Agent 在 soak 开始前已有活动连接；soak 区间内没有新的 WebSocket open/registration 日志，表示期间未观察到重连。Cloudflare GraphQL 的窗口数据报告 0 Worker/DO errors。**内存时间序列未能采集**：探测脚本解析 `docker stats` 单位格式错误；soak 后单次读取为 20.16 MiB，但无法计算起始值、变化量或峰值。因此内存趋势为未验证。

后续离线修复见 `scripts/p4_relay_memory.py`：现在可正确解析 `20.16MiB / 755.3GiB` 这类 Docker 输出，3 个离线单测通过。修复后当前 Relay Agent 单次样本为 17.710 MiB；这是不同时间点的单次读数，不能与上述 20.16 MiB 构成可靠趋势，也没有补做长时间 soak。

Cloudflare Adaptive 指标在精确窗口报告 TrendRadar Worker 28 次请求，账户总计 29 次；本地探针记录 30 次 200。该 2 次差异尚未解释（可能是 Analytics 聚合/窗口边界差异），本地 HTTP 状态统计以探针记录为准；Cloudflare 指标不当作逐请求账本。

## 3. 并发与请求关联 — 已验证

本轮使用 5 个并发只读请求：两篇不同文章详情、其中一篇 AI 分析、新闻列表、DeepSeek 搜索。5 个请求均 HTTP 200；响应与 LAN API 的对应响应逐字节一致；详情 ID 对应正确；实际完成顺序与提交顺序不同，未观察到响应串线或重复响应。此前已有的 6 请求乱序测试也通过（本轮没有增加高频压力）。

Relay 的 `RequestCorrelator` 现有单测覆盖乱序 ID 匹配、超时删除、重复响应忽略及断线时批量清理。本轮没有执行真实的在途请求中断注入；客户端取消信号没有被验证为立即传递到 DO，当前等待仍由 20 秒关联超时兜底。故**立即取消与在途断线清理的线上行为未完全验收**，但永久挂起由固定超时与单测覆盖的清理路径所限制。

DO 的应用层限流为单 Durable Object 每分钟最多 30 次 dispatch，且有 32 个在途请求上限。当前测试均未触发 429。限流逻辑的边界单测通过；本轮没有刻意打满线上阈值以免造成业务扰动。正常低频浏览及 5 路并发没有观察到误限流；高突发请求仍可能被拒绝。

## 4. 离线、恢复与错误语义 — 已验证 / 部分未验证

隔离操作仅停止/启动独立 `trendradar-intelligence-relay-agent` 容器：

- Agent 停止时，公网 Relay `/health` 返回 503；认证业务读取返回 HTTP 503、错误码 `RELAY_OFFLINE`。
- 同时 LAN Web `/healthz` 保持 HTTP 200。
- Agent 启动后重新注册，Worker `/health` 回到 HTTP 200、`relay=connected`；认证新闻列表恢复 HTTP 200，JSON 结构有效。
- 未停止 PostgreSQL、Fastify、原版 TrendRadar/MCP 或共享 Xray 代理。

单测验证关联超时会清除 pending 条目，断线 `resolveAll` 会释放所有 pending 请求。本轮没有为制造后端延迟而改动/停止生产 Fastify，也没有在 Agent 处理期间强制断线；这两项真实故障注入未执行。共享代理中断恢复未测试，避免影响其他服务。Agent 重启后旧连接未表现出干扰，但 DO 内部 socket 的清理过程没有单独从 Cloudflare 日志审计。

## 5. Cloudflare 实际用量 — GraphQL 实测；Dashboard 剩余额度未直接读取

通过 Cloudflare GraphQL Analytics API 读取只读指标（Cloudflare 文档说明 Dashboard 图表也来自该 API）。精确 soak 窗口为 `2026-10-22T01:44:18Z` 至 `02:14:10Z`；UTC 日累计截点为 `02:14:10Z`。账户当前运行两个 Worker 脚本，所以账户汇总与 TrendRadar 自身数据分开列示。

| GraphQL 指标 | Soak 窗口 | UTC 当日截至 02:14:10Z |
|---|---:|---:|
| Worker 请求（TrendRadar / 账户） | 28 / 29 | 84 / 86 |
| Worker errors（TrendRadar/账户） | 0 / 0 | 0 / 0 |
| TrendRadar Worker CPU 时间 | 49,853 µs | 135,473 µs |
| DO 请求（TrendRadar namespace；账户） | 129 / 129 | 468 / 468 |
| DO errors | 0 | 0 |
| DO activeTime | 8,749,341 µs | 15,988,971 µs |
| DO cpuTime | 123,222 µs | 319,399 µs |
| DO duration | 1.1199 GB-s | 2.0466 GB-s |
| SQLite rows read / written | 58 / 29 | 132 / 66 |
| DO 出站 WebSocket 消息 | 101 | 387 |
| DO CPU / memory / fatal internal errors | 0 / 0 / 0 | 0 / 0 / 0 |

DO 指标将 hibernatable WebSocket 的入站消息计入 invocation 类指标，而非 periodic 指标；因此 periodic 的 `inboundWebsocketMsgCount=0` 不能解读成 Agent 没有发送心跳。101 个出站消息与应用层 ping/pong 活动相符，但 Agent 本身没有逐次心跳计数日志。

Relay DO 通过 `ctx.acceptWebSocket` 使用 WebSocket Hibernation API。此次约 30 分钟内 DO `activeTime` 合计约 8.75 秒、duration 约 1.12 GB-s，表明观测窗口内没有因长连接持续占用内存而持续计费的迹象。Agent 每 25 秒发送 JSON 应用层心跳，仍会引起 DO 消息事件/唤醒；实际窗口记录 101 个出站 WebSocket 消息。故 Hibernation 有效，但心跳仍产生事件与请求用量，长期是否值得降低频率应结合更长时间的 Dashboard 曲线再决定，不在本轮改协议。

此前 Wrangler 部署校验曾因 CPU 限制配置不适用于当前计划而拒绝部署，行为与 Workers Free 一致；本轮没有在 Dashboard 页面单独确认计划。按 Cloudflare 当前官方文档，Free 计划基准为 Workers 100,000 请求/日、每次 10 ms CPU；SQLite Durable Objects 100,000 请求/日、13,000 GB-s/日，rows read 5,000,000/日、rows written 100,000/日、总存储 5 GB。当前 GraphQL 当日指标远低于这些公开限额；但**Dashboard 的配额剩余计数/账单视图未读取**，也未取得实际 SQLite 存储字节数，因此不声称精确剩余量。官方来源：[Workers 定价](https://developers.cloudflare.com/workers/platform/pricing/)、[Durable Objects 定价与 SQLite 配额](https://developers.cloudflare.com/durable-objects/platform/pricing/)、[DO Metrics/Analytics 数据口径](https://developers.cloudflare.com/durable-objects/observability/metrics-and-analytics/)。

## 6. 验收状态

### 已验证通过

- 当前相关容器运行状态、Relay 在线、LAN/Relay 健康状态、Token 文件权限及日志/Git 凭据扫描。
- 29 分 52 秒低频公网读取：30/30 HTTP 200，无超时；Agent 全程 healthy、零重启。
- 5 路并发、不同文章详情、列表、搜索、分析读取与 LAN 响应一致；完成顺序乱序但响应匹配正确。
- Agent 离线时 503 `RELAY_OFFLINE`，LAN Web 不受影响；重启后 Relay 和业务读取恢复。
- Cloudflare GraphQL 在测试窗口报告 Worker/DO errors 均为 0；Free 配额量级有充足余量（按报告中的当日原始数据和公开配额比较）。
- Cloudflare Worker `npm test`：5/5 通过；`npm run typecheck` 通过。
- Relay Agent `npm run build` 通过；`npm test`：2/2 通过。

### 未验证 / 未通过本轮要求

- **未完成两小时 soak**（按用户指示在当前时点结束），所以 P4 长期稳定性验收不能标记通过。
- Agent RSS 内存变化/峰值未采集成功；只有窗口后单次 20.16 MiB 读数。
- 客户端主动取消、在途请求瞬间断线、代理中断恢复、数小时以上运行及实际 Dashboard 剩余额度/存储字节数未验证。
- GraphQL 精确窗口 Worker 请求数比本地探针少 2 次，尚未解释。
- 没有触发线上 30/min 限流阈值做实时 429 演练；边界只由单测验证。

### 测试失败

- 未发现 Relay 产品功能失败。
- 一次内存采样解析失败（测试工具问题，非 Agent 故障），因此内存趋势项不通过。

## 7. 文件、代码与 Commit

- 本轮归档新增本报告，更新 `docs/IMPLEMENTATION_PLAN.md`、`docs/IOS_HANDOFF.md`，并新增 `scripts/p4_relay_memory.py` 与离线单测 `scripts/test_p4_relay_memory.py`。没有修改 Relay/Worker/Agent、Web、采集、数据库、认证或部署配置。
- 修复的是 P4 诊断脚本的 `docker stats` 单位解析错误，不是 Relay 产品 Bug。离线回归验证见交付记录。
- 仓库原有的 `docker/trendradar-intelligence-unified.override.yml`、`docs/DEPLOYMENT.md`、`scripts/postgres-backup.sh`、`scripts/postgres-restore.sh` 保持未修改、未暂存。

**P4 长期运行结论：条件通过 / 未完成。** 短时稳定性、在线切换、低频并发和资源消耗表现正常；但严格的 2 小时 soak 和几项长时/故障注入项目未完成，不能将 Relay 标记为已满足长期运行要求。
