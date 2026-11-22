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

## 2026-11-21 零预算结项

源条款：§4.4、§5.2、LR-07。实现：`pmo/gates.py`，复用 `pmo/costs.py` 按有效预算基线版本匹配的人工声明。仍需已核对的独立对账、实际净额和剩余预测均为零，并满足其余结项门槛；声明 ID 及内容进入评审快照。无新迁移。基准 SHA `2d040e33544e9ec6bac7105eabcef3f99ffccbf1`，被测源码树 `59965651b90e4e815883ddaf816e816483f4fed3`（不含证据文档）。Windows / PostgreSQL 16 随机隔离 schema / 回环 HTTP；合成身份、固定对账来源、模型关闭。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_zero_budget_closing.py tests/test_cost_reconciliation.py tests/test_lifecycle.py tests/test_health.py --tb=short` | 0 | PASS：19 项 / 54.28 秒，`.local/zero-budget-targeted.log`。无声明拒绝、绑定声明后仍需对账、0.01 人天实际拒绝、非零预测拒绝且使旧对账失效、同步生成可核验的声明引用并经独立裁决进入 `closed`。 |
| `./scripts/dev.ps1 check` | 0 | PASS：冻结规格静态三检与 mypy，`.local/zero-budget-check.log`。 |

共享开发库未迁移。零预算路径通过不代表 DEC-09/16/26 已签核。

## 2026-11-21 正式客户争议（0028）

源条款：§5.3–5.4、§5.6 LR-07、Q2/Q5/T07，见 [ADR 0017](../adr/0017-customer-requirement-disputes.md)。实现：`pmo/requirement_disputes.py`、`pmo/scope_quality.py`、`pmo/health_calculation.py`、`pmo/gates.py`、`web/src/RequirementDisputes.vue`。基准 SHA `761ab55322fb8507fd67f8b0bc21a897c13a9449`，受影响测试和恢复的源码树 `5716e89118361a6ac603ae1bf971c30a7c09f640`，不含随后进度文本。Windows / PostgreSQL 16 隔离 schema 与独立恢复库 / 回环 HTTP / Edge；合成身份及陈述，模型关闭。

| 命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_requirement_disputes.py tests/test_scope_quality.py tests/test_requirement_additions.py tests/test_health.py tests/test_database_roles.py --tb=short` | 0 | PASS：14 项 / 53.83 秒，`.local/disputes-0028-targeted.log`。P1 正式争议触发红灯、有争议的确认不计权重；客户版本绑定，PM/Reviewer/Agent/跨项目拒绝，重复键只写一次，故障原子回滚，结项提交后旧快照拒绝、撤回重送后通过。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py::test_browser_customer_dispute_has_independent_and_persistent_history --tb=short` | 0 | PASS：Edge 1 项 / 28.04 秒，`.local/disputes-0028-edge-first.log`、`web/e2e/requirement-disputes.mjs`。移动客户与桌面 PM 独立会话、原文和撤回后刷新回读。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_recovery.py tests/test_database_roles.py --tb=short` | 0 | PASS：3 项 / 37.59 秒，`.local/disputes-0028-recovery.log`。0016 带数据升级到 0028、降级保护、真实导出恢复原件及争议历史、运行角色隔离。 |
| `./scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：规格静态三检、mypy、Vue 类型；`.local/disputes-0028-check.log`。 |

目录检验由冻结 YAML 和运行代码的 `app.openapi()` 方法、路径、操作 ID 求差集，并核对注册映射：PASS，95/95 基线 REST、34 项工程接口、40/40 注册，未声称实际逐工具调用或 Host 验收。真实客户写入及 DEC-07/09 仍未放行；共享开发库未迁移。

## 2026-11-22 LR-08：逾期补跑与月报

源条款：§5.6 LR-08、§14 第 1 项、T10 子项。`pmo/rule_scan.py` 以正常关口裁决为起点，按上海时区第 14 个自然日归属月报并记录实际发现时点。跨月扫描时，只对仍缺四问或已确认 Lesson 的项目催办 Sponsor；若已逾期补齐，记录为已解决，不发迟到提醒。`pmo/alerts.py` 和 `web/src/Alerts.vue` 使用同一口径。不改迁移、归档门槛或政策。

基准 `1c1431819af0b83b55243eb47eeea56ec460843c`；定向和浏览器测试源码树 `9cb15fc2ac58db326f72826453eadbfcb7987bab`；含 MCP 综合回归源码树 `5a68370e4df7f7d3e01346b8c09c59857572e0b5`（不含后续证据文本）。Windows、PostgreSQL 16 隔离 schema、回环 HTTP、Edge；合成账号，模型关闭。

| 命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_health.py -k lr08 --tb=short` | 0 | PASS：3 项，`.local/lr08-month-retest.log`。验证提醒、归档、月界补跑、补齐后不再催办和重试去重。 |
| `pwsh -NoProfile -File scripts/dev.ps1 browser` | 0 | PASS：Edge 14 项 / 175.91 秒，`.local/lr08-month-edge.log`。新增 `web/e2e/lr08-monthly.mjs`，核对截止月份、实际发现时点和项目筛选。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`、`npm.cmd --prefix web run build` | 0 | PASS：规格、mypy、Vue 类型和构建，`.local/lr08-month-check.log`、`.local/lr08-month-build.log`；仍有包体积警告。 |
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/mcp-calls-0028.json -q --tb=short` | 0 | PASS：124 项 / 201.65 秒，`.local/0028-full-with-mcp.log`。不代表 T10 组合验收。 |

初次全量回归 FAIL（120 通过、3 失败），修复测试使用的项目可见身份和 0028 降级保护文案后复验通过；未放宽项目隔离。共享开发库仍在 0016。

## Q4 正式立项前支出

