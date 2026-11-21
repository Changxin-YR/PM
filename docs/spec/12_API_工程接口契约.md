# PMO V1.0.3｜Web REST / MCP 统一接口合同（第三轮审计）

本文件和 `12_OpenAPI_路由请求基线.yaml`、`11_MCP_工具契约.json` 是 **M0 输入基线**，不是已启动的服务，也不是最终生成的 OpenAPI。

## 一、强制要求
- 所有写操作必须鉴权、查项目可见范围、执行后端领域规则、检查 Idempotency-Key；现有实体更新还要检查 If-Match。MCP 仅把工具字段转换为上述头，不在工具内部绕过服务层。
- `x-actorTypes: [human]` 的操作必须由后端在真实身份上下文中独立拒绝 Agent token。MCP 不暴露工具只是辅助防护，不能替代 REST 的鉴权。
- 仅返回已经授权的业务字段；未授权项目、证据附件、历史 Lesson 一律执行同样的 DataScope 检查。
- 自动评分与三线计算复用后端领域函数，任何前端预览仅供展示，不得提交模型计算值作为权威结果。
- 通用成功包装 `{data,meta}`；错误 `{code,message,requestId,details}`，M0 必须为每个资源定义完整的响应 Schema，当前基线响应 data 未穷举，因此不能声称最终契约冻结。

## 二、MCP → REST 映射（40 个白名单工具）

| MCP tool | REST 方法 | REST 路径 | 类型 |
|---|---|---|---|
| `pmo_project_list` | GET | `/api/v1/projects` | read |
| `pmo_project_get` | GET | `/api/v1/projects/{projectId}` | read |
| `pmo_project_create` | POST | `/api/v1/projects` | draft_write |
| `pmo_project_draft_update` | PATCH | `/api/v1/projects/{projectId}` | draft_write |
| `pmo_role_assignment_propose` | POST | `/api/v1/projects/{projectId}/role-invitations` | approval_request |
| `pmo_charter_save` | PUT | `/api/v1/projects/{projectId}/charter` | draft_write |
| `pmo_review_prepare` | GET | `/api/v1/projects/{projectId}/reviews/prepare` | derived_read |
| `pmo_review_submit` | POST | `/api/v1/projects/{projectId}/reviews` | approval_request |
| `pmo_review_list` | GET | `/api/v1/projects/{projectId}/reviews` | read |
| `pmo_decision_get` | GET | `/api/v1/reviews/{reviewId}/decisions` | read |
| `pmo_gate_check` | GET | `/api/v1/projects/{projectId}/gates/check` | read |
| `pmo_milestone_list` | GET | `/api/v1/projects/{projectId}/milestones` | read |
| `pmo_milestone_create` | POST | `/api/v1/projects/{projectId}/milestones` | draft_write |
| `pmo_milestone_draft_update` | PATCH | `/api/v1/milestones/{milestoneId}` | draft_write |
| `pmo_milestone_forecast` | PATCH | `/api/v1/milestones/{milestoneId}/forecast` | bounded_write |
| `pmo_requirement_list` | GET | `/api/v1/projects/{projectId}/requirements` | read |
| `pmo_requirement_create` | POST | `/api/v1/projects/{projectId}/requirements` | draft_write |
| `pmo_requirement_draft_update` | PATCH | `/api/v1/requirements/{requirementId}` | draft_write |
| `pmo_requirement_deliver` | POST | `/api/v1/requirements/{requirementId}/deliver` | bounded_write |
| `pmo_requirement_change_history` | GET | `/api/v1/requirements/{requirementId}/changes` | read |
| `pmo_change_propose` | POST | `/api/v1/projects/{projectId}/changes` | approval_request |
| `pmo_cost_summary` | GET | `/api/v1/projects/{projectId}/costs` | read |
| `pmo_cost_posting_propose` | POST | `/api/v1/projects/{projectId}/cost-postings` | draft_write |
| `pmo_cost_budget_draft_save` | PUT | `/api/v1/projects/{projectId}/cost-budgets` | draft_write |
| `pmo_cost_forecast_save` | PUT | `/api/v1/projects/{projectId}/cost-forecasts` | bounded_write |
| `pmo_preapproval_spending_list` | GET | `/api/v1/analytics/preapproval-spending` | read |
| `pmo_risk_list` | GET | `/api/v1/projects/{projectId}/risks` | read |
| `pmo_risk_create` | POST | `/api/v1/projects/{projectId}/risks` | draft_write |
| `pmo_risk_update` | PATCH | `/api/v1/risks/{riskId}` | bounded_write |
| `pmo_task_list` | GET | `/api/v1/projects/{projectId}/tasks` | read |
| `pmo_task_update` | PATCH | `/api/v1/tasks/{taskId}` | bounded_write |
| `pmo_report_list` | GET | `/api/v1/projects/{projectId}/reports` | read |
| `pmo_report_get` | GET | `/api/v1/reports/{reportId}` | read |
| `pmo_report_submit` | POST | `/api/v1/projects/{projectId}/reports` | bounded_write |
| `pmo_fact_propose` | POST | `/api/v1/reports/{reportId}/facts/proposals` | draft_write |
| `pmo_health_get` | GET | `/api/v1/projects/{projectId}/health` | read |
| `pmo_alert_list` | GET | `/api/v1/projects/{projectId}/alerts` | read |
| `pmo_lessons_search` | GET | `/api/v1/lessons/search` | read |
| `pmo_stakeholder_create` | POST | `/api/v1/projects/{projectId}/stakeholders` | draft_write |
| `pmo_transition_request` | POST | `/api/v1/projects/{projectId}/transition-requests` | approval_request |

