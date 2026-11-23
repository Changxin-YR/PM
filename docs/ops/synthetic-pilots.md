# 本机合成试点与联调

仅用于回环隔离演练。`SIMULATED ONLY` 的角色、项目、附件和回执不代表真实业务签核。不要用 `scripts/dev.ps1 setup` 或 `migrate` 启动本套试点；这些命令固定连接共享 `55439/pmo`。

## 启动和复跑

当前项目在 `native` 持久试点：Web `127.0.0.1:18778`，API `127.0.0.1:18779/api/v1/`，数据库 `127.0.0.1:55443/pmo_pilot`，私有原件在 `.local/pmo-synthetic-pilot-native/private-evidence`。全部限本机；回归独用 `55444`。未设置 `PMO_PILOT_RUNTIME` 会选择旧 Docker 试点（Web `18775`、API `18776`、DB `55441`、回归 `55442`），不是当前实例。

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

`status` 区分服务就绪、配置已保存、模型已加载。`stop` 只通知本套应用监督进程退出，保留数据库、附件及回执；`start` 始终关闭模型。只有专用 schema 能按保守合成政策演练。用 `python -m scripts.pilot_regression -- ...` 在独立回归库测试。固定制品的 Compose 探针是另一套环境，当前 `pmo104-rc-e28da52c`（回环 `18777`）。新 smoke 项目选择未用的 `PMO_SMOKE_SUBNET`，用 `deploy/pilot-smoke.compose.yaml` 和 `deployment_smoke.py <project> --pilot-network` 验证；该 override 不启用模型。

`seed` 已完成时只读回，不覆盖用户手工操作。`pilot_rehearsal` 通过服务记录 A 的变更、锁定对账和待接收风险，并把两个 `-DRILL` 项目走到归档；A 保留实施现场，B 保留 Host 委托草稿。模拟身份：发起 `sim-init`；PM `sim-pm-a` / `sim-pm-b`；Sponsor `sim-sponsor`；评审 `sim-review-01` / `sim-review-02`；客户 `sim-customer`；流水核验 `sim-finance`；对账 `sim-reconcile`；风险接收 `sim-risk-receiver`；任命 `sim-appoint`。`sim-admin` 不代办业务。在私有终端运行 `python -m scripts.pilot_seed login <role>`（不加 `sim-`）读取合成口令，不截图或发到聊天。

用 `sim-pm-a` 进入 A 的[项目助理](http://127.0.0.1:18778/#project=ffc9208e-3d4d-4f60-ad1c-5f9e60ee7464&tab=assistant)和[报告分析](http://127.0.0.1:18778/#project=ffc9208e-3d4d-4f60-ad1c-5f9e60ee7464&tab=reports&report=ae445a61-9a1a-40c1-930b-b619f70aa608)，用 `sim-init` 进入 B 的[我的 Agent 令牌](http://127.0.0.1:18778/#project=efcf081f-2e01-401e-b0ca-2d2a3889bc35&entry=agent-tokens)。在选了 `native` 的私有终端运行 `& $py -m scripts.pilot_seed login pm-a` 或 `& $py -m scripts.pilot_seed login init` 查看对应合成口令。

## 打开和批准 Host

以 `sim-init` 打开「我的 Agent 令牌」。接入时用 `& $py -m scripts.pilot_host issue codex --native` 或 `issue dsh --native` 分别创建或轮换 1 小时委托，只有 B 的列表、详情和草稿修改；比较指纹，不粘贴密钥。启动器检查到期时间，令牌只经子进程环境交付。`& $py -m scripts.pilot_host config --native` 在 ACL 保护的本机目录生成不含密钥且显式选择实例的 Codex TOML 和 DSH Cordis overlay，生成不等于加载或批准。

- Codex Desktop：在真实 Host 中登录，添加 `pmo_synthetic_codex`，命令和参数见 `codex-mcp.toml`；信任或连接提示由本人确认。必须从 Desktop 发起调用，同版本 CLI 或 SDK 测试不替代 Desktop。
- DSH：先确认实际 Harness 安装、版本及本机 stdio 配置。`dsh-mcp.cordis.yml` 按 DSH 源码 `docs/user/guide/mcp-memory.zh.md` 的样例生成，仅在该版本支持对应插件时加载。官网聊天和 Mandate 都不是本项 Host。官方 Desktop Gateway 批准与 PMO 令牌独立；本人按实际入口授权，不从凭据存储提取连接令牌。远端 Host 不能直接访问本机回环。

两个 Host 分别记录版本、40 工具发现、限定项目列表/详情、同一幂等键修改 B 的合成草稿并经 Web 核对、重放、跨项目/越动作拒绝、撤销后旧进程失败且另一 Host 可读。先保留接入证据再人工撤销；轮换后启动新进程。SDK 工具、审计和重放证据另计。

## 配置模型

在选了 `native` 的私有交互终端运行 `& $py -m scripts.pilot_local configure-models`。两个配置独立，URL 需支持 [PMO 报告抽取](../adr/0013-report-analysis-jobs.md)或[助理预览](../adr/0018-source-bound-assistant-previews.md)专用 JSON 契约。实际版本在网关选择，本地只存版本标签。保存不连网、不启用模型；不要把口令、URL、密钥或进程环境写进日志和版本库。

确认网关可接收所选合成项目的必要片段后，由本人运行 `python -m scripts.pilot_local activate-models` 并输入 `SEND SYNTHETIC ONLY`。它重启进程加载配置，本身不请求网关。API 持助理认证；报告 worker 单独持报告认证，使用受限 API 角色，不持迁移/Outbox 身份、不挂载原件。区分保存、加载、连通和实际业务结果。范围之外的项目与历史 Lesson 不进入网关。模型只生成候选，核验、评分与裁决仍由有权人工完成。停用时执行 `stop` 后 `start`；无兼容网关时不填猜测的厂商聊天端点。
