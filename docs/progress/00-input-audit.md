# 输入核对，2026-11-19

本地初始状态：四份用户评审资料，没有 .git、AGENTS.md 或应用代码。指定的 Changxin-YR/PM 远端 main 起点为 45307c2，仅 README.md。四份本地历史文件原样保留，不纳入本次提交。

附件 ZIP SHA-256：6b8f8e6ff8e981f73f40e2388f175a0842563702ba2c677973fb7c871b9841ed。逐项检查目录前缀、单一文件名、大小、重复与保留名，CRC 及清单中 27 个文件的 SHA-256 全部吻合。原始 HTML 为 e88d37a5e59fe2e3c14b768ab9d11ae11100b68308278c9bf9b5a0ab1c68a532。docs/spec 保留 28 个原件，另有字节相同的业务源副本。Git 对原件不转换换行。

来源顺序：授权与安全、已签决议、原始 HTML、V1.0.4 方案及规则、工程契约。历史审查和提示词不构成业务签字。规格版本 1.0.4；确定性规则版本 lite-v1.0-standalone.3。

追踪从 03 高层矩阵及 07 原文条款两侧建立，见 docs/acceptance/traceability.csv。95 个 REST 操作、40 个 MCP 工具、102 项原文条款以及参考向量只是输入规模，不是实现进度。

执行顺序：M0 先验证 PostgreSQL 事务/迁移、项目权限、真实 HTTP 与 stdio，再补齐具名 DTO 和约束；M1 无模型 Web 管理；M2 三关口/八规则/归档；M3 全工具逐项验权；M4 证据管道、四模式、恢复演练和综合验收。材料中的预期结果不直接作为服务输出。

## 首次运行目录差集（2026-11-20，历史快照）

这是只读旁路的**首次实测记录**，当时工作树 HEAD 为 `862249d`。下列数量及缺口均以当时的 API 进程和 MCP 注册为准，**不是后续代码或接口的当前计数**。不以工作树 HEAD 推断 API 进程的构建提交。

| 目录 | 冻结来源 | 首次实测 |
|---|---|---|
| REST | [V1.0.4 OpenAPI 路由基线](../spec/12_OpenAPI_路由请求基线.yaml) | `127.0.0.1:8006/openapi.json` 返回 HTTP 200；52 个路径、64 个操作：**58/95 基线 + 6 工程额外**，缺 37；同方法同路径的 `operationId` 无漂移。 |
| MCP | [40 项工具契约](../spec/11_MCP_工具契约.json) | 真实 SDK stdio `initialize`、`list_tools`：**30/40**，缺 10，无额外；结果与 [`ROUTES`](../../pmo/tool_routes.py) 一致，30 项均能按方法、路径、ID 对应 8006 路由。仅枚举，未调用业务工具。 |

### REST 缺失的 37 个 operationId

| 范围 | 首次缺失 ID |
|---|---|
| 评审核心 | `human_review_score`、`human_review_appeal`、`human_review_decide`、`pmo_decision_get` |
| 审批和阶段申请/执行 | `human_approval_inbox`、`human_decide_approval`、`pmo_transition_request`、`human_execute_transition` |
| 组织管理 | `admin_members_list`、`admin_users_list`、`admin_user_invite`、`admin_user_update_status` |
| 结项与风险 | `human_closing_check`、`human_risk_close`、`human_risk_handoff` |
| 复盘与教训 | `human_retrospective_get`、`human_retrospective_save`、`human_lessons_list`、`human_lessons_draft`、`human_confirm_lesson`、`pmo_lessons_search` |
| 健康与任务 | `pmo_health_get`、`human_health_history`、`human_appeal_health`、`pmo_task_list`、`human_task_create`、`pmo_task_update` |
| 报告 | `pmo_report_list`、`pmo_report_submit`、`pmo_report_get`、`human_report_submit_existing`、`human_report_confirm`、`human_report_analyze` |
| Fact | `human_report_fact_list`、`pmo_fact_propose`、`human_fact_verify`、`human_fact_clarify` |

REST 工程额外 6 项：`human_evidence_verify`、`human_appointment_queue`、`human_requirement_baseline_get`、`human_requirement_baseline_sign`、`human_requirement_metrics`、`human_issue_role_invitation`。不计入 95 项基线分母。

### MCP 缺失的 10 个工具 ID

`pmo_decision_get`、`pmo_transition_request`、`pmo_health_get`、`pmo_task_list`、`pmo_task_update`、`pmo_report_list`、`pmo_report_get`、`pmo_report_submit`、`pmo_fact_propose`、`pmo_lessons_search`。

### 评审直接依赖

