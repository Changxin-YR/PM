# M2 关口与规则：未整体验收

2026-11-20：已在真实事务中检查里程碑上限、草稿冻结保护和并发版本冲突；这是规划数据入口的约束，不是三道关口运行。`tests/test_requirements.py` 人工布置的冻结数据也不是正式批准基线。规格自检的 102 个向量不计产品测试。

2026-11-20 增量：三关口预检、只读准备与评审记录查询已接入。真实 HTTP 送审进入 `charter_review`、`feasibility_review`；后者的上游 `designing` 是隔离夹具，需求基线本身经两个独立 HTTP 会话签署。隔离库从 `delivering` 实测客户/里程碑/阻塞风险缺口、金额与人天 ±20% 边界和正式对账缺口；即使逐笔核验全部完成，仍拒绝结项送审。冻结快照、名单和阶段迁移由同一事务写入，验证并发及 Outbox 故障全回滚。

未验证任何评审通过边、完整 LR-01/LR-07、正式风险接收和正式对账。评分、复议、终裁、其他阶段边和整期规则仍待开发；不标记 Q1/Q5、T03/T04/T07/T10 或完整 M2 为 PASS。详情见 ADR 0010 与 M0 实测记录。

## 2026-11-20 立项及落地人工评审（0017）

规格 §4.1–4.3、§20.4–20.5、LR-01、LR-02、A01/A14；起点 `862249db8fe7b331b0c783fce6631126c50e47f3`，验证对象为该提交上的 0017 评审批次（与本段同批提交）。实现位置：`pmo/review_scores.py`、`pmo/review_outcomes.py`、`pmo/reviews.py`、`migrations/versions/0017_review_decisions.py`、`web/src/ReviewWorkbench.vue`。环境：Windows、真实 PostgreSQL 16.14 随机隔离 schema、FastAPI HTTP、不同合成自然人会话，无模型、无业务签核。

- `python -m pytest -q tests/test_reviews.py tests/test_runtime.py tests/test_database_roles.py`：PASS，退出 0，15 项。覆盖真实送审、评分、复议、签认、裁决、进入方案细化及执行、确切边界/展示舍入、独立性、Agent 和无权管理员、材料过期、撤回重送、低分任务去重、并发/同键重放、Outbox 故障回滚。
- `scripts/dev.ps1 browser`：PASS，退出 0，真实 Edge 1 项；合成立项由不同会话送审与评分，裁决进入方案细化，整页刷新重读理由与历史；断网重试保持相同正文、版本和幂等键。
- `scripts/dev.ps1 check` 和 `npm.cmd --prefix web run build`：PASS，退出 0；规格静态核验独立于产品验证，主包体积警告仍在。
- 初次 `pytest -q tests/test_reviews.py tests/test_runtime.py`：FAIL（8 通过、1 失败，退出 1）；PM 同时以人和 Agent 送审时，`audit_logs.delegated_by` 外键锁与账号锁发生死锁。更换为 `FOR NO KEY UPDATE` 后，竞态重测改为一成一拒。一次全量运行：FAIL，退出 1；捕获补强任务空期限的 DTO 重放错误及正在开发的对账测试表未接入；前者已修复、定向重验 PASS，后者在后续迁移批次验证。

结论仅覆盖此处测得的子项，不把 Q/T、全部 LR-01、目标 Host 或政策签核置为 PASS。

## 2026-11-20 对账、风险、结项、复盘与归档（0018–0019）

源条款：主方案 §4、§5.3、LR-07/08、§20.4–20.5，见 [ADR 0011](../adr/0011-closing-evidence-and-lifecycle.md)。基准源码 SHA `5555fac130ce18e8325f3459404ad057072775e5`，测试批次源码树 `4d958feb78d0ed4e7536750dddc8ca4e1ff1c04c`（仅进度文本不在该树）。环境：Windows、回环 HTTP、PostgreSQL 16.14 随机合成 schema、私有附件、本地真实 Edge 157 和 Python MCP SDK；未启用模型，未使用真实业务签核。

