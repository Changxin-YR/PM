# PMO R5 验收记录

## 运行环境

| 项目 | 实际环境 |
|---|---|
| 工作台 | [本机可运行预览](http://127.0.0.1:18788/)，API `18789` |
| 数据 | Windows 原生 PostgreSQL `16.14`，Alembic `0043`。预览 `55454`，自动回归 `55444`，真实网关复验副本 `55455` |
| 运行时 | Python `3.12.15`、Node `24.22.0`、Vite `7.3.8`、Microsoft Edge `158.0.4370.33` |
| 原件 | SHA‑256 `e88d37a5e59fe2e3c14b768ab9d11ae11100b68308278c9bf9b5a0ab1c68a532` |
| 旧库 | 旧工作台和原共享库未运行迁移。已批准的两个合成模型项目在另外的 `0041` 冷副本上迁至 `0043` 后用于复验 |

运行候选 `a8d628779f5c994d7bf92600f0684f030b374941`，源码树 `3a734d7ee583fbd3bf85e23958a146b2d2119b16`。本机启动回执的 Web `index.html` SHA‑256 为 `f96ea892a3baef4eb9e9a377018de0d1bb2289b60ae2a23bde96c06130b9cf81`。后端、迁移、部署和运行脚本与后端、真实模型及 Host 复验候选 `613483f` 一致，最后改动集中在需求指标文本空白、视觉测试及其文档。

本机生产构建的 Web、API 存活及就绪探针均返回 HTTP 200。实际 Edge 经授权会话打开 `18788` 的实施项目概览，确认四项指标和面板正确出现，页面异常数为 0。证据 `preview-18788-overview.png`。

运行目录实读为 **171 个 REST 操作**，含 95 个冻结基线及 76 个工程操作，**40 个 MCP 工具**。`.local/r5-evidence/rest-operations.csv` 为实际运行路由与生产代码绑定的结果，OpenAPI 快照同目录。目录核验和实际调用覆盖各自以对应测试记录为准。

## 产品变更与专项

| 项目 | 路径与验证 |
|---|---|
| B1 分类 | 三类人天精确对平，未分类和历史总量可读。分项候选抵扣同源已核验差额，独立核验后才进入实际。分类 Fact 绑定确切报告字段、期间、版本。含 7.00、4.50、旧未分类阻断、金额类别和重复防护。[报告证据](reporting.md) |
| B2 评分、快通、校准 | 提示可明确的理由矛盾，无法判断时转人工核对。原文快通按明确政策选用，独立评分齐备后由发起人确认并在同一事务写备案和迁移。结项评价保留冻结评分、锚点、差值和证据。[评审证据](reviews.md) |
| B3 LR-04 | 两口径分别按未舍入数值触发。重估材料、依据、涉事任务、当前角色和独立 Decision 绑定。继续投入后仍按实际偏差显示红灯。已验证错任务、旧快照、Agent、失权、并发、Outbox 故障回滚和重放。`tests/test_cost_reassessment.py` 和 `web/e2e/cost-reassessment.mjs` |
| B4 送达 | 同一正式 HTML 原件哈希用于下载和隔离投递。发布、授权入队和回执分开，重启后保留真实合成收件箱证据。外部渠道、供应商核查、真实接收仍待验。[报告证据](reporting.md) |
| 视觉 | [视觉实施与验证](visual-acceptance.md)、[功能保留矩阵](feature-parity.csv)。补充状态采用真实合成角色和回执。 |
| 业务逐条 | [102 条原文追踪](lite-traceability.csv)、[20 项逐条差异及初始缺口](differences.md)。 |

## 复现命令

```powershell
$env:PMO_PILOT_RUNTIME='native'
$env:PMO_BROWSER_CHANNEL='msedge'
$env:PMO_R5_SCREENSHOTS=(Join-Path (Get-Location) '.local/r5-evidence/screenshots')
$env:PMO_R5_REVIEW_SCREENSHOTS=$env:PMO_R5_SCREENSHOTS
$env:PMO_R5_VISUAL_OUTPUT=(Join-Path (Get-Location) '.local/r5-evidence/gallery')
.venv\Scripts\python.exe -m scripts.pilot_regression -- -q --tb=short --junitxml=.local/r5-evidence/backend.xml
.venv\Scripts\python.exe -m scripts.pilot_regression -- -q --tb=short --junitxml=.local/r5-evidence/edge.xml tests/browser_case.py tests/browser_review_r5.py tests/browser_r5_reporting.py tests/browser_visual_r5.py
.venv\Scripts\python.exe -m mypy pmo --ignore-missing-imports
npm.cmd --prefix web run build
.venv\Scripts\python.exe -m alembic heads
```

固定规格脚本为 `docs/spec/08_*.py`、`15_*.py`、`19_*.py`。应用检查复用同一候选代码，后端分片的实际命令、JUnit 和退出摘要在 `.local/r5-evidence/`。规格检查保留冻结原文、102 个向量、95 个基线 REST 操作、40 个 MCP 工具和 13 个工程状态。当前运行 OpenAPI 的增量独立校验。

| 验收 | 实际结果 |
|---|---|
| 全量后端 | **446 个唯一用例最终通过**。四分片首轮 444 通过，报告 worker 两项分别遇到 HTTP 500 及短租约被二次领取。两项同代码独立复验通过，随后完整报告分析套件 12／12 通过。保留初轮 `backend-1.xml`～`backend-4.xml` 和复验 `report-analysis-final.xml`，合并记录 `backend-final.xml`。 |
| 全量 Edge | 最终候选 **35／35 通过**。首轮 32 通过。周健康登录超时和视觉脚本失败随后通过复验。草稿回归定位到需求指标百分比后的新增空白，已修复并保留原断言。修复后完整分片分别 11、12、12 项通过，原始 `edge.xml` 和最终 `edge-final-*.xml` 均保留。 |
| 规格、类型与构建 | 三个规格脚本退出 0，mypy 检查 109 个模块退出 0。Vue 类型检查及 Vite 生产构建退出 0。构建保留单包体积告警。 |
| 视觉与 A4 | 实际 Edge 全站 283 条布局与导航断言通过，浏览器 JS 错误与 HTTP 5xx 均为 0。五档 CSS 宽度及 100／125／150% 原生缩放，真实截图归档 96 张 PNG。长中文正式周报实际打印为 1 页，约 `594.96 × 841.92 pt`。 |
| 迁移、回滚及恢复 | 全量后端的 PostgreSQL 新建／降级／升级与数据库、附件联合恢复用例通过。另一独立冷副本从 `0041` 升 `0043`，并独立验证原件与权限。 |

[后端 JUnit](../../../.local/r5-evidence/backend-final.xml) · [最终 Edge JUnit](../../../.local/r5-evidence/edge-final.xml) · [正式周报 PDF 与截图包](../../../.local/PMO-R5-Edge-视觉截图与补充证据.zip) · [实际页面截图索引](../../../.local/r5-evidence/gallery-final/index.html)

追踪共 102 条。其中 82 条通过工程复核，8 条待业务口径确认，4 条待外部验收，8 条为旧排期或未来升级。20 项原有优化差异为 12 项语义等价、8 项需决议。正式签核、实际收件和生产目标验收按独立门槛判断。

### 真实模型与恢复

在 `55455` 验证从旧合成环境 `0041` 迁到 `0043`。迁移后读到 6 个项目、14 张评审、11 份报告、18 条成本流水及 4 份复盘。90 个私有原件逐个检查大小和 SHA‑256。API 与 Outbox 角色分别验证最小权限及 DDL 拒绝。原数据和私有附件留在旧目录，复验只在复制件执行。

同一已批准网关、模型及精确合成来源下，固定候选 `613483f` 的立项、台账、结项和过程四模式均取得新真实 `ready` 回执。结项曾实际触发 `MODEL_TIMEOUT`，确定性人工工作台仍返回 HTTP 200。相同候选随后取得新 `ready` 回执。报告通道使用审批绑定的报告版本 `1` 和内容摘要，真实 worker `90ca14bc-44a3-4191-ac6e-1cd955c64315` 提取带原话的 `2.50 人天`，人工将数值 `2.50` 作为候选提报，独立核验 HTTP 200 后回读 `verified`。累计叙述仍是源事实，未作为本期增量入账。脱敏请求及状态保存在 `.local/r5-evidence/model-proof.json` 和 `model-proof-manual-closure.json`。

### Q1～Q8 与 T01～T10

| 条款 | 运行测试与观察 |
|---|---|
| Q1 | `test_reviews.py`、`test_assistant.py`、`test_retrospectives.py` 验证立项分数、弱项、历史教训及引用。 |
| Q2 | `test_health.py`、`test_assistant.py`、`test_costs.py` 验证确定性时间、成本双口径、需求满足率、时点、来源和不可用状态。 |
| Q3 | `test_forecast_governance.py`、`test_health.py` 验证预测漂移、无已批准变更、来源和去重。 |
| Q4 | `test_spending.py`、`test_postings.py` 按实际发生与实际过关时点检索立项前支出，排除候选和冲销。 |
| Q5 | `test_lifecycle.py`、`test_requirement_exceptions.py` 验证三项结项条件和逐项缺口，客户书面接受仅限明确需求。 |
| Q6 | `test_retrospectives.py`、`test_assistant.py` 通过检索读取已确认 Lesson，检查分类、项目出处及无来源拒答。 |
| Q7 | `test_weekly_health.py`、`test_requirement_disputes.py` 验证北京时间周界、黄红项目及触发原因，旧状态和不可信状态单独显示。 |
| Q8 | `test_change_history.py`、`test_requirement_baselines.py` 与 Edge 读取实际变更时间线、审批人、前后版本。 |
| T01–T04 | `test_runtime.py`、`test_mcp_action_matrix.py`、`test_stage_edges.py`、`test_evidence.py`，覆盖 Web/REST/MCP 一致性、跨项目拒绝、非法跳关和人工裁决入口。 |
| T05–T07 | `test_postings.py`、`test_r5_reporting.py`、`test_cost_reassessment.py`、`test_report_analysis.py`、`test_facts.py`、`test_acceptance_baseline.py`，覆盖同键重试、版本、错误出处、注入防护和原子审计。 |
| T08 | `test_retrospectives.py`、`test_assistant.py`，跨项目教训检索需授权，未确认教训不进入材料。 |
| T09 | 当前 SDK 和已安装 Codex app-server 独立实测。Codex Desktop GUI、DSH Desktop 在 R5 候选上 `NOT_RUN`，DSH Headless 缺 `deepseek-official` 凭据。详见下文。 |
| T10 | `test_reference_vectors*.py`、`test_health.py`、`test_outbox.py`、`test_recovery.py`，含八规则、重复扫描与通知、数据库和附件联恢复。 |

### 实际 Host

安装版 Codex app-server `0.171.0-alpha.13.2` 在独立临时会话直接使用 JSON-RPC，发现 40 项 MCP 工具。委托仅可列出和读取 `SIM-NEW-001`，跨项目 `RESOURCE_NOT_FOUND`，超动作 `FORBIDDEN`。草稿写入与同键重放均为版本 6，独立 Web API 回读 6。恢复原名称至版本 7，撤销令牌后同一存活连接返回 `UNAUTHORIZED`。Codex CLI 模型回合超时，桌面 GUI 保留历史回执。DSH Headless `0.2.2-rc.5` 在首次工具调用前返回 `MISSING_CREDENTIAL`；需要已登录并获准更新临时连接的 DSH 目标客户端，完成工具发现、受限读写、同键重放及撤销复验。两个临时委托均已撤销，持久客户端配置保持原样。脱敏回执在 `.local/pmo-r5-preview/r5-host-attempt-summary.json`。

## 外部解除条件

| 门槛 | 解除所需 |
|---|---|
| 签核 | 八项 DEC、快通选择、重报期限、RP-01／02 由有权业务负责人签署，独立核验后激活到指定组织、项目、动作与环境。 |
| 外部送达 | 审批材料范围、指定报告版本、真实地址与收件资格，接入获准供应商，并在真实回执中区分已提交、已送达、实际收件及未知结果。 |
| 两个真实试点 | 指定新立项与实施期项目，提供合法身份、范围和原件，按实际阶段完成验收。 |
| 目标环境 | HTTPS、目标 CI、账号与附件权限、告警实收、目标性能、最近七个成功备份日、每周联合恢复，确认 RPO／RTO。生产当前 NO-GO。 |
| 上线后观察 | 从实际获准上线日起运行一月，按原文八问复核并由业务方决定是否调整阈值。 |