源条款：§4.2、§12.1 Q4、ADR 0005。`pmo/spending.py` 以首次生效的正式立项 Decision（通过或有条件通过）划界，按实际发生时点查询已核验正负流水。核验时间晚于决议不会抹除历史支出，待核凭证和立项后的支出不进入列表。基准 SHA `b88f83f334f0f4c497ff20470f4e742d9ea5ea4d`，本切片源码树 `ba3a59d02673af6f2779a77d7a2342bed78f4de1`；Windows、PostgreSQL 16 随机 schema、独立合成审阅人及核验人、真实回环 HTTP/stdio、无模型。同期工作区有独立的落地提醒改动，不涉及本查询。

| 命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_spending.py tests/test_postings.py --tb=short --junitxml=.local/preapproval-conditional.xml` | 0 | PASS：2 项 / 11.95 秒。正式条件通过后按发生时间截取；历史金额与人天、之后支出、候选、未授权人员和 SDK 枚举同一个列表；凭证独立核验、并发、冲销回归。 |

修复前 `.local/preapproval-conditional-first.xml` 记录 FAIL（退出 1）：正式条件通过后发生的已核验流水被错误纳入。未更改立项门槛或支出记录。

## 落地条件任务逾期提醒

源条款：§4.3、§5.6 LR-02。`pmo/rule_scan.py` 仅检查正式落地条件通过中绑定的任务；评审来源的手工任务或否决整改不混入。期限严格越界后通知在任 PM，按任务与期限去重；改期后关闭旧提醒，完成后关闭当前提醒，取消任务不等于履行已批准条件。`pmo/alerts.py` 和 `web/src/Alerts.vue` 提供人类详情，Agent 仍使用封闭的预警 DTO。提醒不修改三线与阶段。无新迁移。

基准 SHA `ae2272b1dba895064089682183c8b5d3252b773f`，被测本切片源码树 `9db58af0fcecac0967869ed27f329cfebe90f4f9`，不含后补文档。Windows、PostgreSQL 16 随机 schema、合成独立账户/任务、真实 HTTP/stdio、模型关闭。在真实项目服务记录正式通过后，只在隔离测试中向扫描器传入合成截止时点。

| 命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_feasibility_reminders.py tests/test_health.py tests/test_tasks.py tests/test_database_roles.py --tb=short --junitxml=.local/feasibility-reminders-retest.xml` | 0 | PASS：17 项 / 49.46 秒。正好到期不催办，逾期仅匹配正式条件，三线完全一致，改期/取消/完工、Outbox 双跑、当前 PM 站内回执和 MCP 输出范围实测。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：三套冻结规格检查、mypy 81 文件（`.local/feasibility-reminders-check.log`）、Vue 类型。 |

首次同一套件 FAIL（退出 1，16 项 PASS、1 项 FAIL，`.local/feasibility-reminders.xml`）：测试把未确认 P0 造成的既有红线误认为提醒逻辑造成，修正为到期前后全部三线结果一致；不放宽规则。
## 0030 结项撤回按冻结状态表回到执行

依据：§4.1、§4.5、§20.4–20.5、LR-01/07。基准 `5d196790dcb063a45fd7c38a2479ff3d84aef051`，被测源码树 `765a9f99738838659a7931f82b85ead27792094a`（不含补录文档）。实现：`pmo/reviews.py::withdrawal_stage`、`pmo/review_outcomes.py::write_action`、`0030_closing_withdrawal_edge.py`、`web/src/ReviewWorkbench.vue`。Windows、真实回环 HTTP、PostgreSQL 16.14 隔离 schema/专库、Edge、合成角色，无模型或签核。历史 `closing_review→delivering` 保持只读，不补造裁决或重写阶段记录。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_requirement_disputes.py::test_dispute_changes_frozen_closing_basis_and_is_atomic --tb=short --junitxml=.local/lifecycle-edge-first.xml` | 1 | FAIL：旧版返回 `delivering`，冻结基线要求 `executing`，`.local/lifecycle-edge-first.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_requirement_disputes.py tests/test_requirement_exceptions.py tests/test_reviews.py tests/test_lifecycle.py tests/test_database_roles.py tests/test_recovery.py tests/test_operational_checks.py --tb=short --junitxml=.local/lifecycle-0030-affected-final.xml` | 0 | PASS：30 项 / 82.25 秒，`.local/lifecycle-0030-affected-final.log`。独立角色、过期材料、重复撤回、不能直回交付/直接重送；回到交付后仍受争议/LR-07 阻断，解决后新轮次可裁决。`0016→0029→0030` 带历史行升级，PostgreSQL 拦新非法行，真实 `pg_dump/pg_restore` 联合回读同一旧记录及原件。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py::test_browser_closing_withdrawal_rework_and_fresh_submission tests/browser_case.py::test_browser_drafting_through_archive_without_model tests/browser_case.py::test_browser_customer_exception_and_conditional_decision --tb=short --junitxml=.local/lifecycle-0030-edge.xml` | 0 | PASS：3 项 / 64.56 秒，`.local/lifecycle-0030-edge.log`。撤回后刷新状态/理由、重新交付后新申请；原正常和条件结项仍可用。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：规格、Python 87 文件（`.local/lifecycle-0030-check.log`）、Vue 类型。 |

修复后首轮 `.local/lifecycle-0030-affected.log` 退出 1（29 PASS、1 FAIL）：执行中的结项预检按设计首先返回阶段不符。在重新完成交付后原断言继续检查客户争议仍阻断结项；未放宽关口或删除断言。

## 独立终止裁决与暂停项目复盘

