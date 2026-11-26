# 本机合成试点与联调

仅用于回环隔离演练。`SIMULATED ONLY` 的角色、项目、附件和回执不代表真实业务签核。不要用 `scripts/dev.ps1 setup` 或 `migrate` 启动本套试点；这些命令固定连接共享 `55439/pmo`。

## 启动和复跑

原生持久试点入口为 Web `127.0.0.1:18778`、API `127.0.0.1:18779/api/v1/` 和 PostgreSQL `127.0.0.1:55443/pmo_pilot`，私有原件在 `.local/pmo-synthetic-pilot-native/private-evidence`。全部限本机，回归独用 `55444`，冷恢复副本独用 `55445`。从 `0036`、`0037`、`0038`、`0039` 或 `0040` 升级前，先停止专用试点应用及 worker，准备并核验对应数据库和私有原件的本机恢复点。`python -m scripts.pilot_restore_evidence <冷恢复点>` 在独立集群复验旧回执、附件字节和受限角色。随后用新候选执行 `& $py -m scripts.pilot_local init --upgrade-existing`，启动器校验实际 Alembic 唯一 head。升级成功后启动同一候选。未设置 `PMO_PILOT_RUNTIME` 时使用独立 Docker 试点 Web `18775`、API `18776`、DB `55441`、回归 `55442`。

在 Windows PowerShell 7 的仓库根目录运行。每个新终端都显式选 `native`。如果 `status` 已就绪则无需重启；主机重启后按下列顺序恢复同一实例。原生运行器使用已准备的 PostgreSQL 16 二进制（此机为 16.14），位置 `.local/pmo-synthetic-pilot/pg16-runtime/pgsql/bin`。

```powershell
$env:PMO_PILOT_RUNTIME = 'native'
$py = '.\.venv\Scripts\python.exe'
& $py -m scripts.pilot_local status
& $py -m scripts.pilot_local init
npm.cmd --prefix web run build
& $py -m scripts.pilot_local start
& $py -m scripts.pilot_seed seed
& $py -m scripts.pilot_rehearsal
& $py -m scripts.pilot_rehearsal browser
```

`start` 始终关闭模型，同时启动 Web、API、Outbox 和规则调度进程 `pmo.rule_scan --watch`。原生与本机 Docker 数据库模式使用同一启动器。调度器使用受限的 `pmo_api` 权限和专用合成 schema，读取本套私有原件目录。调度与模型授权无关。

`status` 显示 HTTP 就绪、模型配置和已匹配的进程角色。调度器状态 `running` 表示记录的调度进程存在且命令匹配，`not_recorded` 表示沿用旧标记，可在一次受控停止和启动后复核，`unavailable` 表示没有可核验的调度进程。在受保护的 `services.log` 内可查看 `rules.batch` 或 `rules.failed`。`model-preflight` 也显示调度器状态，模型载入状态仍独立核验。

`stop` 通知本套应用监督进程退出，保留数据库、附件及回执。主机重启后若留下旧标记，启动、停止和显式升级会在两个回环端口均可独占绑定、记录进程均已退出或已由不匹配命令的进程复用时清理旧标记。如果停止等待超时，会保留运行标记和停止请求。此时查看 `status`、受保护的 `supervisor.log` 和 `services.log`。进程角色匹配只用于诊断，标记没有足够的信息证明孤儿进程和监听端口的归属，禁止按记录 PID 强停。

只有专用 schema 能按保守合成政策演练。用 `python -m scripts.pilot_regression -- ...` 在独立回归库测试。固定制品的 Compose 探针是另一套环境，已验证的基线栈 `pmo104-rc-15fde057`（回环 `18780`）。新 smoke 项目选择未用的 `PMO_SMOKE_SUBNET`，用 `deploy/pilot-smoke.compose.yaml` 和 `deployment_smoke.py <project> --pilot-network` 验证，该 override 不启用模型。

