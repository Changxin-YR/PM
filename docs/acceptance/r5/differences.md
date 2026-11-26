# Lite 原文差异复核

基线为当前分支 `fe7c50b`。仓库与执行包原始 HTML 的 SHA‑256 均为 `e88d37a5e59fe2e3c14b768ab9d11ae11100b68308278c9bf9b5a0ab1c68a532`。`f1f1c82` 固定恢复版工作台入口，`f2740f9` 为业务补强，`fe7c50b` 保存逐条比较。启动时当前分支在 `fe7c50b` 无后续提交。`dev/pmo-v1` 上的 R3 是分叉历史，恢复版基线沿本分支续作。启动时未发现仓库或上级生效的 `AGENTS.md`。

| 条目 | 分类 | 原文与现行行为 | 核查入口 |
|---|---|---|---|
| SRC-002 | EQUIVALENT | 项目数是适用规模与升级信号。系统保留单治理层、项目与需求粒度，项目数本身不禁用 Lite。 | `navigation.ts`、`test_milestones.py`、`test_stage_edges.py` |
| SRC-004 | EQUIVALENT | 平台表格、轻脚本的逻辑能力迁移到独立 REST／MCP / Web，仍共用规则和持久化。技术形态发生变化。 | `test_runtime.py`、`test_dual_entry.py` |
| SRC-011 | NEEDS_DECISION | 有组织身份才能发起，进入送审前增加任命与独立接受。内部发起可用，外部身份与任命具体规则待 DEC-19。 | `test_appointments.py`、`test_onboarding_admin.py` |
| SRC-019 | NEEDS_DECISION | 原文至少一条教训并人工确认，系统要求另一名具权人确认，附复盘及适用校准前置。独立性与待观察归档正式口径待确认。 | `test_retrospectives.py`、`test_lifecycle.py`、DEC-20 |
| SRC-020 | EQUIVALENT | 暂停、恢复和终止需原因及人工决议，终止附独立回顾和归档。 | `test_transition_authority.py`、`test_stage_edges.py` |
| SRC-036 | EQUIVALENT | 自然人、组织账号及项目任命分别保存，审批记录可追责。正式替换在任者是额外运营场景。 | `test_appointments.py`、`test_transition_authority.py` |
| SRC-038 | EQUIVALENT | 按原图实际枚举的关系保留语义。技术审计和版本关系保存在独立结构。 | `docs/erd.md`、`test_postgres_invariants.py` |
| SRC-041 | EQUIVALENT | 3／2／1 权重不变，获批取消退出有效需求分母并计变更率，正式新增纳入有效范围。未批准变化保持冻结。 | `test_requirement_baselines.py`、`test_reference_vectors_governance.py` |
| SRC-044 | EQUIVALENT | 立项助理自动在许可范围检索已确认教训，返回命中分类、原项目及引用。原文例句中的汇总是允许的表达形式。 | `test_assistant.py`、`test_retrospectives.py` |
| SRC-052 | NEEDS_DECISION | 原文“偏差”方向未明确。隔离采用绝对值，金额和人天独立归因与对账。 | `test_cost_reconciliation.py`、`test_zero_budget_closing.py`、DEC-16／26 |
| SRC-054 | NEEDS_DECISION | 书面客户接受仅覆盖有效版本的需求缺口，限定资格、期间、原件与撤回。正式细则待 DEC-09。 | `test_requirement_exceptions.py` |
| SRC-065 | NEEDS_DECISION | 两应交周期漏报为独立触发。关键里程碑无证据的当前解释限定在发布明确进度或完成陈述时。 | `test_health.py`、`test_reference_vectors_signals.py`、DEC-18 |
| SRC-068 | EQUIVALENT | 保留四硬四预警。LR-08 计月报、通知与归档前置同时存在。 | `test_reference_vectors_governance.py`、`test_health.py`、`lr08-monthly.mjs` |
| SRC-071 | NEEDS_DECISION | 同时点矛盾进澄清，跨期不同值由 PM 区分正常变化和矛盾。原文“与上周同类对照”的自动归档边界需业务确认。 | `test_facts.py`、`test_reports.py` |
| SRC-073 | NEEDS_DECISION | 候选、中可信事实、财务核验和有权人签署分轨。具体中可信资料如何进入正式三线须按事实属性核对，模型候选始终不作正式流水。 | `test_facts.py`、`test_health.py`、`test_postings.py` |
| SRC-077 | EQUIVALENT | 过程管家读取确定性周报、规则及三线，组合周报由授权项目集合生成。 | `test_assistant.py`、`test_weekly_portfolio.py` |
| SRC-078 | EQUIVALENT | 四问、缺口和教训候选可由助理准备，Lesson 由人确认，归档沿独立授权主链。独立确认细则见 SRC-019。 | `test_retrospectives.py`、`test_assistant_preview.py` |
| SRC-080 | NEEDS_DECISION | 业务逻辑保留五类。过程管家在受控报告提取中生成候选，需人工提报。其他模式仅访问允许的查询能力。台账问答允许已授权、可追溯的确定性台账来源，原文“无 Fact 引用拒答”的严格解释须单独确认。 | `test_runtime.py`、`test_report_analysis.py`、`test_assistant_preview.py` |
| SRC-098 | EQUIVALENT | Lite 实体语义保留，标识与版本可继续复用。未来完整版的实际数据迁移留待其范围确定时核验。 | `docs/erd.md`、`test_recovery.py` |
| SRC-100 | EQUIVALENT | 实际载体已通过现有独立数据库方案确定，所有入口复用同一服务。具体采购或目标部署另验。 | `README.md`、`test_runtime.py` |

## 原基线缺口

| SRC | 初始分类 | 实施切片 | 正式解除条件 |
|---|---|---|---|
| 026、042 | DEFECT | B1 同源人天分项、候选差额、独立核验和历史兼容 | RP-01 已批准并激活 |
| 048 | DEFECT | B2 评分锚点提醒和原锚点评估、证据及偏差回顾 | DEC-20 确认锚点、阈值和人工复核口径 |
| 063 | DEFECT | B3 绑定真实触发快照、书面重估、独立投入决议与任务完成 | DEC-26 明确当前具权责任人及适用动作 |
| 072 | DEFECT | B4 带版本与摘要的正式报告，以及异步隔离收件闭环 | RP-02、受权外部渠道和实际接收凭据 |
| 047 | DEFECT | B2 增补独立名单齐全后发起人确认、备案和阶段迁移候选 | DEC-04／19／20 激活原文快通，或明确签署采用加强链；固定 30 日与自然月的边界另行确认 |

以上 20 行中 EQUIVALENT 12 项、NEEDS_DECISION 8 项。没有取得足以登记 APPROVED_DEVIATION 的真实签核。

起点 5 条部分实现涵盖四类缺口。R5 补齐了分类、评分防操纵、重估裁决与隔离送达四条工程链。在 `SRC-072` 保留真实外部实收阻断。复查另外发现快通路径差异（`SRC-047`），已完成原文候选，正式启用仍需签核。同时修复了分类字段引起的旧过程助理响应兼容问题，历史无分类的四区块响应继续按原格式回读。