依据：§4.1、§20.4–20.5、LR-01、A09/A14。基准 SHA `1e8e6e60623bd6c206acba282da597350749366b`；综合实测源码树 `8957c1e2dc493e0aba383432660c11b9c19e827c`，仅增加终止归档断言后的树为 `9a9b7a5eaa842cffdf3e97ed4718d7b8f71f9aaa`。实现 `pmo/project_lifecycle.py::propose`：同一轮次保留 Sponsor 自提申请，但不让一条 Sponsor 无法自批的申请挡住独立 PM 作同一申请；本人的重复申请仍冲突。审批权、终止同意及政策未签核限制均未改变。Windows、PostgreSQL 16.14 随机 schema、回环 HTTP/stdio、合成独立账户，模型关闭，无业务签核。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_lifecycle.py -k termination_pending_from_sponsor --tb=short --junitxml=.local/lifecycle-authority-first.xml` | 1 | FAIL：执行中和暂停中，独立 PM 的申请均被错误返回 `TRANSITION_REQUEST_PENDING`。`.local/lifecycle-authority-first.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_lifecycle.py tests/test_mcp_transition.py --tb=short --junitxml=.local/lifecycle-authority-affected.xml` | 0 | PASS：9 项 / 27.70 秒，`.local/lifecycle-authority-affected.log`。回读独立终止、重复与跨项目拒绝、Agent 不能批准、暂停恢复、落地返工和交付返工的权限与落库记录。 |
| `.venv/Scripts/python.exe -m pytest -q 'tests/test_lifecycle.py::test_termination_pending_from_sponsor_does_not_block_independent_pm_request[on_hold]' --tb=short --junitxml=.local/rc0030-terminated-archive.xml` | 0 | PASS：1 项 / 12.54 秒，`.local/rc0030-terminated-archive.log`。`on_hold→terminated→retrospective→archived` 保留终止类型和四问，缺答复或确认 Lesson 时拒绝；有权独立人归档，阶段历史无 `closed`。 |

## 裁决审计关联与暂停、恢复权限

源条款：§6.2–6.3、§20.4–20.5、LR-01。基准 SHA `14d6a0b7f39b580d0928ba46c0360769180105a8`，通过 Edge 的源码树 `b838686f61f948b7b8fc475f226ae30ebecb5299`；后端运行时的提交内容相同，仅浏览器断言随后追加。关口和非关口裁决在原事务内写 `audit_logs.decision_id`，正式回执和审计带裁决来源，Outbox 与裁决快照共享编号；管理员按组织和请求号读取。未补造历史审计，未改迁移、权重、审批角色或签核状态。实现 `pmo/review_outcomes.py`、`pmo/project_lifecycle.py`、`pmo/projects.py`、`pmo/audits.py`、`web/src/AuditLog.vue`。Windows、PostgreSQL 16 隔离 schema、回环 HTTP/stdio、Edge，合成账号，无真实签核。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_reviews.py::test_charter_atomic_decision_acl_replay_and_concurrency tests/test_lifecycle.py::test_pause_resume_checks_sponsor_deadline_and_stale_revision --tb=short --junitxml=.local/decision-audit-first.xml` | 1 | FAIL：两项，分别复现关口回执没有裁决引用、暂停裁决的组织审计没有 `decisionId`。`.local/decision-audit-first.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_reviews.py tests/test_lifecycle.py tests/test_admin_audit.py tests/test_mcp_transition.py tests/test_requirement_disputes.py tests/test_requirement_exceptions.py tests/test_runtime.py --tb=short --junitxml=.local/decision-audit-verified.xml` | 0 | PASS：37 项 / 62.67 秒，`.local/decision-audit-verified.log`。挡住 PM 自批、Reviewer 和同项目管理员批准暂停与恢复；独立 Sponsor 正例；回读 Decision/审计/Outbox 同号，失败未留下半条业务记录。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py::test_browser_draft_workflow tests/browser_case.py::test_browser_drafting_through_archive_without_model --tb=short --junitxml=.local/decision-audit-edge-retest.xml` | 0 | PASS：2 项 / 75.85 秒，`.local/decision-audit-edge-retest.log`。立项后重登和刷新仍可经请求号查裁决 ID；正常无模型归档回归。初次同组选例 1 FAIL / 1 PASS（`.local/decision-audit-edge.log`，退出 1）：新增检查使用已经正常登出的管理员页；显式重新登录后保留原断言。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：规格、Python 和 Vue 类型检查；`.local/decision-audit-check.log`、`.local/decision-audit-vue.log`。 |

## 待审申请的发起权限复核

源条款：主方案 §20.4–20.5、领域不变量 §4、LR-01。基准 SHA `eb3121c20eddaadc6f6a6d7f73e2c81dd23146f2`，修复前新增反例文件 blob `5d82cacc790a5f2b727e8a85f83674aff2f4e1f4`，被测修复源码树 `9044fc9f438e3961285210f74b747e3e7c6a8fbc`（不含证据文档）。Windows、PostgreSQL 16.14 隔离 schema、回环 HTTP 和 stdio；合成账户，模型关闭。`pmo/gates.py` 的送审资格以实际项目/组织内的自然人查有效任命，供 `pmo/agent_tokens.py`、`pmo/reviews.py` 和 `pmo/review_outcomes.py` 共用。终裁重查已存申请人，Agent 送审则重查其委托人；失权判原轮次过期。由现任 PM 撤回并重新送审，保持旧评分与撤回历史。没有新增审批权或更改迁移。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_reviews.py -k pending_charter_cannot_be_decided --tb=short --junitxml=.local/review-submitter-first.xml` | 1 | FAIL：2 项，`.local/review-submitter-first.log`。发起人任命撤销后，人工及 Agent 提交的旧 Review 均被错误终裁，返回 200。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_reviews.py tests/test_lifecycle.py tests/test_mcp_transition.py tests/test_reference_vectors_permissions.py tests/test_requirement_exceptions.py --tb=short --junitxml=.local/review-submitter-verified.xml` | 1 | FAIL：3 项 / 47 PASS，`.local/review-submitter-verified.log`。资格函数重构后令牌签发别名仍用旧参数，修正调用点。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_reviews.py tests/test_lifecycle.py tests/test_mcp_transition.py tests/test_reference_vectors_permissions.py tests/test_requirement_exceptions.py tests/test_agent_tokens.py --tb=short --junitxml=.local/review-submitter-retested.xml` | 0 | PASS：52 项 / 93.33 秒，`.local/review-submitter-retested.log`。两路失权均 `409 REVIEW_SNAPSHOT_STALE`；不创建 Decision、终裁快照、迁移、成功审计、Outbox 或幂等记录，拒绝审计单独可核；有效 PM 撤回重送、独立 Reviewer 裁决到 `designing`。 |