| 命令 | 结果 | 实测内容及证据位置 |
|---|---|---|
| `scripts/dev.ps1 test` | PASS，退出 0；67 项 / 182.14 秒 | `tests/test_lifecycle.py`：合成普通项目真实 HTTP 从草稿到归档，阶段由业务接口推进；重报与终止复盘独立。另见 `tests/test_cost_reconciliation.py`、`tests/test_risk_resolution.py`、`tests/test_retrospectives.py`、`tests/test_runtime.py`、`tests/test_database_roles.py`。 |
| `scripts/dev.ps1 browser` | PASS，退出 0；2 项 / 111.45 秒 | `web/e2e/lifecycle.mjs`：浏览器流转交付、独立风险关闭、对账、结项、四问、独立 Lesson 与归档，刷新读回理由和只读状态；前置需求/来源由真实 API 准备。`web/e2e/draft.mjs`：原草稿、台账与人工立项。 |
| `python -m pytest -q tests/test_human.py tests/test_recovery.py tests/test_retrospectives.py` | PASS，退出 0；8 项 / 98.17 秒 | 独立库带 0016 项目及里程碑原件升级至 0019，新增项目对账来源附件后真 `pg_dump/pg_restore` 恢复并读取两项原件字节及关联；真实限流、证据复查和复盘事务也通过。 |
| `scripts/dev.ps1 check`、`npm.cmd --prefix web run build` | PASS，退出 0 | 静态参考检查和 mypy、Vue 类型与构建；主包仍有体积警告。 |

失败与根因记录：初次复盘专项 FAIL（4 失败），合成夹具未同时写入 0019 规定的流转申请，已修正后通过。初次综合回归 FAIL（57 通过、9 失败于夹具登录阶段），同源 IP 测试跨用例累计触发真实限流；只在随机隔离 schema 的用例之间推进该 IP 的窗口，新增真实 IP 与账户限流断言，67 项重验通过。Edge 发现脚本语法、更新期间主动关闭、重名按钮和嵌套定位错误，修正后定向重验通过。

阶段边界：正常合成闭环通过，未覆盖需求书面例外、全部终止边及 LR-08 定时通知。真实非零承诺仍阻断；金额、人天和风险绝不受需求例外豁免。

## 2026-11-21 具体需求书面例外（0020）

源条款：§4.4、§6、§19.4、§20.5，DEC-09/26、LR-07、A07。合成默认：独立客户代表以认证会话签署（必须有对应客户联系身份），另一个仍在任的 Reviewer 核验版本、偏差、范围和有效期完全一致的 PDF 原件；回执和撤销事件只追加。条件裁决与普通裁决互斥；结项快照和 Decision 持久化具体偏差及证据哈希，不改需求实际确认状态，不生成落地补强任务。其他结项门槛保持独立。产线仍 `POLICY_PENDING`。

基准 SHA `487d96f5999924239fc2a8ece2361d7b8cef3a09`，本批被测源码树 `fd5fd041001f316dbbea95f98aa4dcb0d01447c2`（不含后续周报/任务及进度文本）。位置：`migrations/versions/0020_requirement_exceptions.py`、`pmo/requirement_exceptions.py`、`pmo/gates.py`、`pmo/review_outcomes.py`、`web/src/RequirementExceptions.vue`。环境：Windows、回环 HTTP、PostgreSQL 16.14 随机隔离 schema 和临时私有原件、Edge、不同合成账号，无模型和业务签核。

