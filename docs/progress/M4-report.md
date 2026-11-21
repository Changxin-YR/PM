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