`seed` 已完成时只读回，不覆盖用户手工操作。`pilot_rehearsal` 通过服务记录 A 的变更、锁定对账和待接收风险，并把两个 `-DRILL` 项目走到归档；A 保留实施现场，B 保留 Host 委托草稿。模拟身份：发起 `sim-init`；PM `sim-pm-a` / `sim-pm-b`；Sponsor `sim-sponsor`；评审 `sim-review-01` / `sim-review-02`；客户 `sim-customer`；流水核验 `sim-finance`；对账 `sim-reconcile`；风险接收 `sim-risk-receiver`；任命 `sim-appoint`。`sim-admin` 不代办业务。在私有终端运行 `python -m scripts.pilot_seed login <role>`（不加 `sim-`）读取合成口令，不截图或发到聊天。

用 `sim-pm-a` 进入 A 的[项目助理](http://127.0.0.1:18778/#project=ffc9208e-3d4d-4f60-ad1c-5f9e60ee7464&tab=assistant)和[报告分析](http://127.0.0.1:18778/#project=ffc9208e-3d4d-4f60-ad1c-5f9e60ee7464&tab=reports&report=ae445a61-9a1a-40c1-930b-b619f70aa608)，用 `sim-init` 进入 B 的[我的 Agent 令牌](http://127.0.0.1:18778/#project=efcf081f-2e01-401e-b0ca-2d2a3889bc35&entry=agent-tokens)。在选了 `native` 的私有终端运行 `& $py -m scripts.pilot_seed login pm-a` 或 `& $py -m scripts.pilot_seed login init` 查看对应合成口令。

## 打开和批准 Host

以 `sim-init` 打开「我的 Agent 令牌」。接入时用 `& $py -m scripts.pilot_host issue codex --native` 或 `issue dsh --native` 分别创建或轮换 1 小时委托，只有 B 的列表、详情和草稿修改；比较指纹，不粘贴密钥。启动器检查到期时间，令牌只经子进程环境交付。`& $py -m scripts.pilot_host config --native` 在 ACL 保护的本机目录生成不含密钥且显式选择实例的 Codex TOML 和 DSH Cordis overlay，生成不等于加载或批准。

- Codex Desktop：本机 `pmo_synthetic_codex` 已用安装版 CLI 合并进正确配置，命令和参数可与 `codex-mcp.toml` 核对，其他 MCP 条目不变。CLI 读回不证明 Desktop 已加载。保持其他任务不受干扰，空闲时在实际 Desktop 的设置中核对 MCP 服务条目及本机 Host 加载状态；若当前会话未刷新，由本人选合适的时机重开客户端。登录、信任或连接提示由本人确认。必须从 Desktop 发起调用，同版本 CLI 或 SDK 测试不替代 Desktop。
- DSH：先确认实际 Harness 安装、版本及本机 stdio 配置。`dsh-mcp.cordis.yml` 按 DSH 源码 `docs/user/guide/mcp-memory.zh.md` 的样例生成，仅在该版本支持对应插件时加载。官网聊天和 Mandate 都不是本项 Host。官方 Desktop Gateway 批准与 PMO 令牌独立；本人按实际入口授权，不从凭据存储提取连接令牌。远端 Host 不能直接访问本机回环。

两个 Host 分别记录版本、40 工具发现、限定项目列表/详情、同一幂等键修改 B 的合成草稿并经 Web 核对、重放、跨项目/越动作拒绝、撤销后旧进程失败且另一 Host 可读。先保留接入证据再人工撤销；轮换后启动新进程。SDK 工具、审计和重放证据另计。

## 配置模型

在选了 `native` 的私有交互终端运行 `& $py -m scripts.pilot_local configure-models`。两个配置独立。项目助理使用工作台账号 `sim-pm-a` 登录；向导里的网关 Bearer Key 与工作台口令无关，不粘贴到聊天或截图。没有已批准的地址、版本/模型 ID 和范围时选 `n` 跳过，不影响人工功能。

| 字段 | 填写要求 |
|---|---|
| `Protocol` | `pmo_json`：已有 [PMO 报告抽取](../adr/0013-report-analysis-jobs.md)或[助理预览](../adr/0018-source-bound-assistant-previews.md)专用网关，由网关选择模型；`chat_completions_json`：服务方确认支持 JSON 对象输出的 Chat Completions 兼容接口，使用填写的模型 ID。请求不会提供工具。 |
| `Complete endpoint URL` | 由服务方提供的完整 HTTPS 请求地址，只有本机回环夹具可用 HTTP。客户端使用该地址原样 POST，禁止嵌入凭据、查询或片段，也不会跟随重定向。`chat_completions_json` 需填完整接口地址，如 `https://provider.example.invalid/v1/chat/completions`。仅有 `/v1` 一类服务基址时会被拒绝。旧的 `pmo_json` 配置使用基址形态时会提示确认该地址实际承接 PMO JSON 契约。 |
| `Actual model ID/version` | 网关确切模型 ID/版本；不是产品名或 ChatGPT 登录名。 |
| `Project codes` | 按批准范围填写精确代码。报告验收只使用 `SIM-EXEC-001`，也是该通道默认值。助理回车选择两组合成主项目。 |
| `Gateway bearer token` | 在不回显的终端提示输入，网关不要求认证时才留空。 |

保存后，先运行 `& $py -m scripts.pilot_local model-preflight`。它只读取受保护的本地配置、回环服务就绪状态和报告 worker 进程状态。输出包含两个通道的状态与范围，令牌只显示是否已填，完整网关地址不进入诊断。任何已保存通道配置无效时退出码为 1。两个通道均未配置仍可正常运行人工工作台。

| 输出字段 | 如何解读 |
|---|---|
| `configuration` | `missing` 尚未配置，`invalid` 需要按错误修正，`valid` 表示 URL、协议、版本和合成项目范围通过本地校验。 |
| `servicesReady`、`running` | `servicesReady` 表示所选实例的回环 Web 和 API 探针可用。`running` 同时要求本机进程与运行标记一致。 |
| `loaded`、`loadedConfiguration` | 表示本机已核实的运行实例已经加载通道，同时显示启动时加载的版本、协议和项目。重新保存后需重新启动才能加载新配置。 |
| `worker` | 报告通道独有。`running` 表示已加载通道对应的 worker 进程存在，`unavailable` 表示报告通道加载但未找到对应进程，`not_loaded` 表示报告通道未加载。 |
| `providerResult` | 离线预检固定为 `not_checked`。网关认证、协议兼容、实际模型及抽取质量需另取运行证据。 |

报告仍关闭时运行 `& $py -m scripts.pilot_local prepare-model-report`。该命令用合成 PM 的真实会话提交一次日报，保留报告 ID、版本、摘要、原话和独立评审账号回读。日报含具体时点和 `2.50` 人天，报告分析只允许这一条的确切版本。激活前若存在排队或运行作业，先核对对应来源和处置，再重试激活。

如需验证结项模式，使用已归档的独立演练副本。`& $py -m scripts.pilot_local register-model-closing --code SIM-EXEC-001-DRILL --project-id b8393630-db78-47b5-ae7c-c19699155adf` 在专用库核对种子回执、归档状态和四问原文。运行 `activate-models --closing-code SIM-EXEC-001-DRILL` 可在本次启动纳入该项目，使用范围限结项预览。此操作沿用已保存的助理凭据。

由本人在 `& $py -m scripts.pilot_local activate-models` 显示的网关、报告版本、原文、队列和结项来源范围内输入 `SEND SYNTHETIC ONLY`。切换会重启进程加载配置。API 持助理认证，报告 worker 单独持报告认证，使用受限 API 角色。报告 worker 只领取与获准报告 ID、版本、内容摘要完全匹配的作业。模型生成候选后，仍由有权人工核验事实、评分和裁决。停用时执行 `stop` 后 `start`。

加载后再用 `model-preflight` 核对实际加载通道与报告 worker。用范围内的合成项目按实际阶段验证立项、结项和问答的可用来源与结构化预览。过程管家选择现有合成报告，发起报告分析，观察独立 worker 从排队到完成并核对候选原文引用，再复验人工核对入口。保留草稿 B 和实施 A 的阶段。需要其他状态时使用已归档副本的只读来源或新隔离变体。每次记录通道、实际版本、模式、回执或审计编号、候选校验、范围外拒绝及降级状态。证据中省去报告正文和网关配置。

台账问答的「需求变更历史」先选定当前项目的需求，再读取原台账的分页历史、前后版本和来源。此查询不使用模型。立项评审在未填检索条件时按立项卡文本匹配已确认教训，展示命中的文字及立项卡和教训来源。未匹配时保留空结果。
