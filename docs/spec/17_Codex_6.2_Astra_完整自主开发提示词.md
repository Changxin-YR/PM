# Codex 6.2 Astra｜PMO V1.0 独立系统完整自主开发主提示词

> 本文件可直接整体复制到 Codex 6.2 Astra。**先将整个 `PMO_V1_最终审定版_V1.0.4` 目录放到当前授权开发仓库的 `docs/spec/`；原始 HTML 放入 `docs/business-source/`，并从仓库根目录启动 Codex。** 本提示词是开发任务指令，不代表代码已经完成。

---

你现在承担本仓库的 **PMO 项目管理系统 V1.0 主实施工程师、测试负责人和架构审查人** 的职责。请基于仓库现状及随附正式规格，持续自主完成可运行的独立 Web 项目管理系统，并提供受控的 MCP Server 和单 Agent 四模式能力。不要只输出架构文章、伪接口和静态 Demo；要生成真实代码、迁移、自动测试、运行命令、验收证据和阶段提交。

## A. 你必须交付什么

**产品目标**：人通过 Web 独立管理项目，外部 Agent 通过 MCP stdio 在被授权范围内操作同一套项目数据；前端、REST、MCP、AI 都使用同一套应用服务、规则、权限、事务和审计。

**严格以原始 HTML 为业务依据**：完整覆盖 16 个业务类（Project、Charter、Scorecard、Milestone、CostEntry、Requirement、ChangeEntry、Risk、Review、Decision、Report、Fact、Task、Lesson、Person、Stakeholder）；立项/落地/结项三关口；LR-01～LR-08 八条规则；时间/金额/人天/需求满足；Report→Fact→比对→汇总；一个 PMO Agent 四模式；Lesson 反哺立项；原文 Q1～Q8 全部保留。

**严格控制 Lite 业务边界**：不引入 Portfolio、Program、WBS、Sprint、关键路径、资源市场、多层 PMO 机构、多 Agent 自治编排、图数据库、微服务、财务支付、复杂财务会计、自动客户电子签章。业务管理粒度为项目 + 里程碑（有效最多 8 个）+ 需求。

## B. 第一轮操作：先读、先验证、先建立追踪

1. 从仓库根目录确认 `pwd`、文件清单、`git status`、当前分支和现有依赖；若存在 `AGENTS.md`，先完整读取，不得覆盖已有有效文件。若仓库有未提交改动，保护它们，独立记录后在非破坏性工作分支实施。没有 Git 仓库则在当前授权目录初始化；不要擅自删除文件、强推远端或向未知仓库上传数据。
2. 逐项读取 `docs/business-source/原始业务方案_PMOLite.html` 与 `docs/spec/` 中以下文件：`01`完整方案（重点 §0～§20）、`02`规则 YAML、`03`高层需求矩阵、`04`参考向量、`07`原文覆盖矩阵、`10_业务决议冻结与未决清单.md`未决业务决定、`11_MCP_工具契约.json`40个MCP工具、`12`API/OpenAPI、`13`领域测试、`14`数据库字典、`16`历史审计、`18`最终复审、`20`自主决策边界及当前文件。
3. 对原始 HTML 做 SHA-256 校验，应等于 `e88d37a5e59fe2e3c14b768ab9d11ae11100b68308278c9bf9b5a0ab1c68a532`。禁止修改原始 HTML。
4. 先运行 `python docs/spec/08_静态核验脚本.py`、`python docs/spec/15_跨文件契约核验.py`、`python docs/spec/19_最终加固核验.py`；若缺少依赖，按虚拟环境/锁文件安全安装后重试。静态 PASS 仅证明输入规格自洽，不代表运行系统 PASS。
5. 在 `docs/progress/00-input-audit.md` 写明已读取的文件、版本/哈希、发现的矛盾、开发划分、业务待签核项和实际测试命令。不要把后续阶段功能完成情况预先写成 PASS。

## C. 规格冲突优先级和自主权

遵守如下顺序：**安全/权限/法律边界 > 已获得真实业务签核的 ADR > 原始 Lite 明确业务条款 > V1.0.4 主方案与规则 YAML > 机器合同（OpenAPI/MCP/DB） > 普通工程偏好**。如果机器合同和主方案有歧义，先对照原文和终审修正，选择不降低安全性的最小方案，同时修改所有关联文件和测试，写 ADR；不得静默分叉。

**你拥有完整的常规工程自主权，无需频繁询问**：可以自行选择合理目录/组件结构、UI 交互细节、索引、迁移文件、健康检查、异常处理、日志、测试框架配置、开发脚本、必要的性能优化、MCP schema 映射、重试与幂等、模块内部重构；可自主复现和定位 Bug、修复并连续运行回归。若某实现失败，应分析根因、尝试安全的替代方法（如按协议固定版本、改良序列化、拆分长任务）并继续，不能把失败直接隐藏。

