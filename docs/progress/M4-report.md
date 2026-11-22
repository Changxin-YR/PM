# M4 四模式、证据与恢复：未整体验收

2026-11-20：`tests/test_outbox.py` 在随机 PostgreSQL schema 实测本地事件消费恢复，包括过期租约、重复消费、退避和死信。消费回执不是站内通知送达证明，更不是全库或附件备份恢复演练。

## 2026-11-20 数据库与私有附件联合演练

在本机 PostgreSQL 16.14 Docker 上，从 `template0` 新建并迁移合成源库。实际 `pg_dump --snapshot` 导出与附件清单相同的事务视图。测试在快照建立后、`pg_dump` 启动前提交第二个附件；源卷上的晚提交对象和孤儿候选均未进入恢复点，亦未被删除。实际 CLI `restore-drill` 生成隔离新库；从新库读取项目、里程碑附件关联、大小及摘要，再用应用读取器校验原件。源文件缺失不发布新点；归档附件被同长替换、dump 被篡改均拒绝；通过摘要但不是合法 PG 归档时，实际恢复失败并清理新库。测试不启动活跃应用的 Outbox，不恢复真实数据。

定向测试 2 passed；规格三检与 mypy 通过。将恢复提交 `ca21139` 导出为干净目录，用相同本地 PostgreSQL 重跑后端套件，39 passed / 54.42s（退出码 0）。同期共享工作区全量回归 37 passed / 2 failed：进行中的关口送审同时增加了 0016 与四个 MCP 工具，运行时测试当时仍严格断言 0015 和旧集合。干净提交的结果不覆盖尚未合并的关口送审功能。每日计划、七日保留及每周演练流程见 `docs/ops/backup-and-recovery.md`；未安装周期任务、未积累七天备份或验证真实目标环境。

## 2026-11-20 PostgreSQL 运行账号隔离（起点 d71556f）

迁移 0016 的独立合成库使用三个不同的非超级用户登录，验证所有者迁移及套用白名单、受限 API 登录/项目修改/越权审计/附件/凭证/Agent 令牌签发撤销，以及独立 Outbox 账号投递回执和一条合成站内通知。实测禁止运行账号改写审计、更新授权、创建对象、切换到所有者、直接修改历史 `actual` 或填入非零值；Outbox 无权读账号、代理令牌摘要和附件元数据。验收库与测试角色在用例退出后清理。没有切换开发预览或目标环境的连接账号。

关口送审与本批合并回归：`dev.ps1 test` 44 passed / 69.71s，`dev.ps1 browser` 1 passed / 46.18s，`dev.ps1 check` 三套规格检查与 mypy 53 文件通过。角色白名单以定向 PostgreSQL、HTTP 和 worker 验证为准，不能把其余以普通测试连接运行的用例视为目标生产部署的验收。操作步骤见 `docs/ops/database-roles.md`。

四模式、Fact 来源/核验/冲突、Lesson 反哺、八规则宕机补跑、性能基准、两真实试点及业务签核尚无完整证据。Q1～Q8、T01～T10 不作整体验收通过结论；生产 NO-GO。

## 2026-11-21 0023 数据升级、角色与联合恢复

见 [0023 源码树、环境与命令](M2-report.md)。`tests/test_recovery.py` 在 0016 保留原项目、旧报告、旧健康条目及里程碑附件，带数据升至 0023，通过独立人工会话核验原件、实名提交和发布周报、采样健康；真实 `pg_dump/pg_restore` 后以 API 回读原快照、报告内容哈希、作者/确认身份和附件哈希，旧无回执报告不补签。最终部署候选仍另验。`tests/test_database_roles.py` 用 API 账号运行独立扫描进程并验证重跑无额外任务和投递，Outbox 账号只消费通知且无权读取快照/文件。没有修改开发库的版本、现有 ACL 或建立后台计划任务。

## 2026-11-21 0024 Fact 原话、核验与澄清

源条款：§8.4–8.6、§17.3、§20.4、T06，见 [ADR 0012](../adr/0012-fact-source-and-clarification.md)。基准 SHA `00c1d6193c9966dce7a730830e19c95f0522ae74`，最终被测源码树 `b97068706675bddc5054c490f1b93279ed2957ee`（不含后补证据文档）。实现：`pmo/facts.py`、`pmo/fact_sources.py`、`pmo/fact_store.py`、`migrations/versions/0024_fact_source_receipts.py`、`web/src/ReportFacts.vue`。环境：Windows、真实回环 HTTP 与 stdio、PostgreSQL 16.14 随机隔离 schema/专用数据库、真实 Edge，合成账号和带已知数值的原件；无外部模型、真实数据、试点或业务签核。

| 实际命令 | 结果 | 证据 |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_database_roles.py tests/test_recovery.py tests/test_facts.py tests/test_reports.py tests/test_runtime.py --tb=short` | PASS，退出 0；17 项 / 41.76 秒 | `.local/fact-0024-integration.log`。原话、无出处、跨项目、模型式文本、Agent 权限、原件损坏、冲突、幂等与 Outbox 故障回滚；专用库用受限 API 写入今次回执，Outbox 无权读 Fact；带 0016 数据升级、拒绝丢 Fact 的降级，真联合恢复后核对高可信 Fact、作者/核验记录与原件。该定向验证先于任务列表接入，最终回归包含任务闭合。 |
| `scripts/dev.ps1 test` | PASS，退出 0；100 项 / 147.57 秒 | `.local/fact-0024-full.log`。维护会话、冻结、关口、任务、快照等既有用例；新验 PM 只能随真实澄清关闭对应任务。仍有一项 Alembic 配置弃用警告。 |
| `scripts/dev.ps1 browser` | PASS，退出 0；真实 Edge 7 项 / 121.99 秒 | `.local/fact-0024-edge.log`；新增 `web/e2e/facts.mjs`，PM 提报、独立 Reviewer 核验、冲突、PM 澄清和完整刷新，覆盖移动/桌面视口。 |
| `scripts/dev.ps1 check`；`npm.cmd --prefix web run build` | PASS，分别退出 0 | `.local/fact-0024-final-check.log`、`.local/fact-0024-build.log`。静态规格、mypy、Vue 类型与 Vite 构建通过；仍有主包体积警告。 |

失败记录：初次回归 `.local/fact-0024-targeted.log`（11 通过、3 失败，退出 1），修复 Meta 不含 revision 的异常与 UUID 测试序列化；初次静态加固脚本拒绝不认识的 `type` 数组，改用等价封闭 `oneOf`；首次 Vue 类型检查报告隐式分页参数，明示数值类型后按上表验证。未删失败断言。

`app.openapi()`/`ROUTES`/工具注册对表 PASS：90/95 基线 + 25 工程接口，40/40 注册且真实 stdio 枚举。剩 1 项报告分析和 4 项组织管理；不将注册等同所有调用或目标 Host 实验。四模式和全套 T06 仍待验收，人工事实工作区不受模型开关影响。

## 2026-11-21 0025 报告分析

源条款：§6、§8.5–8.6、T06；[ADR 0013](../adr/0013-report-analysis-jobs.md)。基准 SHA `daa28949db1045a4db63478a80bb03a2b1926767`，被测源码树 `315123539507ec073988ac671030b072ba979562`（不含本批证据文档）。实现：`pmo/report_analysis.py`、`pmo/report_analysis_worker.py`、`pmo/model_gateway.py`、`migrations/versions/0025_report_analysis_runs.py`、`web/src/ReportAnalysis.vue`。环境：Windows、PostgreSQL 16.14 随机 schema/专用数据库、真实 HTTP/Edge，模型关闭或回环 HTTP **合成网关**。未启用外部模型或上传实际报告。

| 实际命令 | 结果 | 证据 |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_report_analysis.py tests/test_database_roles.py tests/test_recovery.py --tb=short` | PASS，退出 0，10 项 / 75.36 秒 | `.local/analysis-0025-integration.log`。主体级重放、并发领取、旧租约、Outbox 故障回滚、新版本/退出/角色撤销、恶意提示与封闭 Schema、出处、超时。受限 API 账号可领取任务并转人工，Outbox 无权读分析收据；0016 旧报告、会话与原件带数据升级，真实联合恢复后回读已定位建议与附件；拒绝会丢分析历史的降级。 |
| `scripts/dev.ps1 test` | PASS，退出 0，107 项 / 181.60 秒 | `.local/analysis-0025-full.log`。既有评分、裁决、关闭模型完整生命周期与真实 stdio 回归。仍有 Alembic 配置弃用警告。 |
| `scripts/dev.ps1 browser` | PASS，退出 0，真实 Edge 9 项 / 153.05 秒 | `.local/analysis-0025-edge.log`。新增 `web/e2e/analysis.mjs`：手机视口关闭模型转人工，桌面视口模拟建议、刷新重读、人工明确提报。 |
| `scripts/dev.ps1 check`；`npm.cmd --prefix web run build` | PASS，分别退出 0 | `.local/analysis-0025-final-check.log`、`.local/analysis-0025-build.log`。规格三检、mypy、Vue 类型和 Vite 构建通过；Vite 仍有主包体积警告。 |