| 实际命令（测试数据库由开发脚本同样的本地配置注入） | 结果 | 证据 |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest -q --tb=short tests/test_requirement_exceptions.py tests/test_lifecycle.py tests/test_reviews.py tests/test_database_roles.py` | PASS，退出 0，20 项 / 139.53 秒 | 真实 HTTP 条件结项和独立否决路径，撤销/过期/改动原件后裁决失败，撤回重送，同键只写一次，金额/人天超限、非零承诺和未接收风险不能送审；API/Outbox 分账号保护。 |
| `.venv/Scripts/python.exe -m pytest -q --tb=short tests/browser_case.py::test_browser_customer_exception_and_conditional_decision` | PASS，退出 0，1 项 / 51.94 秒 | `web/e2e/lifecycle.mjs`：不同账号签署、送审、核验偏差、条件裁决、刷新读回。前置对账/关闭风险和原件准备走 HTTP。 |
| `scripts/dev.ps1 check`；`npm.cmd --prefix web run build` | PASS，退出 0 | 静态规格检查、mypy、Vue 类型、Vite 构建；主包大小警告仍存在。 |

失败记录：首次例外专项 FAIL（5 项通过、1 项失败，退出 1），测试把文本型审计请求号按 UUID 绑定；改按文本比较。首次组合回归 FAIL（19 项通过、1 项失败，退出 1），超支夹具未按对账规则提供归因；补真实请求中的归因，未放松产品门槛。初次类型检查 FAIL，解决附件参数和在制周报类型错误后复验通过。最终候选仍需针对现有数据和新增附件引用联合恢复。

## 2026-11-21 报告、任务及原评审终止（0021–0022）

源条款：§5.6 周报周期及 LR-06 输入、§6、§7.2、§8.4、§20.4–20.5；A16。实现：`pmo/reports.py`、`pmo/tasks.py`、`pmo/project_lifecycle.py`、`migrations/versions/0021_report_receipts.py`、`migrations/versions/0022_review_termination.py`。基准 `bba0ca095558e38b48ed38810ad67332a98e8683`，最终被测源码树 `61b80177c5950e5c5708e30f15cc5c1ad9380557`（不含随后进度文本）。Windows、PostgreSQL 16 隔离 schema、回环 HTTP、Python MCP SDK、Edge；合成角色，无模型和真实签核。

| 实际命令 | 结果 | 证据 |
|---|---|---|
| `scripts/dev.ps1 test` | PASS，退出 0，89 项 / 158.17 秒 | `tests/test_reports.py`、`tests/test_tasks.py`、`tests/test_lifecycle.py`、`tests/test_recovery.py` 等。覆盖真实报告作者及人工 PM 发布、冻结正文和版本、任务授权、显式 Agent 委托及重试、独立发起人签认与 Sponsor 裁决、暂停恢复、结项返工、并发/过期/Outbox 注入回滚、隔离角色和恢复。后续修复只触及 Web 标签与定位。 |
| `git archive` 最终源码树后运行 `.venv/Scripts/python.exe -m pytest -q --tb=short tests/browser_case.py` | PASS，退出 0，5 项 / 106.03 秒 | `tests/browser_case.py`、`web/e2e/lifecycle.mjs`。草稿、真实会话审批、交付归档、书面需求例外、报告和三账号终止各路径；原日志在 `.local/browser-0022-verified.log`。 |
| `scripts/dev.ps1 check`、`npm.cmd --prefix web run build` | PASS，退出 0 | 规格静态检查、mypy、Vue 类型及 Vite 构建；构建包体积警告仍在。 |

初次报告组合 FAIL（18 通过、1 失败），降级保护错误的断言未匹配新保护文案，已修正。浏览器回归 FAIL（1 失败、4 通过）：先捕获阶段文字重名，后发现随阶段插入「需求例外」会重排隐式标签 ID；修复选择器、稳定标签名及打开默认项，重验 PASS。未把周期扫描或真实放行写成已验。

目录通过实际 `app.openapi()` 与冻结 OpenAPI 的方法和规格化路径求差集，并与 `pmo.tool_routes.ROUTES` 对表：83/95 基线、21 项工程接口、38/40 工具。基线缺项为健康 GET/历史/申诉（3）、报告分析及 Fact 枚举/候选/核验/澄清（5）、管理员组织成员/用户列表/邀请/状态（4）；未注册 `pmo_health_get`、`pmo_fact_propose`。目录和单项调用证据分开，不代表目标 Host 兼容性。

## 2026-11-21 三线、独立申诉与规则补跑（0023）

源条款：§5.1–5.6、§8.4，LR-03/04/06/08。基准 `e4fc1e55039b8043e6196840393355e4ee320f07`；被测源码树 `35e0a6da0057c2372fc33ebccec259a771a2d1b6`（不含后补证据文档）。实现：`pmo/health_calculation.py`、`pmo/health.py`、`pmo/rule_scan.py`、`migrations/versions/0023_health_and_rule_scans.py`、`web/src/Health.vue`。环境：Windows、回环 HTTP/stdio、PostgreSQL 16.14 随机隔离 schema 或隔离专用数据库、真实 Edge；合成账号和证据，无模型、真实签核或生产修改。

| 实际命令 | 结果 | 证据 |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_health.py tests/test_runtime.py --tb=short` | PASS，退出 0；13 项 / 32.93 秒 | `.local/health-0023-targeted.log`。三线与单黄总体绿；>15% 预警、严格 >20% 重估；零预算声明和 `UNAVAILABLE`；快照与独立申诉；LR-06 连续漏报和当期进展/完成证据；LR-08 第 14 日、复盘四问与 Lesson；扫描并发去重和事务回滚；SDK 与 REST 对表。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_database_roles.py tests/test_recovery.py --tb=short` | PASS，退出 0；3 项 / 25.81 秒 | `.local/health-0023-roles-recovery.log`。非所有者扫描单次生成 LR-06 通知，Outbox 独立投递，权限和历史防改；升级与联合恢复见 [M4](M4-report.md)。 |
| `scripts/dev.ps1 test` | PASS，退出 0；97 项 / 182.74 秒 | `.local/health-0023-full.log`。共有一项 Alembic `path_separator` 弃用警告。 |
| `scripts/dev.ps1 browser` | PASS，退出 0；真实 Edge 6 项 / 121.30 秒 | `.local/health-0023-edge.log`；新增 `web/e2e/health.mjs`，两个独立会话申诉/复核，刷新和移动视口切换，另五条既有路径回归。 |
| `scripts/dev.ps1 check`；`npm.cmd --prefix web run build` | PASS，分别退出 0 | `.local/health-0023-check.log`、`.local/health-0023-build.log`。规格、mypy、Vue 和 Vite；仍有主包体积警告。 |

初次定向回归 FAIL（退出 1；旧降级保护、工具集合断言未同步），与首轮 Edge FAIL（退出 1；新增用例夹具缺 `data` 和 `headers` 导入）原日志 `.local/health-0023-first.log`、`.local/health-0023-browser.log`；修正后按上表重验。

目录校验（`app.openapi()` 与冻结 YAML 的方法、路径和 ID；`pmo.mcp_server.tools` 与 `ROUTES`）：PASS，退出 0，**86/95 基线 REST + 24 工程接口；39/40 注册工具**。缺报告分析与 Fact 5 项、组织管理 4 项，唯一未注册 `pmo_fact_propose`。逐项调用及目标 Host 验收另记。获批新增需求、客户正式争议入账、零预算结项仍未验证；本次不标记 Q/T、八规则全部组合或政策为通过。

## 2026-11-21 获批新增需求

源条款：§5.3–5.4、§5.6 LR-05、§6.2、Q8/T05/T07，见 [ADR 0016](../adr/0016-approved-requirement-additions.md)。基准 SHA `abf460d6bb0b245e4540cbc347f9c348fadbb2b9`；全量被测树 `4bf9814af8c17dac843e1e68d78b651fa1fe9659`，增量扫描补充后的被测树 `255e47d330730c03a12032d88deef09a9e8dde5a`（均不含后补证据文档）。实现：`pmo/change_requests.py`、`pmo/change_decisions.py`、`pmo/requirement_additions.py`、`pmo/scope_quality.py`、`pmo/requirement_delivery.py`、`web/src/Changes.vue`；无新迁移。Windows/隔离 PostgreSQL/真实 HTTP、Edge，合成身份。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_requirement_additions.py tests/test_change_requests.py tests/test_change_history.py tests/test_scope_quality.py tests/test_requirement_baselines.py tests/test_lifecycle.py tests/test_weekly_drafts.py` | 0 | PASS：13 项，`.local/addition-affected-first.log`。预留 ID 不在需求清单/分母，独立授权、同键异参、自批/Agent/跨项目拒绝，并发批准一次，Outbox 故障事务完整回滚，初始签名不篡改，新增 P1 客户确认后满足率精确为 `2/5`。 |
| `pwsh -NoProfile -File scripts/dev.ps1 test` | 0 | PASS：117 项 / 190.22 秒，`.local/addition-full.log`（增量队列补充前）。 |
| `pwsh -NoProfile -File scripts/dev.ps1 browser` | 0 | PASS：Edge 12 项 / 157.52 秒，`.local/addition-edge.log`。桌面 PM 发起，独立 Reviewer 移动视口批准，刷新真实清单、台账一致，`web/e2e/requirement-additions.mjs`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_requirement_additions.py tests/test_health.py::test_incremental_scan_lr04_lr03_dedupe_and_transaction` | 0 | PASS：3 项 / 17.34 秒，`.local/addition-scan.log`。加入与既有变更相同的扫描标记后重新验证 LR-05、并发和现有扫描。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`、`npm.cmd --prefix web run build` | 0 | PASS：规格三检、mypy、Vue 类型和构建，日志 `.local/addition-check-first.log`、`.local/addition-build.log`；包体告警仍在。 |

新入口是工程补充，基线 95 和 MCP 注册 40 不变。DEC-07 继续待真实签核；合成客户确认不代表真实客户签收。