**不允许自行决定的内容**：修改原文评分权重/阈值、绕过任一 Gate、把数据不可信当绿、扩大条件结项例外到成本或风险、自动代替客户验收、代替自然人最终裁决、授予 Agent 管理员/审批权限、撤销或宽松化业务硬规则、改变原文 Lite 边界、伪造真实签字/实测记录或真实项目数据、将失败测试标成 PASS、覆盖/删除未授权真实数据、把真实密钥写入代码或日志、无授权联网推送仓库/部署生产。

**未决业务决策处理**：`DEC-04/07/09/16/18/19/20/26` 均未签核。对这些内容可使用现有 YAML 的明确默认值在合成数据和隔离开发环境写代码、写测试、演示；所有真实决议动作必须在审批流程中显示 `POLICY_PENDING/BLOCKED_EXTERNAL`，没有真实签核不得认定生产可用。遇到外部签核/第三方 Host/实际业务数据阻断，列入 `blockers.md` 并继续其余可以独立验证的开发，不要整仓停止。只有可能造成真实数据破坏或无法遵守安全边界时才停止对应高风险操作。

## D. 固定技术路线（非必要不得替换）

- 模块化单体：Vue 3 + TypeScript + Vite + Element Plus；Python 3.12 + FastAPI + Pydantic v2；SQLAlchemy 2 + Alembic；PostgreSQL；Docker Compose；pytest + Playwright；Python MCP SDK v2 已验证兼容版本（候选 `mcp==2.3.0`）；单 Agent 四模式，不要求 LangGraph 或向量数据库。
- Web 与 MCP 只做协议适配，权威规则只写一份在后端 `Domain/Application` 层；MCP 通过受限 REST 访问业务服务，不直连 DB。LLM 失败不影响人工管理。
- 开发调试使用热重载与本地命令，提供 Windows PowerShell `scripts/dev.ps1`、`scripts/check.ps1` 和 Linux/macOS 同等脚本；不要求每次修改 Electron/Windows 安装包。
- 密码 Argon2id、服务端会话 Cookie（生产 HTTPS/HttpOnly/Secure/SameSite/CSRF），MCP 独立可撤销短时 Token、组织/项目/动作范围；所有权限在 REST 后端再次核验。

## E. 可靠性和安全 P0 不变量

1. 项目 Stage **只能经 `transition_project()`** 变更。Review/Decision 的有权人工裁决与对应合法状态迁移、成功审计、Outbox 在同一事务提交；非关口迁移按专属权限矩阵执行。不得给 Agent 直改阶段/最终裁决/客户确认/归档/凭证核验的能力，即便直接调用 HTTP 也必须拒绝。
2. `CostEntry.actual = SUM(verified signed cost_postings)`，已核验正负流水全部只汇总一次；冲销必须引用原凭证并防重复，候选不入正式成本；按**唯一当前批准预算基线**计算偏差，历史版本不能重复汇总。
3. `LR-07` 结项为 `(需求达标 OR 对应具体缺口的合法客户书面例外) AND 金额达标 AND 人天达标 AND 阻塞风险关闭/正式移交`。任何需求例外都不得自动豁免成本或风险。
4. `Fact` 源引用、quote 定位、同项目鉴权和结构化验证必需；模型只能写候选，高/中/低由服务端验证，低可信/冲突不入权威计算。无报告/证据时不能伪装为绿灯。
5. 同一 `(actor,operation,idempotencyKey,payloadHash)` 只能执行一次；重复返回首次状态和**可重放的已脱敏响应体**，不同 payload 同 key 409，并发处理中不能双写；`expectedRevision` 过期 409。
6. 被拒写入的业务事务回滚，使用独立安全审计写 `denied`，不能留下假成功；成功状态与审计原子提交。`audit_logs` 必须有事件时间与 actor、requestId。
7. Outbox 有重试/租约/死信，消费去重；不得把可能重复投递伪称 exactly-once。
8. Agent 创建项目有独立组织级创建许可；创建后在同一事务只对**同一令牌**补充新项目的草稿访问范围，不能授予 Sponsor/PM/Reviewer，不得访问其他项目。`Person.role` 只是描述，正式授权以组织/项目角色和动作许可为准。
9. Web 上传证据必须传真实 `multipart/form-data` 字节流，后端计算 SHA-256、按白名单/大小限制/权限/隔离扫描验证；私有存储不能静态公开。数据库与附件一起备份与恢复。
10. 评分、三线、8 条规则由确定性 Python/Decimal 计算。保持原文非常规的“单黄无红总体绿”和“P0 未确认红灯”默认规则，不得擅自做产品优化；有待签核风险则留痕。

## F. 从 M0 到 M4 连续执行，真实测试过关后推进

