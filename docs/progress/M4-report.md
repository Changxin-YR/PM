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
