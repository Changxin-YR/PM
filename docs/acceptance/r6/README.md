# R6 隔离试点交接

## 当前演示项目

东湖园区设备报修与回单试点 `R6-PARK-001` 已归档。用项目经理账号 `r6-m-pm` 可现场查阅四模式对话、两期报告、独立对账及客户签收。26 轮中文对话、两份正式周报的打印件、全量后端与浏览器回归结果均已归入同一份验收单。[园区试点验收](园区试点验收.md) · [打开项目助理](http://127.0.0.1:18790/#project=9fd07805-5ca7-4d8e-ba9e-7c8d144622d2&tab=assistant)

## 较早的移动工单与行政问答交接快照

2026-11-26，北京时间。本机分支 `codex/pmo-r6`，HEAD `a773b21`。接手项目为**企业移动工单管理系统 `R6-MOBILE`，当前阶段 `executing`**。先核对落地评审的条件任务“抽查三张离线回单的同步记录”，再从第一期实施报告开始。交接时只读查询到该项目有 0 份报告、0 笔正式周报投递。[项目交接索引](../../progress/handoff.md)

## 六份业务回执

行政问答有五个独立项目样本，移动工单另有一个项目。下表阶段经 R6 数据库只读回读，`requestId` 对应本机 HTTP API 的最后一次有效裁决或快速备案，六项均返回 HTTP 200。原始回执按 `.local/pmo-r6-preview/<项目代号>-receipts.json` 保存。每份文件的 `steps` 同时保留重试检查点，查证时按步骤名和 `requestId` 定位。

| 项目代号 | 已完成路径 | 当前阶段 | 回执步骤 | `requestId` |
|---|---|---|---|---|
| `R6-QA-FAST` | 双人评分后，发起人快速备案 | `designing` | `charter-decision` | `d172d1bf-0782-4e41-9bc4-2aa1de61bf12` |
| `R6-QA-NORMAL` | 普通立项评审通过 | `designing` | `charter-decision` | `f764ffc4-ffee-4180-a545-8d8cfbdfd01d` |
| `R6-QA-APPEAL` | 否决门槛拦截通过请求，复议后第二轮重评并快速备案 | `designing` | `charter-decision` | `18f3c482-3c11-40a9-92de-2e4440b08aa7` |
| `R6-QA-REFILE` | 首轮否决，修改立项材料后重报通过 | `designing` | `refiled-decision` | `5a370a94-c06b-4024-9d93-a4d8d1f94bae` |
| `R6-QA-LOW` | 低分通过请求返回 `422 CHARTER_SCORE_BELOW_THRESHOLD`，随后正式否决 | `rejected` | `charter-decision` | `2b0a5114-bc25-4565-bce1-51bb15757459` |
| `R6-MOBILE` | 普通立项后，落地评审有条件通过 | `executing` | `feasibility-decision` | `c865ffaa-be0a-443b-bf8e-7a26788f6992` |

移动工单已完成组织开户和项目角色任命、三条需求、三个里程碑、七类预算与预测、PM 与客户代表的需求基线双签。落地裁决回执中的条件任务状态为 `todo`。工程脚本的现有业务路径到此结束。行政问答的阶段输出在 `.local/r6-evidence/projects-4.log`，其中移动工单曾因预算分项缺失中断，改用七类预算后续跑的最终输出在 `.local/r6-evidence/mobile-planning-2.log`。

## 工作区与登录

- 14:43 实测 R6 Web `127.0.0.1:18790` 返回 HTTP 200，API `127.0.0.1:18791/health/ready` 返回 `ready`。PostgreSQL `127.0.0.1:55456/pmo_pilot` 使用实例专属 schema，实读 Alembic `0043`。R5 单独使用 `18788/18789/55454`（Web/API/数据库）。
- R6 运行记录显示模型关闭。政策环境为 `pmo-r6-isolated`，每个行政问答样本有 3 项 `r6-v1` 合成政策，移动工单有 9 项，交接时逐项查得最近政策事件均为 `active`。签署、核验、激活由不同身份执行。版本化政策和隔离周报投递的代码已接入，移动工单的实际隔离收件列在下方待验清单。[政策登记手册](../../ops/policy-activation.md)
- 本机未提交改动涉及数据库角色、政策选择、周报投递、实例启动、造数和两份回归测试。新增的两个脚本尚未跟踪，接手或迁移工作区时一并保留。根目录原有四份历史评审资料属于此前工作区。[中文项目演练](../../../scripts/pilot_r6.py) · [开户、资格与政策登记](../../../scripts/pilot_r6_setup.py)

在本仓库根目录的 PowerShell 查看现有实例。若已停止，使用同一环境变量运行 `.\.venv\Scripts\python.exe -m scripts.pilot_local start`，沿用既有项目与回执。

```powershell
$env:PMO_PILOT_RUNTIME = 'native'
$env:PMO_PILOT_INSTANCE = 'r6-preview'
.\.venv\Scripts\python.exe -m scripts.pilot_local status
```

在本机私密交互终端中沿用上述环境变量，执行 `.\.venv\Scripts\python.exe -m scripts.pilot_r6 login m-pm` 查看移动工单 PM 登录资料，行政问答 PM 使用 `q-pm`。口令、邀请记录及政策原件存于受保护的 `.local/pmo-r6-preview/`，仅在该机读取。已存在的六个样本可直接按项目代号打开。

## 工程验证

本机后端回归使用独立 PostgreSQL `55444`。下列 R6 本机证据均位于 `.local/r6-evidence/`。

| 验证范围 | 实际结果 | 核查入口 |
|---|---|---|
| 远端 `a773b21` 基线 | 445/446 通过。唯一失败是正式周报已入队，测试读取回执时仍为 `pending` | `ci-job.log` |
| 本机复现周报积压 | 预置 501 个事件后复现同一断言。测试改用独立队列并按目标投递事件核对完成状态，定向复验 1/1 通过 | `ci-backlog-before.xml`、`ci-backlog-after.xml` |
| 本机首轮全量后端 | 444/446 通过。`test_private_evidence_to_milestone` 遇到 HTTP 读取超时。`test_expired_lease_and_worker_commit_failure` 尝试修改有效租约，被数据库拒绝 | `backend.xml`、`backend.log` |
| 附件与报告分析定向复验 | 租约测试改为等待数据库记录的实际到期时间。附件 2 项、报告分析 12 项，合计 14/14 通过 | `recovery-report.xml` |
| 初始前端构建 | Vue 类型检查与生产构建通过。最大 JS 包 1,495.66 kB（gzip 474.48 kB），实际加载表现待 Edge 验证 | `build-initial.log` |

编写此段时，最终全量后端尚待重跑。后续结果见本页顶部的园区验收单。R5 的 446 项后端、35 项 Edge 归属其历史验收。[R5 验收记录](../r5/verification.md)

## 当时的后续验收顺序

1. 从 `R6-MOBILE` 的现有实施阶段处理评审条件，完成两期报告、分类人天独立核验、冲突事实及变更审批，保存角色、版本、请求编号和阶段回读。
2. 用正式周报核对隔离收件、未知状态查询、人工确认重试和 worker 重启恢复，再走结项、归档。独立记录报告版本、投递回执与归档裁决。
3. 补 R6 实际 Edge 角色页面、中文 A4 导出、四模式中文问答与报告分析，使用本地人工流程或隔离模拟网关。Codex Desktop、DSH Desktop 分别留实测记录。
4. 在最终候选上执行全量后端、Edge、类型与构建、八问、十项人机验收、102 个冻结向量、升级与数据库及附件联合恢复，更新原文追踪、差异和功能保留矩阵。真实政策、真实试点、外部实收及生产放行由目标环境验收，当前生产结论为 **NO-GO**。[追踪矩阵](../r5/lite-traceability.csv) · [差异复核](../r5/differences.md) · [功能矩阵](../r5/feature-parity.csv) · [目标验收单](../../ops/target-acceptance.md)
