# PMO V1.0.4 有限差额台账

## 冻结依据（2026-11-23，北京时间）

本机起点 `67afde30b57db30f8d9138480968c3785cb19fe3` 无跟踪文件改动；四份未跟踪方案材料排除。`e28da52cf977f89343b274252dffb2b69d9a1345` 至 `67afde3` 中，`faef7db` 改动六个 `scripts/pilot_*`，末提交只改文档与脱敏证据，`pmo/`、`web/`、迁移、锁文件均未变。起始远端 `f4ae206014af01988ae56416526014ce4a3d8e8e` 已经 SSH 和 GitHub Ref 只读回读。

持久试点 API `18779` 实际运行 OpenAPI 为 95 个冻结操作（55 人工、40 双入口）及 46 个工程操作；40 项 SDK stdio 映射不是附加业务操作。数据库只读核实 `55443/pmo_pilot`、`pmo_api`、独占 schema `0036`；A `executing`、B `drafting`、两 `-DRILL` 均 `archived`。附件沿用私有目录及 ACL，异常回归只用 `55444` 上的临时 schema、临时原件和独立合成身份。共享 `55439` 未启动、未操作。

工程初始 **18**：A **7**、B **7**、C **4**。结果以本文件的批次回执为准；整行矩阵不因测试总数或本台账关闭而自动改为 PASS。模型及 Host 接入前置工程与真实验收另计。

| 场景与来源 | 操作、角色与状态/时序 | 现有证据；缺口类型 | 预期和关闭条件 |
|---|---|---|---|
| A01 FND-006；工程契约 §1–3 | 所有运行 method/path；匿名、人工、双入口；公开探针与开户登录例外 | `e28da52` 的运行 OpenAPI、`test_runtime`、`test_mcp_action_matrix`；缺逐路由证据归属 | 对齐 141 个操作、40 项映射、实际鉴权入口、有效请求主要拒绝及成功证据；无证据行单列，不能用 DTO 壳或动作拒绝替代资源拒绝。 |
| A02 FND-006 / SEC-01、03 | `POST /projects/{id}/evidence`、`GET /reports/{id}/facts`；同人可访问两个项目，项目父路径与目标 ID 错配；有限 Agent 访问另一资源 | `test_change_contracts`、`test_evidence`、`test_report_analysis` 有单项；补同组织独立变体 | 错配与不可见拒绝或空筛选，不能访问另一项目/报告内容或写入附件元数据。 |
| A03 FND-006 / T02、T09 | `GET/PATCH` 授权资源；撤销、过期、委托资格及项目角色撤销 | `test_agent_tokens` 有实际撤销、部分失权；补有效收据后的读、写、重放 | 旧凭据不能继续读写，范围失效不泄露收据；独立凭据控制组有效、无额外成功审计或 Outbox。 |
| A04 FND-006 / DTO 补充 §21 | 报告/Fact/附件、风险接收、健康、评审 DTO；普通成员、外部接收人及特权读者 | `e28da52` 40 工具闭合 Schema；`test_risk_resolution`、`test_health`、`test_facts` 有嵌套证据；归并边界 | 按角色只投影允许的嵌套字段、证据来源及计数，不公开路径、正文或其它项目标识。 |
| A05 FND-006 | REST 列表及适用 MCP；同筛选总数、越界页和授权变化 | `test_alert_reads`、`test_change_contracts` 已修三类页；其它页直接传 PostgreSQL OFFSET，有溢出待复现 | 保持授权后总数、小页稳定排序和空越界页，不返回 500；覆盖仍直接计算 offset 的分页族。 |
| A06 FND-006 / GOV-008 | 归档 `GET`、核心台账/报告新写；当前授权与历史请求 | `test_postgres_invariants`、Edge 归档报告；补服务与无副作用对应 | 只读回历史；保持合法历史重放，不把新写伪装成重放，成功审计/Outbox 增量为零。 |
| A07 FND-006 / SEC-02 | 同键同参、异参、旧版本；换权限后同键请求 | `e28da52` 22 项工具重放，`test_project_draft_replay`；补动态权限组合 | 同参稳定、异参冲突、旧版本无新结果；先重查权限。 |
| B01 FND-002/003 | 凭据、核验、风险接受/申请的项目/版本；跨实体错配 | `test_postgres_invariants` 的约束；`test_risk_resolution` 的证据变体；补组合 | 无错绑行；数据库和服务拒绝分别留证，不新增全域 CHECK。 |
| B02 GOV-004 | 核验流水和结项对账；独立财务/对账及来源变化 | `test_cost_reconciliation` 已验双口径/基线变化、并发；补真实新核验 | 两口径不混用；旧对账可回执但不再有效；旧材料不重复成功。 |
| B03 GOV-008 | 风险移交申请、确认、正式移交；评审与接收人独立 | `test_risk_resolution` 已验申请、确认、收据和证据变化；与结项组合分开 | 分别持久化；未接收时不清除阻塞，确认且移交后才重算；无重复收据。 |
| B04 GOV-007 | 人工裁决；材料过期或现任评审失权、双裁决并发 | `test_reviews` 已验材料变化、发起人失权及并发；补旧页面/授权时序 | 双并发只有一个合法结果；失权后旧审批、旧键拒绝。 |
| B05 FND-002/003、GOV-006/007 | 决议、状态、回执、成功审计、Outbox；制造事务故障 | `test_a14_audit_failure_rolls_back_decision_and_state`；补 Outbox 故障 | 同提交或无残留；受控拒绝审计独立保留。 |
| B06 GOV-005/006 | `/health` 与 `/health/history`；更换来源再回读旧快照 | `test_health`、早期恢复；历史 `projectRevision` 取实时项目 | 不用当前版本装饰历史；保留旧输入、来源及规则；当前命令继续正确验版本。 |
| B07 GOV-008 / DEC-09、16、26 | 有效需求例外 + 缺对账、金额/人天或风险未满足；人类终裁 | `test_valid_exception_does_not_waive_overruns_commitments_or_risk`、102 向量；串联见 C04 | 需求豁免只作用于列出需求，不跨门槛；裁决重查当前结果。 |
| C01 WEB-002 / UI-01、03、05、07 | 总览、我的审批跳转；现任与摘要读者 | 关口、阶段、变更定位已有 Edge；核实必需入口 | 关口条件裁决仍归评审中心；风险接收、对账、客户签收回原台账，不新增无原条款依据的收件箱。 |
| C02 WEB-005 / UI-07、08、10、11 | 对账、风险接收、需求例外；独立财务、接收人、客户与范围外人员 | `lifecycle.mjs`、`browser_case.py`；缺可见性/直接请求对应 | 合法角色从真台账成功；无权直接请求拒绝；接收人只看最小 DTO。 |
| C03 WEB-002/005 / T04、T07 | 对账、风险、例外、评审打开后材料失效、失权或另一人先处理 | 某些组件确定拒绝后仍保留过期按钮或数据 | 保留原错误并重读；网络结果未知时仍按原键重试。 |
| C04 WEB-005 / Q5、T04 | 对账→风险申请/接受/移交→需求例外→条件裁决；独立变体 | 正常生命周期和需求例外已分别在浏览器验，待串联 | 正反时序；旧回执不得用于结项，最终决议对应当前来源及必要副作用。 |