失败修复：`.local/analysis-0025-regression-first.log`（1 通过、13 错误，退出 1）定位历史迁移共享的会话元数据导致重复列；保留原元数据，后续回归 `.local/analysis-0025-regression.log` 14 PASS。`.local/analysis-0025-worker-first.log`（6 通过、1 失败，退出 1）修正测试中 `1.40` 原件却使用 `2.50` 候选的输入，保留原话拒绝。`.local/analysis-0025-edge-targeted.log`（1 通过、1 失败，退出 1）补齐模拟模式的测试启动器导入，再按上表通过。`npm --prefix web run typecheck` 非仓库命令，正式 `build` 已执行类型检查。

目录对表 PASS：OpenAPI 按 operationId、HTTP 方法、路径为 **91/95 基线、26 工程接口**，缺项 `admin_members_list`、`admin_users_list`、`admin_user_invite`、`admin_user_update_status`；`ROUTES` 与 `mcp_server.tools` 均 40。真实模型及各目标 Host `BLOCKED_EXTERNAL`。周报自动初稿、完整四模式、T06 整体和生产批准未通过验收。

## 2026-11-21 0027 无模型周报初稿

源条款：§5.6、§8.5–8.6、§20.3、DEC-22、SRC-069/072/074。基准 SHA `89b630830e0527136585d14c1ef17aa48749afff`，被测源码树 `1ede5adc5c35ed1af3e3ec89fd9d0e80a8a6deac`（不含证据文档）。实现：`pmo/weekly_drafts.py`、`pmo/weekly_basis.py`、`pmo/reports.py`、`web/src/Reports.vue`、迁移 `0027`，[ADR 0015](../adr/0015-weekly-report-basis.md)。环境：本机 Windows、PostgreSQL 随机 schema/专用数据库、合成会话、模型关闭的真实回环 HTTP/Edge。非业务签核。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `pwsh -NoProfile -File scripts/dev.ps1 test` | 0 | PASS：115 项 / 181.92 秒，`.local/weekly-0027-full.log`。四区块、所选周人天固定边界、冲销、候选与争议排除、过期拒绝、来源只读、Agent/跨项目拒绝、并发幂等及发布 Outbox 故障回滚见 `tests/test_weekly_drafts.py`；0016 数据升级及恢复后查回已发布周报、来源、原件和旧历史见 `tests/test_recovery.py`；受限 API/Outbox 白名单见 `tests/test_database_roles.py`。 |
| `pwsh -NoProfile -File scripts/dev.ps1 browser` | 0 | PASS：Edge 11 项 / 143.21 秒，`.local/weekly-0027-edge.log`。新增 `web/e2e/weekly.mjs` 真实生成、编辑、提交、PM 发布、独立成员查看及刷新回读来源。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check` | 0 | PASS：规格三检与 mypy，`.local/weekly-0027-check.log`。 |
| `npm.cmd --prefix web run build` | 0 | PASS：Vue 类型与 Vite build，`.local/weekly-0027-build.log`；有包体告警。 |

失败和修复：首轮后端类型缺 JSON 值注解，首轮 Vue 类型缺 `projectRevision`，均修复。`.local/weekly-0027-affected-first.log`（退出 1）修正了测试的私有草稿 404 与审计编号字符串类型断言，继续断言提交后非 PM 403 和无成功审计。`.local/weekly-0027-affected.log`（27 PASS，1 FAIL，退出 1）发现旧 Fact 回滚测试跨项目计算整库数量；改为对本测试项目断言零残留，未去除失败时必须回滚的断言。全量终态按上表。

基线 REST 95/95、工程接口 30、MCP 注册 40/40；周报工程接口保持人工专用，不增加 Agent 工具。独立的真实模型、各目标 Host、试点与业务签核仍 BLOCKED_EXTERNAL。

## 2026-11-22 项目助理四模式

源条款：§7.1、§8.5–8.6、Q1/Q2/Q5/Q6、T08。项目资料统一页签及四个封闭只读输出：立项预检仅结合已裁决的评分摘要及授权可见、已确认的历史 Lesson；过程限现任 PM，复用三线、规则事件、Fact 澄清和确定性周报，并展示同报告版本/哈希的异步分析候选；结项用正式门槛与四问，引用实际复盘摘录但不落库；台账问答仅支持受控主题和可核验引用，不用旧快照填补不可信结果。写操作均回各自工作页，并保持人审、版本及权限。Agent 仍经已有 MCP 工具访问同一服务，不能直调人工助理接口。实现 `pmo/assistant.py`、`web/src/ProjectAssistant.vue`；未新增迁移、未使用真实模型。

基准 `ec230048ffb5f7a4feadaf79cc81ae66bdb9a1cb`，被测助理源码树 `6b46af24b1efd6a138add240bce5e04f268c2bc7`（含模拟网关浏览器用例，不含进度文本与独立 MCP 发行改动）。Windows、实际 PostgreSQL 16 隔离 schema、回环 HTTP、Edge；合成会话、模型关闭或回环模拟。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_assistant.py tests/test_lifecycle.py tests/test_health.py tests/test_facts.py tests/test_weekly_drafts.py tests/test_retrospectives.py tests/test_cost_math.py --tb=short` | 0 | PASS：35 项 / 58.35 秒，`.local/assistant-affected.log`。验证待审评分不泄露、正式总分和引用一致、重复读取不写入、跨项目及 Agent 拒绝、Fact 争议剔除、周报摘要指纹一致、三线不可信时不用旧快照、结项缺口与正式预检一致、已确认 Lesson 可反哺新立项。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py::test_browser_read_only_project_assistant_without_model tests/browser_case.py::test_browser_report_analysis --tb=short` | 0 | PASS：Edge 3 项 / 31.84 秒，`.local/assistant-edge-modes.log`。移动切换四模式、拒答及刷新；关闭模型转人工、模拟网关候选回读，仅人选用后进入低信任候选。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：规格、mypy 与 Vue 类型，`.local/assistant-check.log`。 |

初次定向 FAIL（3 项 PASS、1 项 FAIL，`.local/assistant-affected-first.log`）：用例未先办理进入复盘，产品正确拒绝写入。修正用例按真实审批流程推进后复验。运行目录 95/95 基线 REST + 38 工程接口，40 项 MCP 工具不变。四模式在合成工作台可用；过程模式的外部模型仅在授权下配置，其余模式当前采用确定性建议/有来源的模板输出，未验真实模型、真实试点或签核。

## 0028 冻结参考向量首批

依据：§4、§5、§20.4 与规格包 `04_PMO_V1_规则测试向量.json`。基准 SHA `e52fd065a441367f32824b62361f3bc95c2f9c13`，验证源码树 `5e140f7ed6a9ff5b7f728482123a63c7d07bc3db`（含测试、生成器及逐项 CSV，不含本段）；Windows、实际 PostgreSQL 16 随机 schema、真实回环 HTTP，合成身份与禁用模型。运行产品 `review_scores`、`cost_math`、`health_calculation`、`reports` 和 REST，不执行规格包中的参考实现。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_reference_vectors.py --tb=short --junitxml=.local/reference-vectors.xml` | 0 | PASS：44 项 / 27.69 秒；8 条评分、16 条成本、6 条健康、2 条需求、4 条周报、8 条状态。`PMO_TEST_DATABASE_URL` 依 `dev.ps1 test` 从本机开发配置组装；共享开发 schema 未迁移。 |
| `.venv/Scripts/python.exe scripts/reference_vector_evidence.py .local/reference-vectors.xml docs/acceptance/reference-vectors-0028.csv` | 0 | [102 条逐项结果](../acceptance/reference-vectors-0028.csv)：PASS 44、FAIL 0、NOT_RUN 58；未匹配测试结果的冻结输入不计为通过。 |

此前同一 pytest 命令两次退出 1：首次将 `.local/dev.env` 的口令误作 URL，22 项未完成 fixture 初始化；按开发脚本组装 URL 后，6 项凭证用例误取写入回执中的 `revision`（`.local/reference-vectors-dto-fail.xml`），改为人类账簿读取实际明细版本后核验，未改产品契约或放宽断言。

## 0028 全部 102 条冻结向量产品复验

依据：冻结 `04_PMO_V1_规则测试向量.json`，主方案 §4、§5.6、§6、§7、§20.4–20.6。基准 SHA `c2ca7c29698478f7b4cc789a19e47c137f855c5e`，被测源码树 `49c5ec6605f06b3ea99071849c556a1ba85c7f31`（含生成的逐项 CSV，不含本段）。Windows、PostgreSQL 16 随机 schema、真实回环 API/stdio，纯合成授权、独立账户、预算/凭证、原件与时点；超时用例单独启用回环模拟网关，不用真实模型。未变更规格原件、产品逻辑、数据库迁移或共享库。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `$vectorFiles = (Get-ChildItem -LiteralPath tests -Filter 'test_reference_vectors*.py').FullName`；`.venv/Scripts/python.exe -m pytest -q @vectorFiles --tb=short --junitxml=.local/reference-vectors-all.xml` | 0 | PASS：102 项 / 113.97 秒。各输入有独立 pytest 节点；需求分母仅用获批生效版本，成本与风险独立阻断，顺带实测 LR-01～08 全组冻结边界、扫描重跑、受控权限错码、降级保留报告。Sponsor 说明场景使用认证后提交的说明报告，不作为预算修订。 |
| `.venv/Scripts/python.exe scripts/reference_vector_evidence.py .local/reference-vectors-all.xml docs/acceptance/reference-vectors-0028.csv --require-complete` | 0 | [逐项结果](../acceptance/reference-vectors-0028.csv)：PASS 102、FAIL 0、NOT_RUN 0。缺项、跳过或失败无法满足完整性检查。 |

