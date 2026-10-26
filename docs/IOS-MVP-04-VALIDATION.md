# IOS-MVP-04 验收记录

日期：2026-10-26

iOS 工作树基线：`dev` / `8e8f4de`；Linux Staging 基线：`staging/ios-auth-01` / `c009b8d`

## 结论

- **04A：已完成可在当前授权范围内执行的验证。** 模拟器完成 Staging HTTPS 配对、Keychain 重启读取、LAN 新闻列表/详情、真实 403 UI、Cloudflare Staging Relay 读取，以及 Agent 恢复读取。测试结束后测试设备已撤销、本机 Keychain 已清除、Staging Agent 已停止。
- **04B：未通过／未执行。** 尚无可由实体 iPhone 正常信任的 LAN FQDN 证书，也未配置 DNS-01 和 split-horizon DNS。Xcode 有 Apple Development identity，但工程未配置 `DEVELOPMENT_TEAM`。因此没有向实体手机安装 App、没有配对、没有切换 Wi-Fi/蜂窝网络，也没有声称实体网络切换通过。
- 未修改正式 DNS、正式 Gateway、生产 Worker、生产数据库、V1/V2 契约、P2 或 AI 设置。Staging Agent 已按需短时启动并正常退出；Cloudflare Worker / DO 保持原配置。

## 可信 LAN HTTPS 调查与方案

当前 Staging API 入口是 `https://192.168.2.117:18443`。Gateway 证书由 `TrendRadar IOS Auth Staging Root CA` 签发，主体是 IP `192.168.2.117`，SAN 只覆盖该 IP 和内部别名 `ios-auth-staging-gateway`；它不是公信链。Simulator 的 Staging 配置可使用已准备的信任环境，但该证书不能作为实体 iPhone 公信 HTTPS 验收证书。现有 Staging Gateway 仍只绑定内网 `192.168.2.117:18443`；Fastify API 是 loopback，数据库及 Ingestion 端口未发布。

推荐的**待批准**方案：

1. 由域名所有者确认一个既有域名及 DNS 服务；使用专用名称 `ios-api-staging.<既有域名>`，不购买新域名、不猜测实际域名。
2. 只在公司/家庭内网 DNS 配置 split-horizon A 记录指向 `192.168.2.117`。公网无需开放 API 入站端口。
3. 用最小权限 DNS API 凭据完成 ACME DNS-01，签发 SAN 与该 FQDN 完全匹配的公信证书；自动续期并限制私钥权限。
4. 在**隔离 Staging** Gateway 保留独立 API TLS 虚拟主机，仅代理只读 V1 和 LAN 配对 V2 路由至 Fastify；保留 Authorization 供 Fastify 验证。不复用 Web 的静态共享 Token 注入路径。
5. 先通过配置检查、证书链/主机名校验、LAN 解析和防火墙审查；只有用户单独批准后才修改 DNS、签发真实证书或更换 Gateway 证书。

当前没有证明域名控制权或 DNS API 权限；本轮未新增 DNS 记录、未签发公信证书、未替换 Gateway 证书，也未开放 WAN 入站。LAN 路径在实体设备上仍阻塞。

## XCTest 配对秘密保护

旧式通过 `typeText` 注入配对码的做法已移除。App 本身没有测试配对码注入入口；测试只从权限为 `0600` 的本机临时文件读取一次性码，在 Simulator 的 SecureField 中通过短暂剪贴板粘贴触发真实 UI，随即清空剪贴板。配对码不进入命令行、XCTest launch environment、URL、源码或版本库；`typeText` 仅用于普通设备名称。

先用非真实 sentinel 验证剪贴板路径，扫描 XCTest 日志及 `.xcresult` 均未发现 sentinel。随后通过同一受控方式完成真实 Staging 配对；最终成功测试日志、结果包及 Staging API/Agent 日志均未发现一次性配对码或 `trd_dev_` Bearer 前缀。临时测试计划、密钥文件及结果产物已清理，测试设备已撤销。

