# M1 人工台账：部分链路已实测，未整体验收

2026-11-20：真实 Edge、Vite、HTTP、PostgreSQL 测试 `tests/browser_case.py` 1 passed / 19.13s。不配置模型，创建项目、刷新、修订、登记里程碑和需求、保存立项卡、登记项目联系人、核对有效成员、组织管理员检索审计均可运行。三个独立会话覆盖提名、发邀请和本人接受。真实业务任命不在测试范围内。

仍缺完整人类生命周期，含评审、执行、成本、变更、风险、报告、验收和复盘。上述浏览器用例不能单独证明 Q1～Q8、T01 或 UI-ALL。

2026-11-20 增量：项目资料新增「关口送审」页。真实 Edge 显示立项材料缺口，补件后送审，从项目接口重读到 `charter_review`，刷新后台账和评审记录仍正确。不是完整人类评审流程，未测评分、裁决或真实试点。

## 2026-11-21 0026 组织账号

源条款：§6.1、§20.4，T02/T04/T05/T07 的账号范围；[ADR 0014](../adr/0014-organization-account-lifecycle.md)。基准 SHA `b42aaa3865f5ab68dc41d478dcf267ef58bb3321`，被测源码树 `7d93623c9fffe94aa6629909c111b3ad94968a2f`（不含证据文档）。实现：`pmo/org_accounts.py`、`pmo/org_invitations.py`、`pmo/outbox.py`、`web/src/OrgDirectory.vue` 和迁移 `0026`。环境为本机 Windows、Python 3.12、隔离 PostgreSQL、真实回环 HTTP/Edge、合成账户，非业务签核。

| 实际命令/证据 | 退出码 | 结果 |
|---|---:|---|
| `scripts/dev.ps1 test`，`.local/org-0026-full.log` | 0 | PASS：113 项，含目录/账号独立授权、一次性邀请、跨组织与 Agent 拒绝、同键冲突、并发、回滚、停用与凭据撤销；全量含带 0016 数据升级、私有附件联合恢复及分权，见 `tests/test_org_accounts.py`、`tests/test_runtime.py`、`tests/test_database_roles.py`、`tests/test_recovery.py`、`tests/test_outbox.py` |
| `scripts/dev.ps1 browser`，`.local/org-0026-edge.log` | 0 | PASS：10 项，含独立管理员签发、未登录接收、合成账户登录、停用及旧会话失效，见 `web/e2e/organization.mjs` |
| `scripts/dev.ps1 check`，`.local/org-0026-first-check.log` | 0 | PASS：规格三检、mypy 76 个模块；静态规格不计产品验收 |
| `npm.cmd --prefix web run build`，`.local/org-0026-build.log` | 0 | PASS：初次标签参数类型错误修复后通过 Vue 类型与生产构建；仍有包体告警 |
| `python -m alembic current` / `python -m alembic heads`，本轮工具回读 | 0 | PASS：共享库仍为 `0016`，源码单 head `0026`；未对共享库执行升级 |

真实 HTTP OpenAPI 由 `tests/test_runtime.py::test_runtime_has_all_baseline_methods_paths_and_tool_mappings` 对冻结的 95 个操作按 operationId、HTTP 方法与路径比较，同时检查 MCP 的 REST 映射及注册；另计 29 项工程接口。权限和副作用仍须按对应操作单独验证，目标环境组织配置和交付为 NOT_RUN。