中间 FAIL（退出 1）：治理 3 项修正审计请求号文本比较和 ADR 0010 约定的无承诺整改日期（合法条件通过任务的未来期限另有测试）；`.local/reference-governance-signals-first.xml` 两项误用未公开里程碑 ordinal；`.local/reference-closing-first.xml` 一项误用报告字段；`.local/reference-permissions-first.xml` 一项在送审后用已失去材料可见性的发起人签发令牌，改用有合法授权的 PM 并先证明令牌确实可读该评审。保留拒绝和无副作用的断言。上述结果只覆盖冻结输入，不代替 A/Q/T 端到端组合、真实 Host、模型或试点。

## 0028 补充验收 A01–A16 与 Q3

依据：`22_最小业务验收基准样例.md`、§12.1 Q3、§14 第 1 项。基准 SHA `bf3b0e2d68c0d0ba6147c24813db3deb257715d8`，被测源码树 `97e286aa9cb89d540ccac23136767068795021be`（不含本报告）。Windows、PostgreSQL 16 隔离 schema、真实回环 HTTP/stdio、Edge；合成身份、原件及入账数值，关闭模型。每条样例映射到实际测试节点，见[追踪矩阵](../acceptance/traceability.csv)。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_acceptance_baseline.py tests/test_forecast_governance.py tests/test_reviews.py::test_charter_veto_appeal_preserves_opinions_fast_confirmation tests/test_postings.py::test_verified_cost_cycle tests/test_reference_vectors_signals.py::test_due_week_threshold tests/test_health.py::test_live_snapshot_appeal_and_stdio_scope tests/test_runtime.py::test_concurrent_replay_scope_and_denial tests/test_lifecycle.py::test_termination_and_rejected_resubmission_use_separate_histories --tb=short --junitxml=.local/acceptance-supplemental-final.xml` | 0 | PASS：17 项 / 40.16 秒。覆盖 A15 外的 15 个样例，并验证一周期缺报对照以及 Q3：待审变更不更改计划，批准后预警解决，旧时间线与正式账簿、当前 MCP 读数均可查。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py::test_browser_drafting_through_archive_without_model --tb=short --junitxml=.local/acceptance-model-off-edge.xml` | 0 | PASS：1 项 / 45.00 秒。A15，同一项目在真实 Edge 办理立项卡、独立评审、里程碑、P0、客户与 PM 基线双签、双口径预算、落地关口，明确显示模型关闭；独立附件、凭证和验收材料通过真实 HTTP，Web 风险关闭、对账、结项评审、复盘、Lesson 及归档。PostgreSQL 回读九条阶段历史。 |

初次定向 FAIL（退出 1，`.local/acceptance-supplemental-retest.xml`，16 项通过）：A05 误断言不可变已核验凭证的健康引用含 `@2`，改为匹配当前契约的精确 `cost-posting:<id>`；仍强制检查 70/50/120、成本红和零重估任务。这批补充样例 PASS 16/16，仅为合成验证、不代表真实签核、目标 Host 或整体验收。

## Q7 每周三线及原因

依据：原文 Q7、独立方案 §5、§7.2、§12.1。基准 `438da6bc937589a175609329f276265e122b7626`，被测源码树 `6bd5a2c8405364c900428aecbb7b2702dfd6113e`（不含后补文档）。`pmo/weekly_health.py` 按授权项目及北京时间自然周分页查询，只从同一采样的持久化输出、输入和事件得到三线及原因。周内最近采样与早先黄红分开；不可信不显示三线颜色，无样本不报绿。`web/src/WeeklyHealthOverview.vue` 接入预警中心。95/95 基线 REST、39 工程操作、40 注册工具；无迁移。Windows、真实 PostgreSQL/HTTP/Edge，合成同组织账户，模型关闭。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_weekly_health.py tests/test_runtime.py::test_runtime_has_all_baseline_methods_paths_and_tool_mappings tests/browser_case.py::test_browser_weekly_health_explains_single_yellow tests/browser_case.py::test_browser_lr08_monthly_uses_deadline --tb=short --junitxml=.local/weekly-health-final.xml` | 0 | PASS：4 项 / 21.09 秒。单黄总体绿、成本红、缺证据不发布、同周旧异常只作历史、固定时点分页、上周空样本、非法周、同组织无授权不泄露、Agent 直调拒绝。Edge 1280/390 像素，确认原因及快照，LR-08 按截止月归集仍通过。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：规格和 mypy 82 文件，`.local/weekly-health-check.log`；Vue 类型。 |

首次 API FAIL（退出 1，`.local/weekly-health-api.xml`）：缺人工操作白名单；添加本操作但未加入 Agent 路由。首轮 Vue 类型 FAIL 修复分页回调类型；下一轮 `.local/weekly-health-retest.xml` 退出 1，因为用例默认历史周并非当期，改为提交实际采样对应的周并保留全部断言。

## 0028 隔离部署、运行探针与性能

依据：主方案 §9.1、§14 第 6 项、REL-001。基准 SHA `a323905be7c81d1b7cb18640c9ccff63edb17dba`，被测部署源码树 `7c84ae1cd1f4bbb9029d508760d46afad0ca09b3`（不含本证据及并行 MCP 动作回归）。实现：`deploy/compose.yaml`、`pmo/health_probes.py`、`pmo/observability.py`、`pmo/database_roles.py` 与独立扫描和 Outbox。部署与失效处置见[手册](../ops/isolated-deployment.md)。

环境：Windows 构建 26300、Ryzen 9 7945HX（32 逻辑 CPU）、16 GiB 主机内存，Docker Desktop Engine 29.8.2（约 5.8 GiB），Compose 5.6.0，PostgreSQL 16.14；Edge `158.0.4370.33`、1280×900。合成隔离 Compose `pmo104-rc-0c3c7df1`，只发布回环 Web，数据库和私有文件使用独立卷。实际 head `0028`，共享开发库只读回核为 `0016`，未升级；无真实项目、外部模型或业务签核。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `docker compose -p pmo104-rc-0c3c7df1 --env-file .local/pmo104-rc-0c3c7df1.env -f deploy/compose.yaml up --build -d --wait` | 0 | PASS，空库初始化、迁移退出 0、API 就绪后起 Web，见 `.local/pmo104-rc-0c3c7df1-build.log`。 |
| `.venv/Scripts/python.exe scripts/deployment_smoke.py pmo104-rc-0c3c7df1` | 0 | PASS，实际连接角色为非超级用户 API、扫描和 Outbox。登录并正式写入 15 个草稿和 15 条里程碑，未赋项目审批角色；15 条创建事件均有唯一已投递回执。实停隔离 DB，`live` 200、`ready` 503；恢复双 200，草稿和回执数量不变，退出登录成功。三轮热身后各测 30 次：筛选从点击到结果渲染 p95 **37 ms**，详情到真实里程碑显示 p95 **43 ms**，本机合成数据低于 1 秒。`.local/pmo104-rc-0c3c7df1-smoke.log`。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_mcp_action_matrix.py tests/test_operational_checks.py tests/test_outbox.py tests/test_database_roles.py tests/test_runtime.py::test_runtime_has_all_baseline_methods_paths_and_tool_mappings --tb=short --junitxml=.local/operational-final.xml` | 0 | PASS，9 项 / 39.84 秒。探针错误版本、私有目录、日志脱敏、受限账号与重复投递；同时包含 MCP 动作矩阵。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_recovery.py tests/test_database_roles.py tests/test_error_contract.py --tb=short --junitxml=.local/rc0028-recovery.xml` | 0 | PASS，4 项 / 34.62 秒。专用合成库从 `0016` 带数据升级到 `0028`，真实 `pg_dump/pg_restore` 与私有附件一起恢复；回读旧立项和附件、健康输入、日报周报及来源、Fact 原话、核验及争议、开户审计和分析收据。损坏归档、异地/重叠恢复路径和有记录的破坏性降级被拒绝。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；OpenAPI 按 operationId、方法及路径与冻结表对照 | 0 | PASS，三套规格及 mypy 84 文件（`.local/operational-check.log`）。95/95 基线 REST、41 工程操作（新增两个探针）、40 MCP 注册。 |

只证明合成候选版的启动、探针、基础写入和本机性能；目标环境 TLS、凭据轮换、告警与周期备份、试点性能须独立验收。未作生产发布。

## 0029 组合验收与审计升级

依据：§12.1–12.2、§20.3–20.6。基准 SHA `6c0dfe2a745a6f9cb7bd86925aa2e2f69a3284b9`，首个固定源码树 `b0a49af21ad802215de95f832d791b20fda09953`；仅追加成本故障测试后的源码树 `d6a3771e50b9587389a92e0f220c6a161df92459`。Windows、Edge、真实回环 HTTP 与隔离 PostgreSQL 16，合成账号，模型关闭。共享开发库只读回核 `0016`。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| [固定源码树后端全量（含 stdio）](M3-report.md#0029-逐项授权副作用与查询审计) | 0 | PASS：258 项；同轮包括 102 个参考向量、八规则关键边界、宕机补跑和 Outbox 去重、`0016→0029` 带数据升级、`pg_dump/pg_restore` 和私有附件联合恢复。旧审计无来源，新 Agent 审计按原请求号回读。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/rc0029-edge.xml` | 0 | PASS：17 项 / 183.55 秒，`.local/rc0029-edge.log`。无模型从 `drafting` 经三关口到 `archived`，回读九条迁移、16 个补充样例及终止、例外、争议等路径。`test_browser_and_stdio_same_snapshot_and_private_resources`：同项目、版本和同一时点的正式快照一致；同组织越权与报告/附件路径拒绝。首次定位问题在 `.local/dual-entry-rescoped.log`；完善定位和 `baseURL` 后 `.local/dual-entry-0029.log` PASS。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_postings.py --tb=short --junitxml=.local/rc0029-cost-atomic.xml` | 0 | PASS：2 项 / 10.70 秒（金额、人天），`.local/rc0029-cost-atomic.log`。Outbox 故障下核验仍 pending、actual 不变、无成功审计、事件或幂等回执；恢复后两个独立核验者并发仅一人成功，回读 `cost-posting` 版本和响应哈希。 |

首轮后端 `.local/rc0029-backend.log` 退出 1：LR-01 两例误以为 PM 在草稿/立项评审中可签发阶段请求动作，客户确认用例的读取路径未定义。修复测试：明确检查签发拒绝、正常 scoped 读工具与阶段请求分别验证；构造实际资源 URL，保留客户本人 Agent 对直接 HTTP 的拒绝和人工正例。工作树启动后曾因并行开发给既有抽取网关增加 `tools` 字段而失败（`.local/rc0029-backend-verified.log`，退出 1）；旧契约保留，审计批以固定源码树独立复跑。规格与类型 `.local/rc0029-spec.log` PASS；候选 Web 构建 `.local/rc0029-build.log` PASS，保留体积告警。Q1–Q8、T01～T08/T10 是合成产品证据；T09、试点、真实签核及生产放行不计入。

## 0029 四模式网关与人工降级

源条款：§8.5–8.6、§20.3、Q1/Q2/Q5/Q6/Q7、T06/T08。基准 `e5752ad564f8360b5a7c4647cc24842528c6ce5a`，被测源码树 `c02013cea625d52c130d8f20ef1aaa721d6f2a24`（不含补录文档）。实现及接口见 [ADR 0018](../adr/0018-source-bound-assistant-previews.md)：`pmo/assistant.py`、`pmo/assistant_model.py`、`pmo/assistant_preview.py`、`pmo/model_gateway.py`、`web/src/ProjectAssistant.vue`。Windows、本机 Edge、真实回环 HTTP/stdio、PostgreSQL 16.14 隔离合成 schema/专库、回环模拟网关；不使用外部模型或真实业务资料。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/assistant-model-mcp-final.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/assistant-model-backend-final.xml` | 0 | PASS：264 项 / 384.53 秒，`.local/assistant-model-backend-final.log`；包含恶意提示、Schema 附加字段、未知来源、矛盾、超时、并发重放、失权、旧材料与人工降级；MCP 逐工具 JSON 与既有 `mcp-stdio-0029.json` 字节一致。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/assistant-model-edge-final.xml` | 0 | PASS：18 项 / 197.06 秒，`.local/assistant-model-edge-final.log`。模型关时同一项目全生命周期；开启模拟网关时，三个预览与正式数值呈现分离，过程模式仍走原异步提取。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_database_roles.py tests/test_recovery.py tests/test_operational_checks.py --tb=short --junitxml=.local/assistant-model-recovery.xml` | 0 | PASS：6 项 / 39.46 秒，`.local/assistant-model-recovery.log`。受限账号预览收据更新不能覆盖正式业务回执；带数据升级、联合恢复及就绪探针。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：三套规格静态检查、Python 87 文件及 Vue 类型，`.local/assistant-model-check-final.log`。运行 `app.openapi()` 按方法、路径、operationId 比对：基线 95/95、工程 42；40 MCP 不变。 |