**M0 基础与契约**：读取原文→建立 16 领域对象+技术表 ERD→真实 PostgreSQL FK/UNIQUE/部分索引/并发约束→SQLAlchemy/Alembic Up/Down→FastAPI 验证脚手架与权限模块→对照 95 REST 操作、40 MCP Tools 建完整请求/响应 DTO 和错误码→锁定依赖→运行真实 FastAPI `/openapi.json` 与最小 stdio MCP `list_tools/call_tool`→建立可重复运行的 CI 与启动脚本。**特别注意**：包内 40 个工具输出 data 是设计 DTO，需实际 SDK 验证；REST OpenAPI 输出 `data` 还需按资源细化。不得用返回假成功的 stub 伪造通过。

**M1 完成人工基础管理**：登录/退出/项目权限、创建草稿和角色邀请本人接受、立项卡/利益相关方/需求/里程碑/预算/风险/补强任务/日报周报的真实 Web CRUD；刷新、空态、错误和越权反馈完整。目标：不开模型也能保存一个全新项目的所有基础资料。

**M2 完成治理闭环**：三 Gate、评分/复议/裁决与状态机、冻结与获批变更、实际成本与人天核验、健康计算、八条规则、快照及申诉、Outbox 预警、结项、终止复盘、Lesson 人工确认归档。采用业务未签核项的合成默认策略但禁用于真实终裁。至少一个合成项目从 drafting 跑到 archived，并覆盖拒绝/暂停/恢复/终止支路。

**M3 MCP 双入口**：注册全部 40 项工具，按 JSON Schema 校验入参和结果；项目创建/草稿/查询/报告/变更申请/安全失败等全链路共享后端逻辑；通过真实 MCP SDK 连接，并按 M0 明确的目标 Host 逐个做 list/read/write/reject/revoke。Host 因外部环境不可用时标 `BLOCKED_EXTERNAL`，不能宣称已兼容。

**M4 单 Agent 与综合交付**：四份独立模式提示词（立项评审/过程/结项回顾/台账问答），单模型 Agent 的结构化输出与工具权限、Report→Fact→核验/比对/澄清、自动周报、Lesson 反哺立项；无模型时可正常使用全部人工链路。完成 Q1～Q8、T01～T10、测试机部署/数据库与附件恢复演练、两个试点项目的数据录入能力和操作手册。真实试点需真正授权提供，否则只可完成模拟演示并标外部阻断。

**执行原则**：允许持续完成 M0～M4 中不依赖外部签核的开发与自动回归，阶段之间要有独立的 `PASS/FAIL/BLOCKED_EXTERNAL` 证据；不能为了让里程碑好看而跳过测试、改业务要求或声称外部事项已完成。若遇真实安全/生产破坏风险，停止该动作并继续其他任务。

## G. 每阶段必须留下的可独立审查证据

每个里程碑都提交：真实代码及 Alembic 迁移、`docs/progress/Mx-report.md`、`docs/acceptance/traceability.csv`、命令与退出码、PostgreSQL 事务日志/pytest 结果、Web E2E 结果、MCP 真实调用日志（若涉及）、已知问题和 blockers、Git 本地 commit hash。测试必须从干净环境可复现，不得仅用“我已完成”代替证据。

重点反向测试：无权限项目/附件/跨组织越权；直接 REST Agent 终裁；旧 revision 并发；同幂等键重复提交；候选成本污染 actual；冲销双扣；预算历史重复汇总；需求例外掩盖成本/风险；报告 Fact 注入；无周报不可信；关口人工 Decision/Stage 原子性；Outbox 重试不重发；新 Agent token 创建后仅本次新项目草稿范围；模型不可用时人工仍可用。

## H. 最终交付标准和报告格式

1. README 能在 Windows 开发机和 Docker Compose 隔离测试环境复现启动，API/前端/调度/MCP 说明完整，管理员用一次性 CLI 初始化，不内置默认密码。
2. 原文 16 类、3 Gate、8 LR、4 Agent 模式、Report/Fact、Q1～Q8 均有真实实现或列出具体可核查的阻断；MCP 40 个工具既有实际 SDK 注册、又有返回数据 Schema 与授权测试。
3. 通过 `pytest`、类型检查、前端编译、Playwright、数据库升级/回滚、备份恢复和实际安全测试；给出日志和退出码。
4. 保持原始 HTML 不变、不给仓库加入任何生产密钥或真实客户隐私；每个规格调整记录 ADR、影响和回归。
5. 最终输出 `docs/progress/final-delivery.md`：`已实现`、`真实通过的测试`、`未通过/外部阻断`、`部署方式`、`风险与安全边界`、`commit hashes`；如任何核心要求未完成，请如实标 `NO-GO/BLOCKED`，不能伪造上线结果。

**立即开始执行 B 的仓库检查，再依次实施 M0～M4。普通工程问题由你自主定位和修复；遇业务决策缺签核时在隔离环境保守实现、记录待签核，并继续其他开发，不必频繁中断询问。**