## 固定候选验收（2026-11-23，北京时间）

固定产品提交 `15fde057e64d1884e009d9ae2cdbc7bf222d14ba`，源码树 `26106b142e75a91110bcd1096a83c2ce5f3ca46d`。源码 ZIP SHA-256 `6b9cb61cfcea88110dd46764714948cfb757060ebc8871fb1469b22457c2bd1d`，wheel SHA-256 `cf782b8a536a33f4b1d0100a8d5a9a0e100a13d9a6a99b0f3738eb69300a1b40`。固定 Compose `pmo104-rc-15fde057` 仅回环、模型关闭，运行迁移 `0036` 与受限账号。下表的固定候选证据来自单独的源码导出与隔离运行。原生持久试点 API 的记录启动版本为 `e28da52`，其浏览器和业务演练另计。

| 检查 | 固定候选结果 |
|---|---|
| 静态契约、类型、前端构建 | 三份规格脚本通过，mypy 检查 93 个文件成功，Vue/Vite 构建通过。保留前端包体告警。 |
| 后端、Edge、发行入口 | 后端 **354/354**，含带数据迁移及数据库与私有附件联合恢复，Edge **28/28**，仓库外安装 wheel 后入口 **11/11**。无测试跳过。冻结规则向量 **102/102**，每条反查到本次后端 JUnit。[向量映射](reference-vectors-0036-15fde05.csv) |
| REST 与 SDK stdio | 实测 OpenAPI 冻结基线 95 项、工程操作 46 项。141 项各有有效成功请求，137 项受保护操作各有同请求去掉凭据后的 401，另有两个公开探针和两个开户/登录入口。40 项 SDK 工具均发现、成功实调并有未授权动作拒绝，18 个读审计和 22 个同键写重放齐全。[SDK 逐工具证据](mcp-stdio-0036-15fde05.json) |
| 本机固定 Compose | 15 项目、15 条唯一 Outbox 消费回执。停此栈 DB 时 `live=200/ready=503`，恢复为 `200/200`，项目和去重回执保留。Edge 每操作 30 次，本机 p95 为筛选 167 ms、详情 235 ms、里程碑 454 ms。 |
| 远端 CI | `15fde05` 首次远端回归 353/354，恢复失败是高版本客户端向 PostgreSQL 16 写入 `transaction_timeout`。仅修工作流的 `c1fcf96` 固定 16 主版本客户端，应用源码相同，复跑 **354/354 通过**。[首轮](https://github.com/Changxin-YR/PM/actions/runs/45116017113) · [复跑](https://github.com/Changxin-YR/PM/actions/runs/45117161430) |

可复查本机汇总在 `.local/pmo-closeout-release/`，包括 `candidate.json`、`backend.xml`、`edge.xml`、`wheel.xml`、`smoke.log`、`mcp.json`、`reference-vectors.csv` 和 `rest-route-evidence.csv`。路由表由 [REST 证据脚本](../../scripts/rest_closeout_evidence.py)从固定候选的运行 OpenAPI、真实端点依赖和 HTTP 日志生成，`--require-complete` 退出 0。匿名拒绝、动作拒绝和资源拒绝分别取证。原始日志、会话、原件与配置均留在本地。

## 最终修正版

最终应用提交 `e736b7d18089e835bf0bb93a330a95354133b87f`，源码树 `727e4ff180f63b3f5ac7296c79f6062655e9a211`，迁移 `0037`。远端开发分支以该 SHA 快进，`main` 保持原发布基线。临时预检 `52ef16b` 在非所有者 API 例行审查中发现函数执行权限问题。最终修正版内联数据库绑定校验，保留受限角色，不使用之前执行过旧版 0037 的验收数据库。

[应用候选远端 CI](https://github.com/Changxin-YR/PM/actions/runs/45119654509) 的 356 项应用测试通过，生成 ERD 与 0037 不一致的校验失败。重新生成 ERD 后的远端分支提交为 `0613711add039b8cb9a0a54fa73014e8a225ec22`，与 `e736b7d` 的差异仅为 `docs/erd.md`，ERD 定向复验通过。[最终远端 CI](https://github.com/Changxin-YR/PM/actions/runs/45120623400) **357/357 通过**。最终源码独立 stdio 验证 40/40 成功及动作拒绝，18 项读审计和 22 项写重放齐全，102 条规则向量全通过。[最终工具证据](mcp-stdio-0037-0613711.json) · [最终向量证据](reference-vectors-0037-0613711.csv)。含更新 ERD 的源码包 SHA-256 为 `a7009f6dfb374476afdfac99f60674e4b903373b3bcf6294e393c811e434ddeb`。最终固定源码 ZIP 的 SHA-256 为 `25efb7333d541c3bca9c93a0b1bdd7445a4bb8bbadc3bfdc1be3955f4d714cec`。最终隔离栈 `127.0.0.1:18782` 使用新数据卷，迁移 `0037`，API、调度器和 Outbox 保持受限角色。真实 Edge 和 API 验证通过。停库 `200/503`，恢复 `200/200`，15 个项目和投递回执保留且无重复。本机每项取样 30 次，筛选、详情、里程碑 p95 分别为 74、69、831 ms。最终 SHA 的 Edge 关联回归 4/4、迁移和角色专项 3/3，通过且无跳过。Web 与完整 Edge 用例和 28/28 通过的预检提交逐文件一致。

## 18 项差额回执

**18 项有限场景均已有通过证据。** 行内“通过”对应表中所列场景与角色；[主追踪矩阵](traceability.csv)的整项状态另按其验收范围判定。

| 编号 | 本批结果与可复查依据 |
|---|---|
| A01 | **通过。** 固定候选逐项绑定了 141 个 method/path、实际鉴权入口和 40 项 stdio 映射，141 项成功与 137 项同请求匿名拒绝齐全。两个公开探针及两个开户/登录入口另列。[取证脚本](../../scripts/rest_closeout_evidence.py) · [匿名探针测试入口](../../tests/rest_probe_api.py) |
| A02 | **通过。** 同组织夹具覆盖同一人可见两项目且父路径与报告不符、普通成员读他人私有 Fact 或附件、仅能见另一项目的成员和受限 Agent。错绑写拒绝并核对无多余数据库附件行，错误筛选为空。[附件与父路径](../../tests/test_closeout_scope.py) · [报告资源](../../tests/test_reports.py) · [分析权限](../../tests/test_report_analysis.py) |
| A03 | **通过。** 真实 HTTP 分别撤销、过期、移除委托资格、撤销成员及取消显式项目授权。原凭据的读、新写、旧键重放均按权限拒绝，核对独立凭据控制组和成功审计、Outbox、幂等条数。委托资格是账户级失权，两凭据共享该资格时均失效。[委托差额回归](../../tests/test_closeout_scope.py) |
| A04 | **通过有限角色场景。** 报告作者、普通成员、评审及 PM 对照私稿、已发布报告、Fact、附件计数和出处。外部风险接收人只见受控字段，拿不到原件。健康核对人工完整视图及 Agent 精简视图，隐藏受限输入。评审核对冻结出处、嵌套 DTO 和未评分时的他人评分边界。[报告对照](../../tests/test_reports.py#L313) · [附件](../../tests/test_closeout_scope.py#L23) · [风险](../../tests/test_risk_resolution.py#L331) · [健康](../../tests/test_health.py#L65) · [评审](../../tests/test_reviews.py#L290) |
| A05 | **通过有限分页场景。** 逐个比对运行 OpenAPI 中全部 31 个带 page 参数的读取操作。授权筛选下比较小页总数和远页空结果，合同上限以外预期收到 422。任务列表在 REST 和真实 stdio 校验范围、顺序与总数，另验委托撤销与资源拒绝。[完整分页集合](../../tests/test_closeout_scope.py) · [任务 REST/stdio](../../tests/test_tasks.py) |
| A06 | **通过。** 实际归档后历史可读，合法归档回执按原键复用，新写被拒。归档前后核对核心台账、成功审计与 Outbox 无增量，另验证报告历史与数据库级写保护。[归档生命周期](../../tests/test_lifecycle.py) · [数据库写保护](../../tests/test_postgres_invariants.py) · [Edge 报告归档](../../tests/browser_case.py) |
| A07 | **通过。** 22 项写工具有 SDK 同键重放证据；草稿跨阶段重放检查委托仍有效。真实 HTTP 校验同键异参冲突、旧版本及失权后同键拒绝，无二次成功。[草稿重放](../../tests/test_project_draft_replay.py) · [动态授权](../../tests/test_closeout_scope.py) · [审批单次提交](../../tests/test_reviews.py) |
| B01 | **通过有限场景。** 迁移 0037 约束显式委托与关联对象的组织、项目和版本，验证无错绑写入。历史回执按其原始版本匹配，新写按当前风险版本校验。直接数据库反例、0036 带数据升级、真实受限 API 下的风险申请、接收与移交通过。运行账号仍无触发器函数直接执行权。[迁移](../../migrations/versions/0037_closing_receipt_bindings.py) · [专项](../../tests/test_pmo104_closeout_bindings.py) |
| B02 | **通过。** 双口径分别注入新核验实际，旧对账立即失效，仅相应口径净额变化。并发只接受一个当前版本。两组合成回执另验证核验人签对账被拒、独立对账人成功、旧原件覆盖不足和旧对账失效。[双口径对账](../../tests/test_cost_reconciliation.py) · [合成演练](../../scripts/pilot_rehearsal.py) |
| B03 | **通过。** Edge 串联发起、指定接收人确认和评审复核移交；确认之后、正式移交之前仍阻塞结项。后端核对独立记录及其父回执、正式处置引用和并发约束。合成演练重现未接收、错误接收人拒绝及正式移交成功。[风险服务](../../tests/test_risk_resolution.py) · [组合 Edge](../../tests/browser_case.py) |
| B04 | **通过。** 变更后的旧送审材料被拒；同时两个裁决只成功一个。评审失权后仍可按成员权限看历史，按原键再次执行裁决收到 403，并留拒绝审计且无新成功副作用。[材料与并发](../../tests/test_reviews.py) · [失权旧键](../../tests/test_closeout_governance.py) |
| B05 | **通过。** 分别注入成功审计和 Outbox 写入故障，验证决议、状态、业务回执、审计和 Outbox 一并回滚；恢复后成功并只提交一次，业务拒绝独立审计。[审计故障](../../tests/test_acceptance_baseline.py) · [Outbox 故障](../../tests/test_closeout_governance.py) |
| B06 | **通过。** 改里程碑来源及项目版本后，当前读取变化，时点读取与历史保留原项目版本、快照、规则、输入散列和出处，不重写旧快照；当前计算入口仍验版本与权限。[快照回归](../../tests/test_closeout_governance.py) · [当前健康入口](../../tests/test_health.py) |
| B07 | **通过。** 有效需求例外下，缺对账与阻塞风险仍显示，金额与人天偏差、未履约承诺仍挡住送审。终裁回查真实原件、有效期及撤销状态，决议仅绑定有效回执中列出的需求。[例外与终裁](../../tests/test_requirement_exceptions.py) |
| C01 | **通过。** Edge 和 HTTP 覆盖工作台与概览、关口、阶段及独立变更审批的定位和授权。对账、接收人及客户书面接受从业务面板操作，条件结项由评审工作台裁决。[审批与概览](../../tests/browser_case.py) · [本人审批入口](../../tests/test_approval_queue.py) · [业务面板联查](../../web/e2e/lifecycle.mjs) |
| C02 | **通过。** 独立对账人从成本页成功，PM 直发被拒；风险接收仅指定人可办理，接收人 DTO 不显示风险正文；只有有权客户代表能办理例外，Agent、PM 和范围外人员有拒绝。分别记录 Edge 的真入口和 HTTP 的越权请求。[Edge 工作流](../../web/e2e/lifecycle.mjs) · [书面例外权限](../../tests/test_requirement_exceptions.py) |
| C03 | **通过有限浏览器场景。** 真实 Edge 覆盖两窗口对账、旧材料撤回以及服务端已处理后丢响应。成本 502、风险和评审网络中断、需求例外请求仍在处理中，四路都以原键、版本与请求体重试，回原回执且数据库不产生重复。明确拒绝时保留原错误并重读。未遍历每个面板与每个角色的失权组合。[Edge 执行](../../web/e2e/lifecycle.mjs) · [回执核对](../../tests/browser_case.py) |
| C04 | **通过。** 固定候选 Edge 两组独立变体走完对账、风险申请/确认/移交、需求例外和条件裁决。有效变体结项并回读决议与例外散列，打开后撤销的变体停在评审，数据库对照核验决议数量、接收与移交回执绑定。[组合 Edge 与库核对](../../tests/browser_case.py) · [浏览器步骤](../../web/e2e/lifecycle.mjs) |

## 合成现场与放行边界

- 原生模型关闭 Web 专项通过，覆盖登录、助理四模式、报告分析入口、跨项目报告拒绝及令牌抽屉。另在两个新隔离合成项目实际执行业务链。实施演练 `SIM-EXEC-1123-123021-DRILL` 归档，12 条客户确认、151 条成功和 14 条预期拒绝。新立项演练 `SIM-NEW-1123-123021-DRILL` 从独立任命和评审到归档，10 条客户确认、135 条成功和 11 条预期拒绝。两例都覆盖资金/人天、独立核验与对账、风险接收、复盘和归档只读。主 A 在实施，主 B 保持草稿。本机脱敏汇总为 `sim-replay-1123-123021-summary.json`。[演练入口](../../scripts/pilot_rehearsal.py) · [合成试点手册](../ops/synthetic-pilots.md)
- Codex Desktop 的合成 T09 实调于 13:05 完成。实际连接发现 40 工具，限定项目读取成功，另一个项目和未授权里程碑动作分别拒绝。B 草稿版本从 7 写至 8，同键回放与独立 Web API 回读均为 8，恢复初始标题后为 9，委托撤销成功，旧 Desktop 进程再请求返回 `UNAUTHORIZED`。本机脱敏回执 `pmo-user-desktop-t09-live.json`。另有两个启动器的 SDK 独立联调，其中撤销 DSH 委托后 Codex SDK 仍可读。同日实际安装并验签正式发布的 Harness `0.2.2-rc.5`，本人登录后测试直接 stdio。范围内列表/详情成功、异项目 404、无授权动作 403。写入及重放均为版本 10，独立 Web 回读一致，恢复原标题至 11。撤销后原连接返回 401 / `UNAUTHORIZED`。合成 Host 证据见 [M3](../progress/M3-report.md#2026-11-23-codex-desktop-合成试点实测)。
- 合成专项复验记录有模型回环与人工降级 26 项、八项 DEC 相应路径 20 项、Outbox 和三线预警 6 项，均通过。真实模型网关、实际通知通道、目标环境周期恢复与性能、真实客户与财务身份、两组真实试点及八项业务签核仍需各自证据。生产继续 NO-GO。[真实业务验收](../ops/business-pilot-acceptance.md) · [目标环境验收](../ops/target-acceptance.md)
