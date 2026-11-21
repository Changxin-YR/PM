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