隔离 Compose `pmo104-rc-0c3c7df1` 用 `docker @stack build` 构建候选镜像（PASS，`.local/rc0029-compose-build.log`，保留 Web 体积告警）；与同一已备份停写的隔离库升级配套。`0028→0029` 后保留 15 项目、30 条消费回执、33 条旧审计；就绪/存活均 200，匿名预览 401，两个模型关闭。最初从 Nginx 入口探测内部 OpenAPI 未找到；改从 API 容器内复核，未向代理增加公开路由（PASS，`.local/rc0029-compose-verify.log`）。真实模型版本及批准外发、目标 Host、两试点和签核各自仍 BLOCKED_EXTERNAL。

## 0030 综合复验与物理关系图

依据：§3、§4.1、§20.3–20.6、T07/T10。基准 SHA `1e8e6e60623bd6c206acba282da597350749366b`，综合实测源码树 `8957c1e2dc493e0aba383432660c11b9c19e827c`；后续仅追加终止归档断言，见 [M2](M2-report.md#独立终止裁决与暂停项目复盘)。Windows、Edge、回环 HTTP/stdio、PostgreSQL 16.14 隔离合成 schema/Compose。关系图由 `scripts/schema_erd.py` 只读生成：78 张业务及技术表、202 个物理外键；421 个冻结字段均在实际库中。`decisions.review_id` 对非关口裁决允许为空，见 [ADR 0011](../adr/0011-closing-evidence-and-lifecycle.md)。字段类型、索引和约束语义不能据字段存在直接判定一致。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_runtime.py -k 'postgres_erd or stage_catalog or postgres_contract' --tb=short --junitxml=.local/schema-0030-runtime.xml` | 0 | PASS：3 项 / 13.70 秒，`.local/schema-0030-runtime.log`；实际 FK 对表，23 条服务状态边与冻结 YAML 一致；用实库 CHECK 验证带评审和审批回执的合法/非法状态组合。 |
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0030-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0030-backend.xml` | 0 | PASS：269 项 / 399.32 秒，`.local/rc0030-backend.log`；逐项 MCP JSON 与 `docs/acceptance/mcp-stdio-0029.json` 字节一致。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/rc0030-edge.xml` | 0 | PASS：19 项 / 205.16 秒，`.local/rc0030-edge.log`；包含结项撤回后的执行返工及完整无模型闭环。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit`；`docker @stack build` | 0 | PASS：规格、类型、候选镜像；`.local/rc0030-check.log`、`.local/rc0030-compose-build.log`，保留 Web 包体积告警。 |

隔离 Compose `pmo104-rc-0c3c7df1` 停写备份后以 `docker @stack up --no-deps --abort-on-container-exit --exit-code-from migrate migrate` 迁移，再 `up -d --wait` 启动候选服务，退出 0（`.local/rc0030-compose-verify.log`）。升级 `0029→0030` 保留 15 项目、30 消费回执、33 审计；存活/就绪 200、两模型开关关闭。该合成运行库不含附件原件；带附件升级和恢复单独见 [M2](M2-report.md#0030-结项撤回按冻结状态表回到执行)。共享库未迁移。以上不改变 T09、真实模型、试点、签核或生产状态。

## 0031 财务有限数及续版恢复

依据：领域不变量 §2、§3、数据库一致性第 2 节、主方案 §9.1、LR-04/LR-07、FND-002、T10。基准 `bb0cdb771fdc6a5af5c50fc19447b0f4998c891a`，验证源码树 `f9fc46a663390f7c9cba67836e9294e9fe7fb963`（不含证据文档）。Windows、PostgreSQL 16.14、本机回环 HTTP 与隔离 schema/专库，合成数据。`migrations/versions/0031_finite_costs.py` 追加约束，保护预算、计划预测、历史基线和有符号的凭证金额；金额/人天继续采用 `NUMERIC`，合法的 0、负向冲销及可空的计划字段不变。升级校验存量，异常则整个迁移回滚，不纠正旧值、不改写已应用迁移。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q tests/test_postgres_invariants.py --tb=short --junitxml=.local/cost-db-0030.xml` | 1 | FAIL：1 项 / 1 PASS；`.local/cost-db-0030.log`。在旧 head 复现成本表接受 `NaN`；项目/类型/期间、独立核验、全额冲销和部分唯一索引反例通过。前两版夹具缺 `baseline_version`、`revision`，独立保留 `.local/cost-db-first.log`、`.local/cost-db-baseline.log`，均退出 1。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_postgres_invariants.py tests/test_costs.py tests/test_postings.py tests/test_database_roles.py tests/test_recovery.py --tb=short --junitxml=.local/rc0031-cost-recovery.xml` | 1 | FAIL：1 项 / 8 PASS；`.local/rc0031-cost-recovery.log`。原生 SQL 测试参数误为字符串 UUID；修正参数类型，保留失败断言。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_postgres_invariants.py tests/test_costs.py tests/test_postings.py tests/test_cost_reconciliation.py tests/test_database_roles.py tests/test_recovery.py tests/test_runtime.py --tb=short --junitxml=.local/rc0031-cost-verified.xml` | 0 | PASS：24 项 / 61.22 秒；`.local/rc0031-cost-verified.log`。覆盖四表拒绝、历史异常使所有新约束与 `alembic_version` 一起回滚、核验不可改、重复来源/冲销、独立实际值对账、受限角色；`0016→0029→0031` 带数据升级并联合恢复，真实回读金额 `65.25`、人天 `3.75`、基线、预测、来源及三份原件，候选流水不重复入账。 |
| `.venv/Scripts/python.exe -m scripts.schema_erd --check .local/erd-0031.md`；`pwsh -NoProfile -File scripts/dev.ps1 check` | 0 | PASS：隔离 PostgreSQL 新图与 `docs/erd.md` 一致（78 表、202 外键），三套规格检查与 mypy 87 源文件 `.local/rc0031-check.log`。 |
| `docker compose -p pmo104-rc-f98acc11 --env-file .local/pmo104-rc-f98acc11.env -f deploy/compose.yaml up --build -d --wait`；`.venv/Scripts/python.exe scripts/deployment_smoke.py pmo104-rc-f98acc11` | 0 | PASS；`.local/rc0031-compose-build.log`、`.local/rc0031-compose-smoke.log`。新增本机回环候选栈，不动既有 `0030` 或共享 `0016`；四条 `ck_*_finite` 均 `convalidated=true`。受限进程、双模型关闭、15 项目与去重回执；真实停库时 503，复启恢复 200；登出 200 且旧会话不可再用。Edge `158.0.4370.33` 30 次/操作，p95 筛选/详情各 43ms，非目标环境性能结论。 |

## 0031 综合回归与库约束反例

源条款：数据库字段类型与约束矩阵、数据库一致性第 2 节、主方案 §3、§20.4–20.6、FND-002/FND-003。Windows、Edge、本机 HTTP/stdio、PostgreSQL 16.14 隔离 schema 与专用库，合成账号。综合验证源码树 `9044fc9f438e3961285210f74b747e3e7c6a8fbc`（`af00acdaf2cdd7e1d0774c81522d403db005b8ba` 仅多证据文档）；追加数据库反例以该提交为基准，源码树 `c6927f61062dbf5dbe31a0cd47f3ace59982a967`。没有第二次改迁移或附件引用。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0031-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0031-backend.xml` | 0 | PASS：277 项 / 415.10 秒，`.local/rc0031-backend.log`。40 工具实测输出证据与 `mcp-stdio-0029.json` 相同（SHA-256 `584fc7468541b3e6a0c7b552e379a6a4a6951db7486092d472f46a88a162f762`），包括 18 读审计与 22 写重放。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/rc0031-edge.xml` | 0 | PASS：19 项 / 224.57 秒，`.local/rc0031-edge.log`。正常无模型三关口、对账、风险处置、归档；暂停后终止复盘、过期和客户例外等。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_postgres_invariants.py tests/test_runtime.py --tb=short --junitxml=.local/rc0031-catalog.xml` | 0 | PASS：18 项 / 21.83 秒，`.local/rc0031-catalog.log`。实际数据库目录核对全部 421 字段类型及空值申明（唯一差异为 ADR 0011 的非关口 `Decision.review_id`）；同组织项目代码、成员角色、里程碑槽位/CHECK/FK、评分轮次唯一及不可改、决议快照/审计追加写、报告部分唯一及受保护内容反例。目录核对不替代其余领域语义。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；在 `web` 运行 `./node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：冻结规格及类型；`.local/rc0031-final-check.log`、`.local/rc0031-vue.log`。 |

## 0032 候选与综合回归

源条款：§9、§12.1–12.2、§20.3–20.6，阶段授权见 [ADR 0019](../adr/0019-project-transition-authority.md)。基准 `60842998920abf4b16e18a1b9a2ba2e40bb42be1`，被测源码树 `4ad0a46cef829cd13792d3c14501fb3d69369985`（`d0bba6b2d31db7ab97f24ab3c79f8f7b003d2543` 另含证据文本）。Windows / Python 3.12.15 / PostgreSQL 16.14 隔离 schema 与专用 Compose / 回环 HTTP 和真实 SDK stdio / Edge `158.0.4370.33`。均为合成账号，模型关闭；`.local/dev.env` 仅注入测试连接，旧候选 `18760`、`18761` 和共享库均未迁移。共享 `public` 只读回核为 `0016`。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0032-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0032-backend.xml` | 0 | PASS：289 项 / 429.81 秒，无跳过，`.local/rc0032-backend.log`。40 项发现、成功调用及动作拒绝全部回读；18 读审计、22 写重放。导出的 JSON 与 [已有逐工具证据](../acceptance/mcp-stdio-0029.json) 逐字节一致（SHA-256 `584fc7468541b3e6a0c7b552e379a6a4a6951db7486092d472f46a88a162f762`）。包括 `0016→0032` 带数据升级及私有附件联合恢复。FastAPI TestClient 和 Alembic 各发出一条弃用警告。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/rc0032-edge.xml` | 0 | PASS：20 项 / 231.83 秒，无跳过，`.local/rc0032-edge.log`。回读无模型全流程、独立阶段授权及有效申请展示，不等同于真实业务签核。 |
| `docker compose -p pmo104-rc-7c9ce0ea --env-file .local/pmo104-rc-7c9ce0ea.env -f deploy/compose.yaml up --build -d --wait` | 1 / 0 | 首轮 FAIL：Docker Hub 令牌连接超时（`.local/rc0032-compose-build.log`）；复用已验证的 Python 基础镜像摘要 `cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2` 后 PASS，未改锁文件或扩大端口（`.local/rc0032-compose-retried.log`）。 |
| `.venv/Scripts/python.exe scripts/deployment_smoke.py pmo104-rc-7c9ce0ea` | 0 | PASS：`.local/rc0032-compose-smoke.log`。回环 `18762`，受限数据库账号，15 个草稿和唯一消费回执；停库 `live=200 / ready=503`，恢复 `200/200` 且项目和回执未丢失。30 次/操作，p95 筛选 50 ms、详情 62 ms，仅本机合成负载。 |

候选 API 容器内回读 OpenAPI：**95/95 基线 REST + 42 工程操作**，40 MCP 路由无缺项，评分分值/锚点五种条件已发布（`.local/rc0032-compose-contract-internal.log`）。Web 代理不开放内部 OpenAPI。回核 `docker ... exec -T api alembic current` 返回 `0032 (head)`，回环 `/health/ready` 200。逐边权限和其余嵌套 DTO 不由目录覆盖推定，目标 Host、目标部署、真实模型和真实试点也未通过。

## 0033 候选与综合回归

源条款：§5.2、§9.1、§12.1 Q2、§12.2 T05/T10、§20.3、工程 DTO 补充 §3、§7。基准 `d2b12df5acc3b680b3a6e165781fd2385a0380ec`，候选和首轮被测树 `08746312574ff91954f3f9df0c50f83f89d7cdf6`，修复 ERD 后固定导出树 `7d5758898ae50c809c72ea71ec6e57c7a36ba6e0`。环境：Windows / Python 3.12.15 / PostgreSQL 16.14 隔离 schema 与专用 Compose / 回环 HTTP、真实 SDK stdio 和 Edge `158.0.4370.33`，全部合成、关闭模型。实现与定向验证见 [M2](M2-report.md#0033-预算版本在同一时点生效)。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0033-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0033-backend.xml` | 1 | FAIL：296 PASS、1 FAIL，ERD 修订标题仍为 `0032`；对照实际库生成文本后修同步，保留断言。`.local/rc0033-backend.log`。 |
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0033-mcp-verified.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0033-backend-verified.xml`（在 `git archive` 导出的固定树执行，实际日志路径指向原仓库） | 0 | PASS：297 项 / 487.54 秒，无跳过，`.local/rc0033-backend-verified.log`。目录与实际 ERD 一致；40 工具发现、调用与未授权拒绝，18 读审计、22 写重放。见 [逐项证据](../acceptance/mcp-stdio-0033.json)，SHA-256 `5c9ca88af1c54b9911a21d8d1921d0ebf38a1faefe7aeb405c769d7e985bc1d2`，相较 0029 增加预算生效时点的 SDK 证据。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/rc0033-edge.xml` | 0 | PASS：20 项 / 267.82 秒，`.local/rc0033-edge.log`。无模型人工闭环与正式授权路径均回归；后续 ERD 修复不涉及产品代码。 |
| `docker compose -p pmo104-rc-cd11b7c4 --env-file .local/pmo104-rc-cd11b7c4.env -f deploy/compose.yaml up --build -d --wait`；`.venv/Scripts/python.exe scripts/deployment_smoke.py pmo104-rc-cd11b7c4` | 0 | PASS：`.local/rc0033-compose-build.log`、`.local/rc0033-compose-smoke.log`。回环 `18763`，受限运行账号，15 个草稿和唯一消费回执；停库 `live=200 / ready=503`，恢复 `200/200`，项目和回执未丢失。30 次/操作，本机 p95 筛选 107 ms、详情 150 ms。 |

联合恢复覆盖 `0016→0033` 的有数据升级，旧成本历史逐字段不变，有效与撤销的阶段授权、私有原件及应用引用均回读。候选容器内 `alembic current` 为 `0033 (head)`，回读 **95/95 基线 REST + 42 工程操作**、40 MCP 所需路由及成本格式（`.local/rc0033-compose-contract.log`）。共享库未升级。不是目标环境周期恢复、性能、真实 Host 或业务签核结论。

## 0033 结项契约候选复验

源条款：主方案 §4.4、§7、§9.1、LR-07、T07/T10、工程 DTO 补充 §1/3/7。基准 `0ef144874a6a55638be6c8a346e73f75124737dc`，候选和后端全量固定导出树 `af645fc52dfa7e0202587458959f4a8780d877c6`。Windows、Edge `158.0.4370.33`、PostgreSQL 16.14 隔离 schema 和专用 Compose，合成账号、模型关闭。定向反例见 [M2](M2-report.md#结项对账和风险接收的回执契约)。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0033-contract-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0033-contract-backend.xml`（固定导出目录，证据路径指向原仓库） | 0 | PASS：301 项 / 536.92 秒，无跳过，`.local/rc0033-contract-backend.log`。40 工具发现、调用、动作拒绝，18 读审计、22 写重放。JSON SHA-256 `b34bfe29ac319cb289e132169c9e26163e0a2b8093199d4210d763ec88cd0aa4`，新增变更嵌套契约用例。 |
| `.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/rc0033-contract-edge.xml` | 0 | PASS：20 项 / 251.50 秒，`.local/rc0033-contract-edge.log`；含无模型三关口、对账、风险、归档。 |
| `docker compose -p pmo104-rc-bd99e6ac --env-file .local/pmo104-rc-bd99e6ac.env -f deploy/compose.yaml up --build -d --wait`；`npm.cmd --prefix web ci`；`.venv/Scripts/python.exe scripts/deployment_smoke.py pmo104-rc-bd99e6ac`（固定导出目录） | 0 | PASS：回环 `18764`，受限账号、15 草稿和唯一消费回执；停库 `live=200 / ready=503`，恢复 `200/200`。30 次/操作，本机 p95 筛选 64 ms、详情 366 ms。`.local/rc0033-contract-compose.log`、`rc0033-contract-npm.log`、`rc0033-contract-smoke.log`，保留包体积告警。 |
| `docker compose ... exec -T api alembic current`；容器内 `GET /openapi.json` 与冻结目录、嵌套契约对表 | 0 | PASS：`0033 (head)`，95/95 基线 + 42 工程操作、40 MCP 路由、双口径顺序、非成员窄回执及六项 human-only 标记。`.local/rc0033-contract-openapi-final.log`。首轮探针退出 1，误以为兼容的固定 `false` 字段应删除；修正探针以核对常量。 |

无迁移或附件引用改动，沿用上一批 `0016→0033` 联合恢复。旧候选 `18763` 和共享开发库未动。这不是目标 Host、目标环境性能或业务验收结论。

## 0034 归档候选与联合恢复

源条款：主方案 §4.1、§9.1、§20.5、LR-08、T10。基准 `4739d033d9a31ba4239cdfe10ea00a297b96b4a9`，构建和 Edge 被测树 `b219d694a68b054fa09eac86af147ba32eb35915`；仅补测试夹具后的全量后端源树 `8dad07608a1bb3eda42e52f4f30698d41ec1b78c`。两树运行源码相同，从 Git 树导出后执行。Windows、Python 3.12.15、PostgreSQL 16.14 隔离 schema/专库及 Compose、Edge `158.0.4370.33`，模型关闭，全部合成。约束与基准反例见 [M2](M2-report.md#归档只读的数据库约束)。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `python -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0034-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0034-backend.xml` | 1 | FAIL：304 PASS、1 FAIL。任务夹具曾在归档后插入 Review，正确触发新约束；调整为先建记录再归档，保留 API 和 stdio 拒绝断言。`.local/rc0034-backend.log`。 |
| `python -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0034-mcp-final.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0034-backend-final.xml` | 0 | PASS：305 项 / 516.61 秒，无跳过。40 项实际发现、调用、动作拒绝，18 项读审计，22 项写重放。`.local/rc0034-backend-final.log`；SDK 逐项 JSON SHA-256 `b34bfe29ac319cb289e132169c9e26163e0a2b8093199d4210d763ec88cd0aa4`，与上一结项契约候选证据一致。 |
| `npm.cmd --prefix web ci`；`python -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/rc0034-edge.xml` | 0 | PASS：Edge 20 项 / 269.58 秒，含模型关闭的三关口及归档。`.local/rc0034-npm.log`、`.local/rc0034-edge.log`。 |
| `docker compose -p pmo104-rc-0591b604 --env-file .local/pmo104-rc-0591b604.env -f deploy/compose.yaml up --build -d --wait` | 1 / 0 | 默认 Python 镜像仓库连接超时 FAIL，`.local/rc0034-compose.log`。使用上一候选的同一 Python 镜像摘要 `cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2` 重跑 PASS，`.local/rc0034-compose-verified.log`；保留前端包体积告警。 |
| `python scripts/deployment_smoke.py pmo104-rc-0591b604`；`docker compose ... exec -T api alembic current`；容器内回读 OpenAPI 和 `pg_trigger` | 0 | PASS：仅回环 `18765`。`0034 (head)`、137 个操作（95 基线、42 工程）、40 工具映射、16 个归档写保护。受限账号、15 草稿及 15 个唯一消费回执；停库 `live=200 / ready=503`，恢复 `200/200`，原项目和消费回执保留。30 次/操作，本机 p95 筛选 43 ms、详情 52 ms。`.local/rc0034-smoke.log`、`.local/rc0034-openapi.log`。 |
| `.venv/Scripts/python.exe` 运行 `docs/spec/08_*.py`、`15_*.py`、`19_*.py`；`python -m mypy pmo --ignore-missing-imports` | 0 | PASS：冻结规格及 88 源文件类型检查。`.local/rc0034-static.log`；静态证据不替代系统验收。 |

同一全量命令中的 `tests/test_recovery.py` 两项 PASS，见 `.local/rc0034-backend-final.xml`。`0016→0034` 有数据升级，实际 `pg_dump/pg_restore` 和私有原件联合恢复；旧预算、阶段授权、归档里程碑、日报引用和原件完整回读，恢复库再次拒绝修改归档里程碑。共享开发库只读查询仍为 `0016`，旧候选未覆盖。所有日志和 JUnit 路径属于原仓库 `.local`；上述 `python` 为本机 `.venv/Scripts/python.exe`。不代表真实签核、目标 Host 兼容、目标环境运维或生产放行。

## 三线候选复验

依据 §5.2–5.5、LR-04/05、Q2/Q7、工程 DTO 补充 §3–4。基准 `b82d9440800e4a8655e194341d21f3fb62b15262`，最终被测源码树 `64ff7789082e4c4ab6b63f4fdcf478e65098e133`，Git 导出目录 `.local/signals-final-64ff7789-fixed`（不含后补证据）。Windows、Python 3.12、PostgreSQL 16.14、Edge `158.0.4370.33`，回环 HTTP/stdio/Compose，合成数据，模型关闭。实现与中间失败见 [M2](M2-report.md#三线零分母及争议出处)。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/signals-final-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/signals-final-backend.xml` | 0 | PASS：305 项 / 482.12 秒，`.local/signals-final-backend.log`。40 工具发现、成功调用及动作拒绝、18 读审计、22 写重放；JSON SHA-256 `1847bcb4d1bef4b889032d4a3157a53733d81606d8c30838126abd4b7e1fe1fd`。 |
| `npm.cmd --prefix web ci`；`.venv/Scripts/python.exe -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/signals-final-edge.xml` | 0 | PASS：Edge 20 项 / 231.05 秒；`.local/signals-final-npm.log` 与 `.local/signals-final-edge.log`。包含无模型三关口、归档、零预算和客户争议历史。 |
| `docker compose -p pmo104-rc-e92e110d --env-file .local/pmo104-rc-e92e110d.env -f deploy/compose.yaml up --build -d --wait`；`python scripts/deployment_smoke.py pmo104-rc-e92e110d` | 0 | PASS：回环 `18767`，受限角色、15 草稿、15 个唯一消费回执，停库 `200/503`、恢复 `200/200`。30 次/操作，本机 p95 筛选 60 ms、详情 62 ms。`.local/signals-final-compose.log`、`.local/signals-final-smoke.log`。 |
| `docker compose ... exec -T api alembic current`；容器内 `GET /openapi.json` | 0 | PASS：`0034 (head)`，137 操作，健康、双口径成本及需求指标的不可计算字段接受 `UNAVAILABLE`。`.local/signals-final-openapi-readback.log`。早期探针使用错误 DTO 名和字段名，退出 1，保留 `.local/signals-final-openapi*.log`；按 operationId 解析真实 Schema 后通过。 |
| `python` 分别执行 `docs/spec/08_*.py`、`15_*.py`、`19_*.py` 和 `python -m mypy pmo --ignore-missing-imports` | 0 | PASS：静态规格与 88 文件类型检查。`.local/signals-final-static.log`。 |

本批无迁移或附件引用变化，上一候选恢复证据仍适用；本次全量的两条恢复测试也通过。命令均在导出树运行，证据归原仓库 `.local`。未覆盖真实 Host、真实签核及目标部署环境。

## 0035 隔离候选和联合恢复

依据主方案 §6/§7/§9.1/§20.5、LR-05/08、T10，工程 DTO 补充 §1/4/7。基准 `d65afae0c4fb8735cc43977a436d679adaa59288`，最终固定导出树 `ba3eb203ac5f12af9bb196813156f990de943ee0`，目录 `.local/pmo104-0035-ba3eb203-fixed`。Windows、Python 3.12、PostgreSQL 16.14、Edge `158.0.4370.33`，隔离 schema、专库和回环 Compose，全部合成、模型关闭。实现及拒绝条件见 [M2](M2-report.md#人工权限契约复盘回执和旧三线快照)。`python` 使用原仓库 `.venv/Scripts/python.exe`，日志与 JUnit 输出至原仓库 `.local`。

| 实际命令或复验 | 退出码 | 结果与证据 |
|---|---:|---|
| `python -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence ../pmo104-0035-final-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=../pmo104-0035-final-backend.xml` | 0 | PASS：310 项 / 483.65 秒，无跳过。40 项发现、成功调用和动作拒绝；18 读审计、22 写重放。`.local/pmo104-0035-final-backend.log`；逐项 JSON SHA-256 `1847bcb4d1bef4b889032d4a3157a53733d81606d8c30838126abd4b7e1fe1fd`。 |
| `npm.cmd --prefix web ci`；`python -m pytest -q tests/browser_case.py --tb=short --junitxml=../pmo104-0035-final-edge.xml` | 0 | PASS：Edge 20 项 / 235.37 秒，包括无模型人工三关口与归档。`.local/pmo104-0035-final-npm.log`、`.local/pmo104-0035-final-edge.log`。 |
| `docker compose -p pmo104-rc-620e8d38 --env-file .local/pmo104-rc-620e8d38.env -f deploy/compose.yaml up --build -d --wait` | 1 / 0 | Docker 默认地址池已耗尽，首次 FAIL（`.local/pmo104-0035-final-compose.log`）。在核对现有网段后为该候选单独配置 bridge，未删除旧网络或卷；同一项目启动 PASS（`.local/pmo104-0035-final-network.log`）。 |
| `python scripts/deployment_smoke.py pmo104-rc-620e8d38` | 0 | PASS：回环 `18769`，只用受限角色。15 个草稿和 15 个唯一消费回执。停库 `live=200/ready=503`，恢复 `200/200`，项目和回执保留。30 次/操作，本机 p95 筛选 50 ms、详情 60 ms。`.local/pmo104-0035-final-smoke.log`。 |
| `docker compose … exec -T api alembic current`；容器内 `GET /openapi.json`；`psql` 回读 `pg_constraint.convalidated` | 0 | PASS：`0035 (head)`，137 操作：40 双入口、95 人工或登录、2 公开运行探针；两项新约束均已验证。`.local/pmo104-0035-final-readback-final.log`。先前探针误走 Nginx 页面，并漏计公开探针、独立邀请码接收；退出 1 保留在 `.local/pmo104-0035-final-readback*.log`。 |
| `python` 分别执行 `docs/spec/08_*.py`、`15_*.py`、`19_*.py`；`python -m mypy pmo --ignore-missing-imports` | 0 | PASS：冻结规格及 88 个源码文件类型检查。`.local/pmo104-0035-final-static.log`。 |

全量命令中 `tests/test_recovery.py` 三项 PASS。由 `0016` 带数据升级至 `0035`，通过实际 `pg_dump/pg_restore` 和私有原件联合恢复，复盘答复和 Lesson 确认原值、原件字节、旧账及归档里程碑保留。恢复库再次校验 Lesson 约束与归档只读。非法存量升级回滚用例早期夹具缺少必填人员启用状态与项目 revision；中间树 `dcff7472cb7ea5f702f78b23a0bed0257b16ec7b` 的 308 PASS / 1 FAIL 记录在 `.local/governance-0035-backend.log`，夹具修正后保留原回滚断言。共享开发库使用只读事务查询仍为 `0016`，退出 0（`.local/pmo104-0035-shared-head.log`）。不是对真实数据、业务签核、目标 Host 或目标部署环境的放行。

## 复盘、三线与草稿重放候选复验

依据 §8.3–8.4、§9.1、§20.3–20.6、T05/T10 与工程 DTO 规范。固定候选基准 `a3cac31e844c56e979b1edc4ef4cce33d201c231`、导出树 `c79ca8059040d0cd125c76551c5cfe5f1f8f10b4`，目录 `.local/pmo104-contracts-c79ca805-fixed`。应用、迁移和构建源码与 `9f27abdb683e4b8de064eadc61d086752455f36a` 一致，后者仅另有重放测试移位及文档。Windows、Python 3.12、PostgreSQL 16.14 隔离 schema/专用 Compose 以及 Edge `158.0.4370.33`，合成环境。`python` 使用原仓库 `.venv/Scripts/python.exe`。

| 实际命令或复验 | 退出码 | 结果与证据 |
|---|---:|---|
| `docker compose -p pmo104-rc-de0dfb26 --env-file .local/pmo104-rc-de0dfb26.env -f deploy/compose.yaml up --build -d --wait` | 0 | PASS：候选回环 `18770`，专用 bridge `10.253.70.0/27`、独立数据和附件卷，无生产发布。`.local/pmo104-contracts-compose.log`。 |
| `python scripts/deployment_smoke.py pmo104-rc-de0dfb26` | 0 | PASS：迁移 `0035`，受限角色，模型关闭，15 草稿、15 唯一消费回执；停库 `live=200/ready=503`，恢复 `200/200`，项目与回执保留。各 30 次的本机 p95：筛选 205 ms、详情 385 ms。`.local/pmo104-contracts-smoke.log`。 |
| `docker exec pmo104-rc-de0dfb26-api-1 alembic current` 和容器内 `GET /openapi.json`；`docker exec pmo104-rc-de0dfb26-db-1 psql ...` 回读两条 `0035` 约束 | 0 / 1 / 0 | `0035 (head)`、137 操作及主体说明回读 PASS（95 人工或登录、40 双入口、2 探针）。约束探针首轮使用错误关系名 FAIL（`.local/pmo104-contracts-readback.log`），改为按约束名查询两项 `convalidated=true`，PASS（`.local/pmo104-contracts-constraints.log`）。 |
| `python -m pytest -q tests/browser_case.py --tb=short --junitxml=../pmo104-contracts-edge.xml` | 0 | PASS：20 项 / 806.91 秒。`.local/pmo104-contracts-edge.log`，含无模型全人工归档、授权阶段流转和书面例外。 |
| `python -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence ../pmo104-contracts-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=../pmo104-contracts-backend.xml` | 1 | FAIL：308 项通过、2 项本地 PostgreSQL 连接超时、1 项回环 HTTP 响应超时；40 工具没有发现/调用/动作拒绝/读审计/写重放缺项，但批次 JSON 状态仍为 FAIL。`.local/pmo104-contracts-backend.log`、`.local/pmo104-contracts-mcp.json`。同期 Compose 构建和多个测试进程运行；不当作业务断言失败，也不标外部阻断。 |
| `python -m pytest -q @vectorFiles tests/test_project_draft_replay.py --tb=short --maxfail=2 --junitxml=.local/pmo104-contracts-vectors-retry.xml`；`python scripts/reference_vector_evidence.py .local/pmo104-contracts-vectors-retry.xml .local/pmo104-contracts-vectors-retry.csv --require-complete` | 1 / 0 | 首次向量与草稿重放 23 项通过、80 项连接超时（`.local/pmo104-contracts-vectors.log`）。连接恢复后在 `9f27abdb683e4b8de064eadc61d086752455f36a`，`$vectorFiles` 为 `tests/test_reference_vectors*.py`，重跑 PASS：103 项 / 194.15 秒，向量逐案 102 PASS、0 FAIL、0 NOT_RUN。`.local/pmo104-contracts-vectors-retry.log`，保留原始失败 JUnit。 |
| `python` 分别执行 `docs/spec/08_*.py`、`15_*.py`、`19_*.py`；`python -m mypy pmo --ignore-missing-imports` | 0 | PASS：规格及 89 个源码文件。`.local/pmo104-contracts-static.log`。 |

该批无新迁移或附件引用变化；沿用 `0016→0035` 联合恢复基线，不改写历史。共享 `public.alembic_version` 查询仍为 `0016`。`18770` 是该固定树，不覆盖后续本地源码变更。

## 裁决出处回归

依据 §6、§8.3–8.4、§20.3–20.6、T07、工程 DTO 规范 §1–2/5。基准 `9f27abdb683e4b8de064eadc61d086752455f36a`，被测源码树 `3cb5f0c2f3a0a8c0bbad539a07d9e38580b3dfe3`（不含补录文档和证据）。实现 `pmo/review_outcomes.py`、`pmo/project_lifecycle.py`、`web/src/ReviewWorkbench.vue`、`web/src/ProjectLifecycle.vue`，参见 [ADR 0019](../adr/0019-project-transition-authority.md#决议回读)。Windows、Python 3.12、PostgreSQL 16.14 隔离 schema/专库、回环 HTTP、实际 SDK stdio 与 Edge，合成账号、模型关闭。无规则、迁移、附件引用或审批权限变更。

| 实际命令或复验 | 退出码 | 结果与证据 |
|---|---:|---|
| `python -m pytest -q tests/test_reviews.py tests/test_lifecycle.py tests/test_transition_authority.py tests/test_stage_edges.py tests/test_mcp_transition.py tests/test_admin_audit.py tests/test_acceptance_baseline.py tests/test_mcp_action_matrix.py tests/test_database_roles.py --tb=short --junitxml=.local/pmo104-decision-provenance-backend.xml` | 1 | FAIL：47 项通过；运行账号 fixture 拒绝测试 URL 附加的 `connect_timeout` 参数。保留原断言和恢复 URL 校验，下一行采用标准 URL（超时用 libpq 环境参数）。`.local/pmo104-decision-provenance-backend.log`。 |
| `python -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/pmo104-decision-provenance-mcp.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/pmo104-decision-provenance-full.xml` | 0 | PASS：313 项 / 469.31 秒；受限运行角色和三项带数据升级/恢复测试通过。40 项工具发现、实际调用及动作拒绝，18 读审计与 22 写重放。`.local/pmo104-decision-provenance-full.log`；[逐工具证据](../acceptance/mcp-stdio-0035.json) SHA-256 `0b3ac888e96b7a1c8d2209303d883d4deb0aa68463c42c68175213ce5b592f1f`。 |
| `python scripts/reference_vector_evidence.py .local/pmo104-decision-provenance-full.xml .local/pmo104-decision-provenance-vectors.csv --require-complete` | 0 | PASS：本源码再次实测 102 PASS、0 FAIL、0 NOT_RUN。 |
| `python -m pytest -q tests/browser_case.py -k 'drafting_through_archive or formal_transition_authority or independent_termination_confirmation or customer_exception_and_conditional_decision' --tb=short --junitxml=.local/pmo104-decision-provenance-edge.xml` | 0 | PASS：4 项 / 102.26 秒，16 项未选。独立评审与归档、阶段授权与终止，Web 展开查看已提交的来源和历史版本。`.local/pmo104-decision-provenance-edge.log`。 |
| `python -m pytest -q tests/browser_case.py --tb=short --junitxml=.local/pmo104-decision-provenance-edge-full.xml`；`npm.cmd --prefix web run build` | 0 | PASS：相同产品与测试源码的 Edge 全量 20 项 / 225.91 秒、生产构建均通过。`.local/pmo104-decision-provenance-edge-full.log`、`.local/pmo104-decision-provenance-build.log`。构建仍有大 chunk 提示，非目标环境性能证据。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；`web` 内 `node_modules/.bin/vue-tsc.cmd --noEmit` | 0 | PASS：三套静态规格、Python 89 文件和 Vue 类型检查。`.local/pmo104-decision-provenance-static.log`、`.local/pmo104-decision-provenance-vue.log`。 |
| 合成 schema `python -m alembic upgrade head`；`python -m uvicorn pmo.api:app --host 127.0.0.1 --port 18772 --reload --reload-dir pmo --no-access-log`；`npm.cmd --prefix web run dev -- --port 18771 --strictPort`；通过 Vite 代理实测登录、创建草稿和登出 | 0 | PASS：本机热重载预览 `18771`，迁移 `0035`，独立 schema `pmo_preview_a833a8fbee1240cab31a5e85252121a0` 与附件目录，模型关闭且 `POLICY_PENDING`。`.local/pmo104-decision-provenance-preview.log`。合成预览只验证准备态，不据此替代真实业务或部署验收。 |

## 阶段流转历史分页

依据工程 DTO §6、主方案 §6、§20.3–20.5；基准 `f71ec96d8d174fd762c69d731518384aba643680`、被测源码树 `1d105074b418a4e43dd01033a818cbff1ade37eb`（不含补录文档）。`pmo/project_lifecycle.py::inbox` 先在 SQL 中按授权项目及审批类型筛选、再分页，返回 `items/page/pageSize/total`；同一页只返回该页出处。`web/src/ProjectLifecycle.vue` 有历史翻页，刷新保留页，新申请回首屏。Windows、PostgreSQL 16.14 隔离 schema、回环 REST/stdio 与 Edge `158.0.4370.33`，合成、模型关闭。没有增加操作、迁移或附件引用，真实审批冻结不变。

| 实际命令或复验 | 退出码 | 结果与证据 |
|---|---:|---|
| `python -m pytest -q tests/test_transition_authority.py tests/test_lifecycle.py tests/test_stage_edges.py tests/test_mcp_transition.py tests/test_runtime.py --tb=short --junitxml=.local/pmo104-stage-history-backend.xml` | 0 | PASS：35 项 / 81.41 秒，包括原默认页、边界分页、同组织另一项目无数据、直接 Agent 禁权、各场景版本/决议及幂等回归。`.local/pmo104-stage-history-backend.log`。 |
| `python -m pytest -q tests/browser_case.py -k 'paginates_stage or drafting_through_archive or formal_transition_authority or independent_termination_confirmation' --tb=short --junitxml=.local/pmo104-stage-history-edge.xml` | 1 | FAIL：3 项通过；1 项新夹具用 `propose` 传了未定义参数。产品差异与终测树相同，夹具按原 API 字段直接发送真实请求。`.local/pmo104-stage-history-edge.log`。 |
| `python -m pytest -q tests/browser_case.py -k 'paginates_stage' --tb=short --junitxml=.local/pmo104-stage-history-edge-retest.xml`；`npm.cmd --prefix web run build` | 0 | PASS：1 项 / 18.01 秒，实测由两个独立账号创建并拒绝 21 个合成申请；可读第 2 页、按页刷新、新申请回首页，项目 stage/revision 不变。Vue 类型和 Vite 生产构建通过，保留 chunk 提示。`.local/pmo104-stage-history-edge-retest.log`、`.local/pmo104-stage-history-build.log`。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；热重载 API `GET /openapi.json` 对冻结方法/路径及主体；只读 SQL 查询两 schema 的 `alembic_version` | 0 | PASS：规格及 89 文件、95 基线和 42 工程接口无差集变更，40 双入口，阶段历史限人工且页长有上限。源码/预览 `0035`、共享 `public` 为 `0016`。`.local/pmo104-stage-history-check.log`、`.local/pmo104-stage-history-readback.log`、`.local/pmo104-stage-history-db-heads.log`。 |

## 补强任务与报告分页

依据 §7.1、§8.4、§20.6、工程 DTO §6–7；基准 `1bc73862c1a19b50459f48055ab18c2728158773`，被测源码树 `4c97d98cba4a8c30ca8d5dcee883d6b84960fc48`（不含证据文档）。实现 `pmo/tasks.py`、`pmo/reports.py`、`pmo/mcp_server.py`，见 [ADR 0020](../adr/0020-task-and-report-reads.md)。Windows、Python 3.12、PostgreSQL 16.14 隔离 schema，真实回环 REST 和 SDK stdio、合成、模型关闭；本批无页面、迁移、附件或权限变更。

| 实际命令或复验 | 退出码 | 结果与证据 |
|---|---:|---|
| `python -m pytest -q tests/test_tasks.py::test_task_list_paginates_the_authorized_rows_in_rest_and_stdio --tb=short --junitxml=.local/pmo104-task-pages-red.xml` | 1 | FAIL：基准加回归用例后，真实 stdio 拒绝合法 `page/pageSize`。`.local/pmo104-task-pages-red.log`。 |
| `python -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/pmo104-paged-reads-mcp.json --pmo-mcp-check-replays -q tests/test_tasks.py tests/test_reports.py tests/test_runtime.py tests/test_mcp_action_matrix.py --tb=short --junitxml=.local/pmo104-paged-reads-backend.xml` | 1 | FAIL：26 项用例通过，但证据插件要求所有工具实调和审计/重放，该定向集只覆盖 29 项实际成功调用。保留 `.local/pmo104-paged-reads-backend.log`，未更新全量 MCP 证据。 |
| `python -m pytest -q tests/test_tasks.py tests/test_reports.py tests/test_runtime.py tests/test_mcp_action_matrix.py --tb=short --junitxml=.local/pmo104-paged-reads-targeted.xml` | 0 | PASS：26 项 / 30.48 秒。任务仅在受委派人可见任务内分页；报告筛选、后续页、空页和超大页正常，REST/stdio 数据与来源一致。任务/报告写入、并发、回滚及作用域回归。`.local/pmo104-paged-reads-targeted.log`。 |
| `pwsh -NoProfile -File scripts/dev.ps1 check`；回环 `18772` 的 `GET /openapi.json` | 0 | PASS：三套冻结规格与 89 个 Python 文件类型检查；实时目录仍 137 项，任务和报告均标记 `human,agent`，页参数和报告类型与运行工具一致。`.local/pmo104-paged-reads-check.log`、`.local/pmo104-paged-reads-openapi.log`。 |