## 0032 暂停、恢复与终止的独立组织授权

依据：主方案 §4.1、§20.4–20.5、LR-01。实现：`pmo/project_lifecycle.py`、`pmo/database_roles.py`、`migrations/versions/0032_transition_authority.py`、`web/src/ProjectLifecycle.vue`、[ADR 0019](../adr/0019-project-transition-authority.md)。基准 SHA `a03ffea90cc01643c84e48bbc36f9baabb78c922`，最终被测源码树 `7838c3021f8bb500f62dd82468cfbc2021a8c549`（不含本段/矩阵）。Windows、PostgreSQL 16.14、回环 HTTP/stdio、Edge 158.0.4370.33，合成账号，模型关闭。迁移不补授真实权限，记录仍须带外授权，隔离 schema 外保持 `POLICY_PENDING`。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_transition_authority.py -k 'non_sponsor_can or storage_enforces' --tb=short --junitxml=.local/rc0032-authority-reproduced.xml` | 1 | FAIL：在基准服务上仅增授权迁移和反例，有效授权人仍 `canDecide=false`（1 PASS、1 FAIL），`.local/rc0032-authority-reproduced.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_transition_authority.py tests/test_lifecycle.py tests/test_mcp_transition.py tests/test_database_roles.py tests/test_recovery.py tests/test_runtime.py --tb=short --junitxml=.local/rc0032-authority-verified.xml` | 0 | PASS：28 项 / 134.88 秒，`.local/rc0032-authority-verified.log`。普通成员按边批准；无授权、他项目、错边、到期、未生效、撤销、失去参与任命、管理员、本人/委托人自批和 Agent 禁权。并发撤销持锁的真实写事务先提交，审批等锁后拒绝；同/不同键并发裁决只生效一次。落地发起人确认必需；Outbox 注入失败回滚 Decision、状态、历史、审计与回执。运行账号不能增改授权。旧账 `0016→0032` 带数据升级及联合恢复；同时保留有效/已撤销授权和三份原件，旧版降级保护与 `0032` 保护各自实测。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py -k 'formal_transition_authority or independent_termination' --tb=short --junitxml=.local/rc0032-authority-edge.xml` | 0 | PASS：2 项 / 145.60 秒，`.local/rc0032-authority-edge.log`。普通成员受权暂停、恢复和终止；Reviewer 未授权不显示操作；刷新状态、理由、权限和组织来源一致。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：冻结规格与类型，`.local/rc0032-check-first.log`、`.local/rc0032-vue.log`。 |

过程反例保留：`rc0032-authority-before`（FAIL，漏填两处夹具必填字段；修夹具后重现原权限问题）、`rc0032-authority-first`（FAIL，并发新键各有唯一拒绝关联 ID，测试应检查冲突而非把拒绝当成功重放）、`rc0032-authority-api`（FAIL，新降级保护遮住旧保护的探针，把授权夹具移到旧保护断言后）。均退出 1，未移除权限和恢复断言。

## 阶段申请的有效重复判定与 Web 选项

依据：§20.5、LR-01、领域不变量的受控迁移与独立批准。基准 `9e62c2ea87126acfa8d235c58c6f749777592ad4`，被测源码树 `6f934ad47aa12d51614be099a913dec2a2cfab5d`（不含补录）。`pmo/project_lifecycle.py::competing_request` 供列表和写入共用；只有同状态边、当前版本、申请人仍有权且材料/负载匹配的申请会占用待审位置。既有 Sponsor 自提申请不会遮住独立 PM；过期申请保留可读、不可裁决。`ProjectLifecycle.vue` 只使用服务端选项，提交仍做幂等与版本检查。Windows、PostgreSQL 16.14 隔离 schema、Edge 158.0.4370.33，回环 HTTP/stdio，模型关闭、合成账号。迁移及附件引用不变。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_lifecycle.py -k 'stale_termination_request or termination_pending_from_sponsor' --tb=short --junitxml=.local/rc0032-application-before.xml` | 1 | FAIL：仅增测试，列表未裁剪申请人的有效重复；停用 PM 后，旧待审错误阻断新申请，3 项，`.local/rc0032-application-before.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py -k formal_transition_authority --tb=short --junitxml=.local/rc0032-application-edge-before.xml` | 1 | FAIL：旧 Web 在 Sponsor 先提后丢失 PM 可用的终止选项，`.local/rc0032-application-edge-before.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_lifecycle.py tests/test_transition_authority.py tests/test_mcp_transition.py tests/test_reference_vectors_permissions.py tests/test_runtime.py --tb=short --junitxml=.local/rc0032-application-verified.xml` | 0 | PASS：48 项 / 78.51 秒，`.local/rc0032-application-verified.log`。执行与暂停两边重复判定、停用后旧申请仍在但不可裁决、新申请可独立终止、MCP 只提不批。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py -k 'formal_transition_authority or drafting_through_archive_without_model or independent_termination' --tb=short --junitxml=.local/rc0032-application-edge.xml` | 0 | PASS：3 项 / 75.83 秒，`.local/rc0032-application-edge.log`。Edge 独立申请、授权裁决与刷新回读，正常无模型归档和发起人确认回归通过。 |

## 立项与落地评分嵌套 REST 契约