安全诊断中曾发现一个**失败的早期实验 `.xcresult`** 会因测试运行进程环境诊断而包含当时的单次配对码（不是 Bearer）。该码随后通过成功配对兑换，测试设备已撤销；含码结果包已删除，后续改用只传私有文件路径的剪贴板方案并复测通过。不得再把秘密放进 XCTest plan 环境变量。

## 实际 Staging 验证

| 验收项 | 结果 |
|---|---|
| Simulator HTTPS 配对 | PASS；使用 Staging LAN HTTPS + 实际配对 UI + 本地设备凭据兑换 |
| Keychain 持久化 | PASS；退出并重启 App 后使用凭据读取列表和详情 |
| 新闻读取 | PASS；真实 Staging fixture 列表及详情，不是 Mock |
| 权限不足 403 UI | PASS；仅在 Staging DB 临时移除该设备 `news:read` scope 后验证，随后恢复 scope |
| Cloudflare Staging Relay | PASS；恢复 Agent 后经现有 Staging Worker 真实读取列表和详情 |
| Relay Agent 恢复 | PASS；Agent healthy 后实际 API 读取成功；停止时容器退出码 `0`、`OOMKilled=false` |
| Agent 停止后的 Relay 状态 | PASS；Worker `/health` 在 Agent 停止后返回 503 |
| 401 与本机清理 | PASS；撤销设备后 App 进入重新认证界面；删除本机凭据后显示未配对 |
| LAN/Relay 错误回退 | 保留既有 API Client 单测；401/403 不作为 reachability fallback 条件 |
| 后台恢复/重复请求 | PASS；回到前台触发刷新；并发初始 load 只发起一个新闻请求；UI 无重复行 |
| 日期/null 字段/AI 状态 | PASS；中文 locale formatter、空发布时间、空摘要、`not_requested` 及失败分析重试 |
| 安全区域/底部导航 | PASS；UI Test 断言底栏可见、可交互且在 Simulator 屏幕边界内 |

中文日期用 Debug UI Test 的 `zh_CN` locale override 对 SwiftUI Environment 验证；这证明格式化跟随 locale，但不是对实体 iPhone 系统地区设置的证明。

Staging Worker 保持版本 `d89eed5f-fa2e-4590-98ef-2b6ecc0d358b`。V1 OpenAPI SHA-256 仍为 `80ecf1eb8e25a400375cb555cb6c819afdee857453b632ae59cb3f906da65231`；V2 OpenAPI SHA-256 仍为 `eedbb42574f7ced5114e6d84563820955edaf49e07b2035a9708d57ff32da1b3`。

测试期间没有生产请求、长期监控、定时读取或模型调用。实际 Staging 读请求仅为上述 UI 验收所需的少量 GET。

## iPhone 真机与个人使用条件

目前不满足 04B 前置条件：

- 未识别/批准可用的真实域名和 DNS 管理权限；当前 TLS SAN 不匹配将来需用的公信 FQDN。
- `DEVELOPMENT_TEAM` 未在 Xcode target 配置；虽有本机 Apple Development identity，仍不能据此证明可合法签名安装到该 iPhone。
- 没有在实体手机执行公司 Wi-Fi → 蜂窝 → Wi-Fi；未切换用户网络。

因此当前**不具备实体 iPhone 日常个人使用条件**。后续需先确认既有域名/DNS 所有者并批准 Staging DNS-01 + Gateway 方案，配置团队签名后，再单独安排获授权的蜂窝网络验收。

## 代码与验证

变更范围：XCTest 配对秘密改用安全本地文件路径/粘贴；Debug-only locale 验证；新闻日期明确使用 SwiftUI Environment locale；新闻 feed 并发 load/refresh 防重；恢复前台刷新；定向 UI/unit tests。

Mock/default Simulator 全量测试：**19 个 unit tests 通过；14 个 UI tests 通过；6 个 Staging opt-in UI tests 按默认 Mock 配置跳过；0 失败**。单独 Staging UI runs：安全剪贴板配对+重启列表/详情、403、Relay 列表/详情、撤销后 401 均通过。Release Simulator build 成功；Release App 二进制不含测试配对/locale 注入标记。

冻结的 V1/V2 OpenAPI 文件未修改；实现与本报告随本轮 Git Commit 一并交付。