## 三、仅限 Web 人工身份的动作

| REST | 后端强制权限说明 |
|---|---|
| `POST /api/v1/reviews/{reviewId}/decisions` | `actor_type=human`，需具备 `reviewer` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/cost-postings/{postingId}/verify` | `actor_type=human`，需具备 `finance_verifier` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/cost-postings/{postingId}/reverse` | `actor_type=human`，需具备 `finance_verifier` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/role-invitations/{invitationId}/accept` | `actor_type=human`，需具备 `assignee` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/role-invitations/{invitationId}/decline` | `actor_type=human`，需具备 `assignee` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/requirements/{requirementId}/customer-confirm` | `actor_type=human`，需具备 `client_representative` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/projects/{projectId}/transitions` | `actor_type=human`，需具备 `authorized_reviewer` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/lessons/{lessonId}/confirm` | `actor_type=human`，需具备 `authorized_reviewer` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/health-snapshots/{snapshotId}/appeals` | `actor_type=human`，需具备 `pm` 授权，严禁 Agent token 通过 HTTP 直接调用 |
| `POST /api/v1/approvals/{approvalId}/decide` | `actor_type=human`，需具备 `authorized_approver` 授权，严禁 Agent token 通过 HTTP 直接调用 |

## 四、强一致性业务事务

**成功事务：** 权限检查 → 当前版本、申请/审批快照及规则版本核验 → 领域规则判定 → 写入业务状态、Decision、Outbox、成功审计 → 提交。任何步骤失败均不能产生部分业务结果。

**拒绝写入：** 业务事务回滚并返回规范错误；另以单独安全审计事务保存 `result=denied`、actor、project、action、reason、requestId。不得把拒绝日志放进必然回滚的事务并自称持久化。

**成本冲销：** 先新增待核验反向记录（必须引用某个有效 verified 正向凭证），独立人工核验后与原凭证一同参与 `SUM(signed_amount)`；绝不同时排除原凭证并再次扣减。相同原凭证仅能一次全额冲销，后续补正用新凭证。

**条件结项：** `requirements_ok OR valid_requirement_exception` 只改变需求门槛结果，成本和风险分别必须达标。有效例外必须列出书面接受的需求 ID、客户证据、授权评审人、当前申请哈希。

**任命闭环：** Creator 只能提议 Sponsor/PM；具有任命权的人发出邀请，被指定的本人登录 Web 接受后才进入可送审状态。Agent 不能接受任命、不能升级项目角色。

## 五、错误码与最小用例

