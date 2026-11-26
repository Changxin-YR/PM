# R5 立项评审和结果校准

适用条目为 SRC-045～048，关联 SRC-043、044。沿原有关口、身份、评审会记录与复盘服务实现。评分表、评审决议快照和校准结果继续使用现有存储。

| 业务入口 | 本轮行为 | 工程证据 |
|---|---|---|
| 关口工作台 · 核对评分理由 | 评审人可用当前评审版本预检。对立项卡相应字段中可核对的明确断言，显示理由原话、材料原话、引用和冲突的维度锚点。其余情况提示人工核对。评分、人工说明、提示及完整证据引用记录在原评分卡。 | `tests/test_r5_review.py`、`web/e2e/lifecycle.mjs` |
| 关口工作台 · 确认快速立项并备案 | 冻结名单交齐、没有否决且未舍入加权达到 4.20，经当前发起人确认，在同一事务保存人工裁决、确定评审会备案、进入方案细化、成功审计与 Outbox。保留 Sponsor 和 PM 任命、独立评分、正式策略和角色核验。 | `tests/test_r5_review.py`、`tests/test_reviews.py`、`web/e2e/review-r5.mjs` |
| 复盘与教训 · 立项评分结果校准 | 并列原分、实际人工评价、差值和状态，展开保留原锚点、原卡引用、实际附件与适用的结项裁决。旧小数结果兼容。待观察和证据不足保留空结果；已冻结原卡和结项回执保留。 | `tests/test_lifecycle.py`、`web/e2e/review-r5.mjs` |

评分自动提示仅核对约定的完整断言与相符的冻结立项字段。反例、引语、假设、历史与未来陈述，以及无可核来源的表述，留给人工判断。现有人工结构化证据检查保留。本补缺没有新增模型调用权限或 egress。既有四模式和报告模型通道的真实验收独立记录。

正式快通需业务负责人签署 DEC-04、DEC-19、DEC-20，并由具权人通过既有登记和激活机制将 `review.charter-fast` 应用于目标组织及项目。版本 `pmo-lite-charter-fast-r5-v1` 明确备案行为。服务端还核验原 `review.charter` 策略。启用或撤销导致冻结策略变化时，原待审记录须撤回并重送。隔离测试将 `PMO_R5_FAST_TRACK_SCHEMA` 与 `PMO_SYNTHETIC_POLICY_SCHEMA` 指向同一数据库内已确认的 `pmo_test_…`，工作台标记为演练。正式未签路径仍拒绝候选快通。

已存在的加强链仍在原签核作用域受控运行。正式启用应选定规则版本；历史确认、评审、状态和决议保持可读。现有拒绝后一次重报以 30 日计，原文“一个月”是否指自然月以及 DEC-20 的绝对差 1.00 复盘阈值仍待签核。

新增的 `POST /api/v1/reviews/{reviewId}/scores/check` 仅供独立人工评审，校验 `If-Match`、冻结范围、评分轮次、锚点及证据。使用原评分 DTO，预检不写评分及幂等状态。快通扩展既有 `confirmations` 的兼容可选参数 `fastTrackReason`，原确认请求与 MCP 工具合同保留。校准结果可选 `actualAnchorCode`，提交时校验它与同次评分对应。无新增数据库迁移。

## 复验命令

```powershell
$env:PMO_PILOT_RUNTIME='native'
.venv/Scripts/python.exe -m scripts.pilot_regression -- tests/test_r5_review.py tests/test_reviews.py tests/test_lifecycle.py tests/test_approval_queue.py tests/test_formal_policy.py -q --junitxml=.local/r5-review-backend-final.xml
.venv/Scripts/python.exe -m scripts.pilot_regression -- tests/browser_review_r5.py tests/browser_case.py::test_browser_drafting_through_archive_without_model -q --junitxml=.local/r5-review-edge-final.xml
```

实际 Edge 截图输出至 `.local/r5-review-screenshots/`，保留 02 评分锚点核对、05 快速立项待确认和已备案、06 校准与结项来源。截图为隔离 PostgreSQL 的真实 Web 页面。