依据：§4.2–4.3、§20.1、OpenAPI 评分基线、工程 DTO 补充 §1 和 §7、[ADR 0010](../adr/0010-review-submission.md)。基准 SHA `60842998920abf4b16e18a1b9a2ba2e40bb42be1`、被测源码树 `4ad0a46cef829cd13792d3c14501fb3d69369985`（不含补录）。`pmo/review_scores.py` 和 `pmo/review_outcomes.py` 发布五个分值对应的锚点条件、证据引用去重和两种固定维度集合，不改历史或规则。Windows、真实回环 HTTP 与隔离 PostgreSQL 16.14、合成账号。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_reviews.py -k runtime_review_score_contract --tb=short --junitxml=.local/rc0032-score-contract-before.xml` | 1 | FAIL：仅增测试，两关口的发布 Schema 都误收额外历史维度，但 HTTP 写入已拒绝非法数据，`.local/rc0032-score-contract-before.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_reviews.py tests/test_requirement_exceptions.py tests/test_reference_vectors_governance.py tests/test_postgres_invariants.py tests/test_runtime.py --tb=short --junitxml=.local/rc0032-score-contract-verified.xml` | 0 | PASS：52 项 / 77.03 秒，`.local/rc0032-score-contract-verified.log`。同一真实写入与历史回读通过发布的 Schema，三种非法评分同时被 HTTP 与 Schema 拒绝；无多余评分或版本推进，复议/阈值/权限/结项例外及数据库保护保持。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check` | 0 | PASS：三套冻结规格校验及 mypy 87 源文件，`.local/rc0032-check-final.log`。 |

## 23 条阶段边的角色和事务回执

依据：主方案 §4.1、§20.4–20.5、LR-01、T03/T04/T07。基准 `efa0f3a332bb4635d53624005268a8a07c790311`，被测源码树 `9105757dce6d457f7c59e5cbc360031313b61440`（不含补录文档）。Windows、隔离 PostgreSQL 16.14、回环 HTTP/真实 stdio、合成账号，关闭模型。`tests/test_stage_edges.py` 不用 SQL 推阶段，通过真实送审、评分、获批计划、附件与结项材料准备合法阶段。

| 场景 | 实际走到的边 |
|---|---|
| `test_normal_and_rework_edges_have_role_bound_receipts` | 15 条：三次入关；立项批准/方向调整，落地批准/退回，结项批准/撤回；暂停、恢复、进入交付、交付返工、正常复盘、审核归档。 |
| `test_formal_rejection_and_one_retry_have_role_bound_receipts` | 新覆盖 4 条：立项拒绝、一次重报、再拒后终止、终止复盘。 |
| `test_other_termination_edges_have_role_bound_receipts` | 新覆盖 4 条：落地评审（需发起人确认）、执行、暂停、结项评审分别终止，评审中终止验证原评审与 Decision 关联。 |

逐边用无权人工及适用的 Agent 直调验证拒绝和独立 denied；合法执行核对前后 revision、真实主体、审批状态、材料哈希、适用 Decision 和快照、成功审计、Outbox 及原响应重放。与实际 PostgreSQL CHECK 的 23 边对表互补；不是全排列权限测试或真实签核。组织管理员、失权、跨项目、并发和故障回滚另由原定向用例覆盖。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_stage_edges.py --tb=short --junitxml=.local/stage-edges-first.xml` | 1 | FAIL：测试将审计 `request_id` 的 `varchar` 当作 UUID 比较；按实际类型查询并保留原断言，`.local/stage-edges-first.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_stage_edges.py --tb=short --junitxml=.local/stage-edges-second.xml` | 0 | PASS：6 路径 / 24.38 秒，`.local/stage-edges-second.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_stage_edges.py tests/test_reviews.py tests/test_lifecycle.py tests/test_transition_authority.py tests/test_mcp_transition.py tests/test_runtime.py::test_runtime_stage_catalog_matches_frozen_graph --tb=short --junitxml=.local/stage-edges-regression.xml` | 0 | PASS：35 项 / 91.53 秒，`.local/stage-edges-regression.log`。 |

## 0033 预算版本在同一时点生效

依据：§5.2、§12.1 Q2、§12.2 T05、§20.3 和 [ADR 0009](../adr/0009-baseline-change-authority.md)。基准 SHA `d2b12df5acc3b680b3a6e165781fd2385a0380ec`，定向被测源码树 `08746312574ff91954f3f9df0c50f83f89d7cdf6`。Windows、PostgreSQL 16.14 隔离 schema、真实回环 HTTP/SDK stdio，合成账号、模型关闭。`0033_atomic_budget_cutover.py` 以语句时点记账：获批预算在同一条 SQL 中退役旧版并插入新版；落地批准时两种口径一起冻结。旧流水外键和已存历史不重写。OpenAPI 明确小数字符串精度、颜色枚举及不可计算时可省略偏差率。

| 实际命令或用例 | 退出码 | 结果与证据 |
|---|---:|---|
| `pytest` 定向用例 `test_approved_cost_bases_switch_at_one_effective_instant`、`test_cost_summary_contract_handles_unavailable_rate` | 1 | FAIL：旧回读在换版空档显示 `budget=0.00`，但已核验实际为 `30.25`（`.local/cost-cutover-before.log`）；OpenAPI 错列 `varianceRate` 必填（`.local/cost-dto-before.log`）。修复过程首轮还误将 SDK 的 `ok` 外层与 REST 比较，保留 `.local/rc0033-cost-first.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_costs.py tests/test_postings.py tests/test_cost_reconciliation.py tests/test_change_requests.py tests/test_reviews.py tests/test_stage_edges.py tests/test_postgres_invariants.py tests/test_database_roles.py tests/test_recovery.py --tb=short --junitxml=.local/rc0033-cost-verified.xml` | 0 | PASS：39 项 / 105.64 秒，`.local/rc0033-cost-verified.log`。换版前一微秒和生效时点的金额、人天、核验流水和来源，一致于人工 REST、Agent REST 与真实 stdio。Outbox 写入故障不产生部分成本、批准、成功审计或幂等回执；重试后只生成一个有效版本。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check` | 0 | PASS：冻结规格和 mypy 87 源文件，`.local/rc0033-check-final.log`。 |