401 `UNAUTHORIZED`（缺凭据/过期）；403 `FORBIDDEN`（无项目权限、Agent 尝试人类终裁）；404 `RESOURCE_NOT_FOUND`（资源不可见）；409 `VERSION_CONFLICT/IDEMPOTENCY_CONFLICT/APPROVAL_STALE`；422 `VALIDATION_FAILED/GATE_BLOCKED/BASELINE_FROZEN/EVIDENCE_UNTRUSTED`；503 `DATA_UNTRUSTED`（服务不可用场景，健康状态不可信通常正常返回并注明 untrusted）。

**M0 退出门槛：** 用 FastAPI/Pydantic 生成可被 OpenAPI 校验器解析的真实 `/openapi.json`，所有已列 path/opId 均能查到；逐项完善响应数据 Schema。`12_OpenAPI_路由请求基线.yaml` 是路由与入参基准，而非生产服务的 Swagger 已完成证明。

## 六、人工 Web 必备端点（第三轮主接口核对）

| REST 端点 | 用途 |
|---|---|
| `POST /api/v1/reviews/{reviewId}/scores` | `reviewer`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/reviews/{reviewId}/appeals` | `authorized_appellant`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/projects/{projectId}/evidence` | `authorized_member`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/reports/{reportId}/confirm` | `pm`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/facts/{factId}/verify` | `verified_operator`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/facts/{factId}/clarify` | `pm`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/risks/{riskId}/handoff` | `authorized_reviewer`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/risks/{riskId}/close` | `authorized_reviewer`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/requirements/{requirementId}/accept` | `pm_or_reviewer`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/changes/{changeId}/approve` | `authorized_reviewer`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/changes/{changeId}/reject` | `authorized_reviewer`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/me/agent-tokens` | `delegator`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `POST /api/v1/me/agent-tokens/{tokenId}/revoke` | `delegator`，服务端只接受 human 令牌，不能委托 Agent 直接最终执行 |
| `GET /api/v1/projects/{projectId}/changes` | 按项目级 DataScope 查询，禁止附件和事实旁路泄露 |
| `GET /api/v1/projects/{projectId}/members` | 按项目级 DataScope 查询，禁止附件和事实旁路泄露 |
| `GET /api/v1/projects/{projectId}/evidence` | 按项目级 DataScope 查询，禁止附件和事实旁路泄露 |
| `GET /api/v1/evidence/{evidenceId}` | 按项目级 DataScope 查询，禁止附件和事实旁路泄露 |
| `GET /api/v1/me/agent-tokens` | 按项目级 DataScope 查询，禁止附件和事实旁路泄露 |
| `GET /api/v1/reports/{reportId}/facts` | 按项目级 DataScope 查询，禁止附件和事实旁路泄露 |

**第三轮终审修订：** 附件上传 API 基线已采用 `multipart/form-data` 文件字节流，后端必须实际校验并计算 SHA-256；这不是已运行的文件上传测试。

## 七、修复过的 Web 接口缺口（M0 强制实现）

| API / 操作 | 用途与边界 |
|---|---|
| `GET /api/v1/projects/{projectId}/charter` | 立项卡详情；受限项目数据 |
| `GET /api/v1/projects/{projectId}/stakeholders` | 利益相关方列表 |
| `GET /api/v1/projects/{projectId}/cost-postings` | 实际凭证/候选流水审计列表 |
| `GET /api/v1/me/role-invitations` | 本人待确认任命 |
| `POST /api/v1/milestones/{milestoneId}/complete` | 人工基于证据完成关键里程碑 |
| `POST /api/v1/projects/{projectId}/tasks` | PM 创建受约束补强任务 |
| `POST /api/v1/reports/{reportId}/submit` | 已有草稿变更为已提交 |
| `POST /api/v1/reports/{reportId}/analyze` | 异步触发报告候选 Fact 分析 |
| `GET /api/v1/admin/users` | 组织成员身份列表 |
| `POST /api/v1/admin/users/invitations` | 管理员邀请组织人员，不生成默认密码 |
| `PATCH /api/v1/admin/users/{userId}/status` | 受控启停账号，不得修改本人权限扩大授权 |
| `GET /api/v1/admin/agents/tokens` | 管理员查看脱敏 token 元数据 |
| `POST /api/v1/admin/agents/tokens/{tokenId}/revoke` | 管理员吊销受控 Token |
| `GET /api/v1/admin/members` | 管理员查看组织成员 |

报告分析：`POST /api/v1/reports/{reportId}/analyze` 仅由本报告作者或现任 PM 持浏览器会话、CSRF、`If-Match` 与幂等键发起。返回 200 和分析收据（`runId`, `reportId`, `reportRevision`, `contentHash`, `status`, `attempts` 等），不代表模型工作已经结束。工程接口 `GET /api/v1/reports/{reportId}/analysis-runs` 按同一身份范围分页读取状态和建议。建议附服务端定位的 `sourceRef`、`quotePath`、`quoteOffset`；没有信任等级、决定或发布能力，仍需走 Fact 的候选与人工核验。详见 [ADR 0013](../adr/0013-report-analysis-jobs.md)。

**固定契约**：`GET /projects/{id}/gates/check?gate=...` 为关口预检查，原文中出现的 `/transitions/preview` 和 `/gates/{type}/check` 不再作为第二套接口；客户确认统一 `/requirements/{id}/customer-confirm`，项目成员添加通过受控角色邀请，不新增裸写 `/projects/{id}/members`。

**前后端一致**：`12_OpenAPI_路由请求基线.yaml` 是唯一机器路由输入基线；跨文件检查用于发现接口遗漏，但不证明操作已经部署。M0 要生成 FastAPI 原生 `/openapi.json` 并对照每个 `operationId`、HTTP 方法、入参、权限、输出 Schema。

**MCP 创建权限**：授权作用域 `(delegator, organization, allowedActions, allowProjectCreate, allowedProjectIds, expiry, revocation)`。创建项目时需组织级授权；不要求新项目 ID 提前列入已有 ID 白名单。其他项目操作仍逐项校验已有项目的 DataScope。

**评分强约束**：Scorecard 的每个维度只允许 `score_1 / between_1_3 / score_3 / between_3_5 / score_5` 五个锚点码，按 1～5 分一一对应；立项与落地评审维度集合不同，业务服务还要核验 `reviewType` 与实际 Review.type 一致。评审人提交后的评分、证据、理由生成不可变快照。

**筛选查询**：`pmo_project_list` 与 `pmo_requirement_list` 的对象型 `filters` 参数在 OpenAPI 中明确为 `deepObject + explode`，M0 的 FastAPI 需要实现并验证对应的请求解析（例如 `filters[stage]=executing`），或通过版本化契约修改为平铺字段；不可让 MCP 端与 Web 端默默采用不同查询协议。


## 八、V1.0.4 接口权限与事务补强
- **唯一状态写服务**：`transition_project()` 为唯一修改 `projects.stage` 的后端服务，最终人工关口裁决接口必须在同一事务调用它。HTTP `/transitions` 不拥有直接写状态的特权，仅按状态边检查合法操作、身份和 Decision 依赖。
- **项目创建后的 Agent 草稿范围**：在 `POST /projects` 成功创建事务内，将新项目加入该**同一令牌**的草稿权限集合；只继承委托人的 Initiator 草稿权，不继承 Sponsor/PM/Reviewer 权限，且需防止重试重复授权。
- **人工成本核验权限**：`finance_verifier` 是须明确赋予的能力而非新 PMO 治理层级；`system_admin` 不自动拥有其权力。凭证创建人、其委托人和本次核验人不能是同一责任主体。
- **审批读写分离**：最终裁决 `/reviews/{id}/decisions`、客户确认、凭证核验、Lesson 确认、项目归档由 human-only API 强制拒绝 Agent Token；`Person.role` 描述性文本不能当作角色授权来源。
- **MCP 返回**：`11_MCP_工具契约.json` 的 40 个 output data 已具最小类型；真实 FastAPI `OkResponse.data` 仍为基线占位，M0 按业务 DTO 构建并比对客户端接受的类型。
