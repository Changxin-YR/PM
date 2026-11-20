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