带数据升级、联合恢复、ERD 及综合运行见 [M4](M4-report.md#0033-候选与综合回归)。不改变业务阈值、权限、签核或未决政策。

## 变更申请和历史的嵌套契约

依据 §2.1、§5.4、LR-05、Q8/T05/T07、工程 DTO 补充 §1/5/7、[ADR 0009](../adr/0009-baseline-change-authority.md) 和 [ADR 0016](../adr/0016-approved-requirement-additions.md)。基准 `72ae09b1706365ccdbf428e39bc9af007faf5128`（复现时等同产品代码的预算候选树），被测树 `783b2c2abf2f37a77fc40ee43270d79f3b690910`，仅后补证据文本。Windows、PostgreSQL 16.14 隔离 schema、回环 HTTP、真实 SDK stdio、Edge 158.0.4370.33、合成账号、模型关闭。实现：`pmo/change_contract.py`、`change_requests.py`、`change_history.py`、`mcp_server.py`，以及 Web 变更台账和需求历史。冻结契约、审批角色及迁移 `0033` 不变；未改变附件引用，不重复联合恢复。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_change_contracts.py --tb=short --junitxml=.local/change-contract-before.xml` | 1 | FAIL：2 项。基准 OpenAPI 接受混入取消状态的需求修改，旧快照无可辨识形状，`.local/change-contract-before.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_change_contracts.py tests/test_change_requests.py tests/test_change_history.py tests/test_requirement_additions.py tests/test_requirement_baselines.py tests/test_forecast_governance.py tests/test_costs.py tests/test_runtime.py tests/test_mcp_action_matrix.py tests/test_reference_vectors_governance.py --tb=short --junitxml=.local/change-contract-verified.xml` | 0 | PASS：36 项 / 78.98 秒，`.local/change-contract-verified.log`。四类真实申请与 HTTP/stdio Schema 一致，数值类型、非本地日期、前后字段不匹配、伪造批准/新增均拒绝；同主体重放、无效写无副作用、跨项目和 Agent 终裁禁权，审批仍需独立人。获批新增和旧 JSON（含 null）保留，Agent 历史不接收非契约原文。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py -k 'draft_workflow or independent_requirement_addition or drafting_through_archive_without_model' --tb=short --junitxml=.local/change-contract-edge.xml` | 0 | PASS：3 项 / 103.53 秒，`.local/change-contract-edge.log`。旧快照标识、原值、申请与独立批准回读，新增需求与无模型三关口归档。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：冻结规格、mypy 88 源文件及 Vue 类型，`.local/change-contract-check.log`、`.local/change-contract-vue-verified.log`。首轮类型声明缺口退出 1（Schema dict）和 2（Web 旧快照字段），保留 `change-contract-check-first.log`、`change-contract-vue.log`。 |

## 结项对账和风险接收的回执契约

依据：主方案 §4.4、§5.2、LR-07、工程 DTO 补充 §1/3/7 和 [ADR 0011](../adr/0011-closing-evidence-and-lifecycle.md)。基准 `0ef144874a6a55638be6c8a346e73f75124737dc`，被测导出树 `af645fc52dfa7e0202587458959f4a8780d877c6`。Windows、PostgreSQL 16.14 隔离 schema、回环 HTTP，合成账号、模型关闭。`pmo/cost_reconciliation.py` 响应固定金额/CNY、人天/person_day 两项，明确金额、比率和核验时间格式；历史与终裁使用同一回执 DTO。`pmo/risk_resolution.py` 的非成员接收人仅能取得自己的有效待确认范围，不含项目或审核人 ID，不可发起或终裁移交。六个操作均已标注 human-only，REST 仍实际拦截。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_cost_reconciliation.py::test_reconciliation_contract_binds_both_units_and_precise_values tests/test_risk_resolution.py::test_nominee_response_contract_is_narrow_and_not_a_project_receipt --tb=short --junitxml=.local/closing-contract-before.xml` | 1 | FAIL：2 项，旧响应合同接受缺口径和非成员响应中的项目 ID。`.local/closing-contract-before.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_cost_reconciliation.py tests/test_lifecycle.py tests/test_recovery.py tests/test_requirement_exceptions.py tests/test_risk_resolution.py tests/test_runtime.py tests/test_stage_edges.py tests/test_zero_budget_closing.py --tb=short --junitxml=.local/closing-contract-final.xml` | 0 | PASS：51 项 / 127.57 秒，`.local/closing-contract-final.log`。含角色/证据拒绝、历史回读、相关依赖变化和结项。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check` | 0 | PASS：冻结规格、mypy 88 源文件，`.local/closing-contract-check-verified.log`。首轮 mypy 退出 1，保留 `.local/closing-contract-check.log`。 |

不更改业务政策、授权、迁移或附件引用。全量回归见 [M4](M4-report.md#0033-结项契约候选复验)。

## LR-06 校验当期日报里的关键里程碑申报

依据：主方案 §5.6 LR-06、领域不变量 §1、DEC-18 的隔离默认。测试前基准 `0ef144874a6a55638be6c8a346e73f75124737dc` 加已暂存结项契约；反例测试 blob `fd61d1bce90aa88ecaf6f9d35b22111c5d35b099`。修复基准 `6c1cc854f9e4d26b6b2c1cec2fd4695d030a812b`、验证树 `e2ed693f99f11afc951776b0db5699d308dd274f`（恢复用例后补，仅证据文档未入树）。Windows、回环 HTTP/stdio、PostgreSQL 16.14 隔离 schema 和专库，合成数据、模型关闭。

`pmo/reports.py` 共用结构、内容哈希和提交回执校验；`pmo/health_calculation.py` 把北京时间当天、每提交人最新有效日报的显式关键里程碑申报纳入同一证据检查。草稿、其他日期、无申报的记录不触发。日报不能填补漏交周报，也不能决定其发布。未核验、缺字节或哈希不符均不能作为佐证；补证不等同于完成里程碑。旧快照原样保留。按来源周期、里程碑和申报类别去重，规则版本、迁移和政策签核不变。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_health.py -k 'lr06_claims and daily' --tb=short --junitxml=.local/lr06-daily-before.xml` | 1 | FAIL：日报申报进展/完成两个反例均错误返回 `green`。`.local/lr06-daily-before.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_health.py tests/test_weekly_health.py tests/test_reports.py tests/test_weekly_drafts.py tests/test_reference_vectors_signals.py tests/test_database_roles.py tests/test_recovery.py --tb=short --junitxml=.local/lr06-daily-verified.xml` | 0 | PASS：38 项 / 106.26 秒，`.local/lr06-daily-verified.log`。日报、周报分别验证进展/完成、源字节篡改、旧快照只读、补证以及规则任务和 Outbox 去重。旧日报和草稿不遮盖当前；日报不能填补漏交周报。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_recovery.py --tb=short --junitxml=.local/lr06-daily-recovery.xml` | 0 | PASS：2 项 / 30.59 秒，`.local/lr06-daily-recovery.log`。在带数据 `0016→0033`、私有原件联合恢复中，新增日报版本、引用、快照和原件 SHA-256 回读。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check` | 0 | PASS：冻结规格和 mypy 88 源文件，`.local/lr06-daily-check.log`。 |

## 组织审计回执可核对裁决来源

依据：主方案 §6.3、§20.3–20.4、领域不变量 §5、GOV-007、T07。基准 `3bc490cd4c9f281b1704b11615a217660658363c`；最初反例测试 blob `028056b2a76a9d3b4798337a9a1fd889e244423e`（当时只有审计测试改动与等同的 LR-06 源码）。最终源码树 `554602fdb96c7ec9fea9b0bb05a4749685902b56`，不含证据文本。Windows、PostgreSQL 16.14 隔离 schema/专库、回环 HTTP、Edge `158.0.4370.33`，合成账号。`pmo/audits.py` 返回已存 `before_hash`，只在本组织人工管理员查询时可见；REST 仍独立拒绝直接 Agent 调用。`web/src/AuditLog.vue` 按同一请求号显示操作者、委托、裁决、规则、版本化来源、前后摘要。无凭据、请求正文或幂等键；不回填缺失旧字段、不改变事务或审批权。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_admin_audit.py --tb=short --junitxml=.local/audit-detail-before.xml` | 1 | FAIL：已提交审计有存储摘要，响应缺 `previousHash`；`.local/audit-detail-before.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_admin_audit.py tests/test_reviews.py tests/test_lifecycle.py tests/test_error_contract.py tests/test_runtime.py tests/test_database_roles.py --tb=short --junitxml=.local/audit-detail-final.xml` | 0 | PASS：36 项 / 112.80 秒。来源、摘要与实际写入对表，拒绝和跨组织不泄漏、读审计故障不返回数据。首轮 1 FAIL / 35 PASS：测试误用空描述默认值，保留 `.local/audit-detail-verified.log`。最终 `.local/audit-detail-final.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py::test_browser_draft_workflow --tb=short --junitxml=.local/audit-edge-verified.xml` | 0 | PASS：1 项 / 53.32 秒。独立立项评分及裁决，刷新后凭请求号打开审计详情、核对版本化来源和摘要。此前健康/归档三项 PASS，新增用例因测试期望漏 `@1`、刷新后读取已释放的响应体两次 FAIL；见 `.local/audit-health-edge.log`、`audit-health-edge-final.log`、`audit-edge-verified.log`。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：冻结规格、mypy、Vue 类型。`.local/audit-detail-check.log`、`.local/audit-detail-vue.log`。 |

## 归档只读的数据库约束

依据：主方案 §4.1、§20.5、LR-08，领域不变量 §4/§6。基准 `4739d033d9a31ba4239cdfe10ea00a297b96b4a9`。`0034_archived_core_read_only.py` 在 `0033` 后加项目及 15 张受保护表的触发器。评分和 Decision 已有的追加式保护不变；数据库额外拒绝向归档项目追加记录或重挂归属，校验新增评分及有关口 Decision 同 Review 的项目一致。非关口 Decision 仍可不引用 Review。子记录写入按 ID 排序锁定相关项目；需要等并发归档提交后重新判断阶段。此约束不授予审批权限，也不拦截成功归档本身、审计或 Outbox 消费。

Windows、隔离 PostgreSQL 16.14、真实 HTTP、合成数据。`tests/test_postgres_invariants.py` 的 `test_archived_project_core_records_are_read_only_in_storage` 和 `test_archive_serializes_storage_writes_against_the_project_lock` 在基准 `0033` 上均复现失败：退出 1，`.local/archive-storage-reproduced.log` 与同名 JUnit。测试在 `0034` 确认两种拒绝、无部分写入、其他活动项目可写；库角色用例确认非所有者账号有相关列权限仍被拒绝。定向 61 项 PASS，退出 0，`.local/archive-storage-verified.log`。前两次夹具初始化失败保留于 `.local/archive-storage-before.log` 和 `.local/archive-storage-first.log`，不计为业务失败证据。全量、候选和带数据升级/联合恢复见 [M4](M4-report.md#0034-归档候选与联合恢复)。

## 三线零分母及争议出处

依据：主方案 §5.2–5.5，LR-04/05，工程 DTO 补充 §3–4，Q2/Q7。最终测试基准 `b82d9440800e4a8655e194341d21f3fb62b15262`、源码树 `64ff7789082e4c4ab6b63f4fdcf478e65098e133`，单独导出。Windows、PostgreSQL 16.14、回环 HTTP、真实 stdio 与 Edge `158.0.4370.33`，合成账号，模型关闭。

- `pmo/scope_quality.py`、`health_calculation.py`、`costs.py` 区分零分母 `UNAVAILABLE` 与还没有可核对的基线。仅有效预算为零时展示不可计算；成本灯色仍取实际净额、预测及在 `asOf` 前生效的零预算声明，声明也带出处。零分母不转成 `100%`/`NaN%`，不改阈值、冻结基线或历史快照。
- `pmo/weekly_health.py` 只从同项目、已存快照中存在的 `requirement-dispute:<id>@1` 关联当时的客户争议；撤回后仍可解释历史红线，不把新撤回当成旧来源。`web/src/RequirementMetrics.vue`、`Costs.vue` 明确展示不可计算。
- 中间树 `14a2506bf499df9f5a9f37cdd59265edf68b9078` 全量退出 1：303 PASS、2 FAIL，两个测试仍断言响应缺字段，而冻结向量要求 `UNAVAILABLE`。最终用原向量结果直接比较，不改向量。保留 `.local/signals-backend.log`、`.xml`。
- 最终针对性验证 3 PASS、退出 0：`test_no_requirement_denominator` 两条原向量和 `test_browser_customer_dispute_has_independent_and_persistent_history`，见 `.local/signals-final-targeted.log` 及同名 JUnit。整体验证命令、退出码和证据见 [M4](M4-report.md#三线候选复验)。

## 人工权限契约、复盘回执和旧三线快照

依据主方案 §4.1、§5.4、§6、§7、§20.5，LR-05/08，工程 DTO 补充 §1/4/7。基准 `d65afae0c4fb8735cc43977a436d679adaa59288`、最终被测树 `ba3eb203ac5f12af9bb196813156f990de943ee0`。Windows、PostgreSQL 16.14、真实回环 HTTP 与 stdio、Edge，合成账号，模型关闭。

- `pmo/api.py` 在 OpenAPI 从既有主体控制和 MCP 路由发布说明，不改变鉴权。`tests/test_runtime.py` 对照冻结标记，并以携带对应动作的 Agent 令牌直调七条人工评审和审批路径，核验 403 和独立 denied 审计。
- `0035_retrospective_receipts.py` 校验已完成复盘是四条非空字符串、Lesson 草稿没有确认字段、已确认 Lesson 有不同自然人的确认记录及有效时间顺序。确认权仍由服务校验。合法历史不重写；非法存量使迁移整体回滚，不删除、不清零、不作补签。
- `pmo/weekly_health.py` 仅将快照明示的 `disputed=true` 解释为客户争议。旧快照缺该字段曾使周度读取报错，原红线、出处与哈希保留。`test_weekly_health_reads_pre_dispute_p0_receipt_without_reclassifying` 修复前 FAIL，退出 1（`.local/weekly-legacy-before.log`）。回归覆盖 16 项后端、2 项 Edge，均 PASS、退出 0（`.local/weekly-legacy-verified.log`、`.local/weekly-legacy-edge.log`）；完整命令和恢复见 [M4](M4-report.md#0035-隔离候选和联合恢复)。

## 复盘四问原文

依据原方案 HTML 401–406 行、主方案 §4.4/§8 与 T08。基准 `a84d8faa555019d53c8b51fe4a7562845a90c352`、被测产品与最终浏览器断言树 `f485c4dfbd754eb965c5502db2e6058e1775607d`。Windows、隔离 PostgreSQL 16.14、Edge `158.0.4370.33`，合成账号，人工闭环时模型关闭。

`pmo/retrospectives.py` 由 GET 提供四条完整原问；`web/src/Retrospective.vue` 和结项回顾助理共用问句，按原序保存和读取。PUT 响应及已有幂等记录不变；模型预览的已存来源字段、归档证据、已保存答复和审批条件不变。本批没有迁移或附件变动。

| 实际命令（本机 `.venv/Scripts/python.exe`） | 退出码 | 结果与证据 |
|---|---:|---|
| `python -m pytest -q tests/test_retrospectives.py -k 'archived_replay or termination_origin' --tb=short --junitxml=.local/retrospective-questions-before.xml` | 1 | FAIL：基准下正常和终止复盘都缺问句（`.local/retrospective-questions-before.log`）。 |
| `python -m pytest -q tests/test_retrospectives.py tests/test_assistant.py tests/test_assistant_preview.py tests/test_lifecycle.py tests/test_runtime.py tests/test_reference_vectors_governance.py --tb=short --junitxml=.local/retrospective-questions-backend.xml` | 0 | PASS：48 项 / 101.00 秒（`.local/retrospective-questions-backend.log`）。在最终浏览器定位调整前运行，后端代码和断言未再修改；正常、终止、权限、重放及只读归档通过。 |
| `python -m pytest -q tests/browser_case.py -k 'drafting_through_archive or assistant or preview' --tb=short --junitxml=.local/retrospective-questions-edge-verified.xml` | 0 | PASS：3 项 / 61.96 秒。四问在填写和归档回读中逐项匹配，手机视口助理和单独模拟网关预览通过。首轮 2 FAIL：旧短标题定位、错误模式定位（`.local/retrospective-questions-edge.log`）；修正定位后保留业务断言（`.local/retrospective-questions-edge-verified.log`）。 |
| `python -m mypy pmo --ignore-missing-imports`；在 `web` 下运行 `node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：88 个 Python 文件与 Vue 类型检查。`.local/retrospective-questions-mypy.log`、`.local/retrospective-questions-vue.log`。 |