首次盘点时，[Web 评审页](../../web/src/Reviews.vue) 直接依赖 `pmo_project_get`、`pmo_review_list`、`pmo_review_prepare` 和 `pmo_review_submit`；准备接口与 `pmo_gate_check` 共用后端评估器。三项评审接口及 `pmo_gate_check` 均有 MCP 注册。已有证据只覆盖进入待评审；闭环仍需表中的评分、复议、人工终裁和决议读取。终裁与 `transition_project()` 在同事务完成，不能用另一次状态迁移调用代替。落地有条件通过依赖补强任务；结项预检因缺正式双口径对账拒绝，逐笔凭证核验不能代替对账，阻塞风险也须正式关闭或经接收方确认移交。见 [ADR 0010](../adr/0010-review-submission.md) 和 [M0 送审实测](M0-report.md)。

### 实际命令、退出码与判定口径

首次运行在仓库根目录执行：

```powershell
curl.exe --noproxy '*' --connect-timeout 2 --max-time 6 -sS -f -w '\nHTTP %{http_code}\n' http://127.0.0.1:8006/openapi.json
$script | .\.venv\Scripts\python.exe -B -X utf8 -
```

- `curl.exe` 退出 `0`，HTTP 200。Python 的 stdin 入口分别执行 REST 差集和 MCP 探针，两次均退出 `0`。
- REST 脚本用 PyYAML 解析冻结基线，用 `urllib.request` 禁代理 GET 实时 JSON，比较 `(method, path, operationId)`，断言两侧 `operationId` 各自唯一。
- MCP 探针以 `StdioServerParameters` 启动同一 Python 的 `-B -m pmo.mcp_server`，未传 `PMO_AGENT_TOKEN`，对无后续页的 `list_tools` 按名称比对契约及 `ROUTES`，并检查每项 REST 映射。
- `Import-Csv -Encoding utf8 -LiteralPath` 读两份矩阵，PowerShell 退出 `0`：规格矩阵 66 行，追加任务前的证据矩阵 169 行。
- `git --no-optional-locks -c core.quotePath=false status --short --branch` 退出 `0`，首次盘点仅显示四份原件未跟踪；`Get-FileHash -Algorithm SHA256 -LiteralPath` 前后核对四份原件，退出 `0`，SHA-256 均未变。

命令成功退出不等于目录完整或业务通过。首次盘点未运行 pytest、浏览器、迁移或构建；既有回归证据另见 [M0](M0-report.md)、[M1](M1-report.md)、[M3](M3-report.md)、[M4](M4-report.md)。

[证据矩阵](../acceptance/traceability.csv)末追加的 32 个任务来自[主方案 §11.2](../spec/01_PMO_V1_独立系统完整开发方案.md#112-任务拆分建议)，按 §14 上线定义保留缺口。新增行的 `status` 仅用 `PASS`（完整单项有实测）、`FAIL`（已量测且未达该任务的明确门槛）、`BLOCKED_EXTERNAL`（验收依赖尚未提供的外部输入）、`NOT_RUN`（无整项实测，含只有静态或局部证据）。局部证据保留在 `implementation`、`test`，缺口写在 `requirement`；不归整既有 169 行历史状态，不作为阶段放行结论。

## 0019 生命周期目录复查

依据冻结 OpenAPI/MCP 目录；合成环境与源码树见 [M2](M2-report.md)。`app.openapi()` 按方法、路径和 ID 比对，退出 0：75/95 基线、14 项工程接口。真实 SDK stdio 枚举断言在 `tests/test_runtime.py::test_mcp_stdio`（纳入退出 0 的全量测试）：33/40，仅代表注册；独立真实调用证据见各用例，不按 33 项全部验收。

REST 尚缺：`pmo_task_list`、`human_task_create`、`pmo_task_update`、`pmo_report_list`、`pmo_report_submit`、`pmo_report_get`、`human_report_confirm`、`human_report_submit_existing`、`human_report_analyze`、`pmo_fact_propose`、`human_fact_verify`、`human_fact_clarify`、`human_report_fact_list`、`pmo_health_get`、`human_appeal_health`、`human_health_history`、`admin_users_list`、`admin_user_invite`、`admin_user_update_status`、`admin_members_list`。

2026-11-21 `0020`：复用按 `(method, path, operationId)` 比对的 `app.openapi()` 探针，退出 0，75/95 基线、17 项工程接口。新增需求例外读取、签署、撤销均为受控人工接口，不增加 MCP 注册；本批真实 HTTP/Edge 证据见 [M2](M2-report.md)。

MCP 尚缺：`pmo_task_list`、`pmo_task_update`、`pmo_report_list`、`pmo_report_submit`、`pmo_report_get`、`pmo_fact_propose`、`pmo_health_get`。MCP 仍无直接评分、复议、终裁或归档工具。
