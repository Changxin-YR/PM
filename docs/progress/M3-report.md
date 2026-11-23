# M3 工具与目标 Host：部分实测，未整体验收

## 2026-11-23 Codex Desktop 合成试点实测

13:05（北京时间）在实际 Desktop 聊天调用本机 `pmo_synthetic_codex`，发现 40 个工具。受限合成委托只允许项目 `SIM-NEW-001` 的列表、详情及草稿编辑。列表只返回该项目，详情为 `drafting`。读取另一个合成项目返回 `RESOURCE_NOT_FOUND`，调用委托未授权的里程碑读取返回 `FORBIDDEN`。

草稿从版本 7 改名到版本 8；同一幂等键重放返回版本 8，由独立人工会话经 Web API 回读版本 8。随即恢复原标题至版本 9，项目仍是 `drafting`。保留版本与审计记录后撤销 Desktop 临时委托，正在运行的旧连接再调用返回 `UNAUTHORIZED`。脱敏原始回执保存在本机 `.local/pmo-user-desktop-t09-live.json`。

实际 Host 测试使用持久试点 `18778/18779`，其 API 启动于 `e28da52`。固定候选 `15fde05` 的[独立 SDK 证据](../acceptance/mcp-stdio-0036-15fde05.json)另有 40 项成功及越动作拒绝、18 项读审计与 22 项写重放；两个独立令牌之间的撤销隔离已有 SDK 证据。另在经签名验证、本机安装且由本人登录的 DeepSeek Harness Desktop `0.2.2-rc.5` 测试直接 stdio 接入。实际客户端列表仅返回 `SIM-NEW-001 / drafting`，授权详情成功，异项目与未授权动作分别返回 `RESOURCE_NOT_FOUND` 和 `FORBIDDEN`。从版本 9 修改到 10，同键重放及独立 Web API 回读均为 10。客户端恢复标题到 11。撤销 DSH 短期委托后，原连接实际返回 `UNAUTHORIZED`，服务端记录 401。两套实际 Host 使用不同的合成委托，DSH 在 Codex 委托撤销后仍可访问其授权项目。脱敏回执在本机 `.local/pmo-dsh-qualification-20261123/actual-desktop-test.json`。以下为早期批次。

## 2026-11-23 早期候选与 Host 边界

产品候选 `e28da52cf977f89343b274252dffb2b69d9a1345`，持久试点 API `127.0.0.1:18779/api/v1/`。全量后端回归启用 SDK 插件，实际 stdio **40/40 发现、40/40 成功调用及闭合 Schema、40/40 未授权动作拒绝（`FORBIDDEN`）、18/18 读审计、22/22 同键写重放**；无重复成功审计或 Outbox。见[逐工具输出](../acceptance/mcp-stdio-0036-e28da52.json)及 [M4 命令](M4-report.md#2026-11-23-固定候选与两组合成试点)。仓库外固定 wheel 的 11 项也已通过，见历史制品记录；两者都不是实际 Host 验收。

| Host | 现状 | T09 |
|---|---|---|
| Codex Desktop | 今日本机安装 `26.1117.4957.0`；内置 CLI `0.171.0-alpha.13` 回报未登录，这不是 Desktop 会话证据。`codex-mcp.toml` 已按 `native` 生成，未写入 Host 配置；登录及 MCP 批准均留给本人，未观察到 Desktop 实调。 | BLOCKED_EXTERNAL |
| DSH | 今日重查本机登记仍为 `0.2.2-rc.3`，登记的 Harness 程序缺失；此前官方桥报 `auth_required`，无可操作批准窗，实际运行版本未确认。`dsh-mcp.cordis.yml` 已按 `native` 生成但未加载。 | BLOCKED_EXTERNAL |

两份模板均不含密钥。原生试点之前独立签发给 Codex 和 DSH 的 1 小时委托于 2026-11-22 约 20:18（北京时间）到期；接入时分别轮换，继续最小授权且只针对 B 的草稿。本人批准之后，从各自真实 Host 验证发现、列表/详情、同键草稿写入及 Web 回读、重放、越权和单独撤销后另一 Host 仍可读。见[操作手册](../ops/synthetic-pilots.md#打开和批准-host)。以下为历史批次。

## 2026-11-22 合成 Host 交接（旧 Docker 试点）

固定产品代码 `e28da52cf977f89343b274252dffb2b69d9a1345`，隔离 PMO API `127.0.0.1:18776/api/v1/`。`SIM-NEW-001` 保留 `drafting`；通过 `sim-init` 的正式入口签发两个各自 1 小时的委托，动作仅 `pmo_project_list` / `pmo_project_get` / `pmo_project_draft_update`，项目白名单仅 B，不允许创建。口令、令牌与配置保留在 `.local/pmo-synthetic-pilot`；不进本报告。

| 对象 | 实际准备与状态 |
|---|---|
| Codex Desktop | 安装 `26.1117.3838.0`，自带 CLI `0.171.0-alpha.13`。启动真实登录流程；CLI `login status` 仍未登录。生成 `codex-mcp.toml`，未写入全局配置，未观察到 Desktop 连接批准或实际调用。 |
| DSH | 本机登记 `0.2.2-rc.3`；登记路径无可执行文件，未发现运行中的 Harness。官方桥实测 `auth_required`、版本未知；无批准窗口。依据本机 DSH 源码样例准备独立 overlay，不把网页或 SDK 当作 Host。 |
| 仓库外安装 | 当前 `pmo_lite-0.1.0-py3-none-any.whl` SHA-256 `3456a20967bd6c5e247ec9ee43debfc28b884a946c6a0c816d4641f98d518b91`。独立 venv 执行 `tests/test_mcp_configuration.py`：11 PASS，以真实安装的 `pmo-mcp.exe` 从仓库外启动，验证 40 项发现、读和配置/鉴权拒绝。 |

T09 两个 Host 均 **BLOCKED_EXTERNAL**。仍须各自在真实 Host 验收发现、列表/详情、同键草稿写入和 Web 回读、重放、越权拒绝、人工撤销原进程并确认另一 Host 不受影响。操作入口见[手册](../ops/synthetic-pilots.md#打开和批准-host)。以下为历史记录。

2026-11-20：`tests/test_runtime.py::test_mcp_stdio` 使用真实 SDK 子进程，发现、调用并校验 13/40 个工具的结构化数据。项目、里程碑、需求、立项卡、角色提名、联系台账复用 REST 的范围与事务；撤销后拒绝。人工成员列表和审计列表不暴露为工具。

上述 13 项为早期记录。2026-11-20 实测从人工签发到 SDK stdio 读、越权拒绝、撤销后原进程即时拒绝，见 `tests/test_agent_tokens.py`。关口批次后运行时共注册 30/40 项；`tests/test_reviews.py` 用实际签发且含显式项目委托的短时令牌调用新增四项，校验成功/错误的封闭 DTO、额外字段拒绝和送审重放。SDK 不等于目标 Host。T09 未通过，不宣传 Codex/DSH/其他客户端兼容。

2026-11-21（0022）：实际注册 38/40；`tests/test_reports.py` 和 `tests/test_tasks.py` 用显式项目委托的真实 stdio 进程实测报告和任务五项工具及副作用，PM 发布保留人工专用。源码树、命令和退出码见 [M2 实测记录](M2-report.md)。健康、Fact 工具尚未注册；没有任何目标 Host 已通过 T09 的证据。

2026-11-21（0023）：实际注册 39/40；最后一项为 `pmo_fact_propose`。`tests/test_health.py::test_live_snapshot_appeal_and_stdio_scope` 通过真实 stdio 按闭合 Schema 读取健康，核心字段同一时点与 REST 相同；不输出人工复核和输入明细。见 [M2 验证](M2-report.md)。Codex、DSH 等实际目标 Host 版本与互操作证据各自未提供，分别为 BLOCKED_EXTERNAL；SDK 通过不替代目标 Host。

2026-11-21（0024）：`app.openapi()`、`ROUTES` 和 SDK 实际 `list_tools` 对表，40 项注册无多余或漏项。`tests/test_facts.py::test_stdio_candidate_and_human_only_boundaries` 调用最后一项并按封闭 Schema 校验结果，对照 REST 和 Web 同一记录，校验跨项目、非法高信任字段、Agent 直调核验/澄清拒绝及同键重放。见 [M4 测试记录](M4-report.md)。所有既有工具的完整输入、输出、权限、副作用、并发和目标 Host 覆盖仍分别追踪。

## 2026-11-22 SDK 逐工具调用及阶段申请

源条款：§4.5、§8.1–8.4、§20.4–20.6。冻结定义、运行时注册和测试内真实 `list_tools` 均为 40/40，逐项成功执行均通过 Schema 校验，见[逐工具调用证据](../acceptance/mcp-stdio-0028.json)。`successfulCaseIds` 链接实际用例，`observedErrorCodes` 仅列观察到的拒绝，不代表穷尽权限或副作用。

基准 SHA `1c1431819af0b83b55243eb47eeea56ec460843c`，实测源码树 `5a68370e4df7f7d3e01346b8c09c59857572e0b5`（含 LR-08 源码，不含进度文本）。Windows、隔离 PostgreSQL 16 schema、回环 HTTP、实际 Python SDK stdio 进程；合成账号，模型关闭。

| 实际命令 | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/mcp-calls-0028.json -q --tb=short` | 0 | PASS：124 项 / 201.65 秒；`.local/0028-full-with-mcp.log`。源码：`scripts/mcp_test_evidence.py`、`tests/test_mcp_transition.py`、`tests/test_facts.py`。阶段申请同键并发写入一项申请、一条成功审计及一条 Outbox；HTTP/stdio 回执相同，Agent 与其委托人均不可自批，独立 Sponsor 才能推进；撤销令牌即时拒绝重放。 |

首轮增量聚合 FAIL；Fact 测试的审计副作用断言未限定项目，改为只查询被测资源，未删除断言。所有 40 项的成功调用是 PASS；逐调用权限、并发及副作用的穷尽覆盖仍 NOT_RUN。Codex 目标 Host：BLOCKED_EXTERNAL，未取得实际配置、版本及全链测试证据；DSH 目标 Host：BLOCKED_EXTERNAL，未取得独立的实际配置、版本及全链测试证据。不能从本日志推断 T09 通过。`pmo-mcp` 发行入口与 `PMO_API_URL` 契约仍需补测。

## 2026-11-22 发行入口

源条款：§8.1、§8.4、MCP-001。`pyproject.toml` 注册 `pmo-mcp`，从冻结工具 JSON 原样打包；`pmo/mcp_server.py` 读取进程级 `PMO_API_URL`，保留开发用 `PMO_API_PORT` 退路。仅允许明确配置的 HTTPS 或回环 HTTP；拒绝 URL 内嵌凭据、查询、片段或不受控路径，不跟随重定向。鉴权和业务仍由 REST 决定。基准 SHA `54f6d29227e8bef325637abbf57499017c29a026`，源码树 `1c7024b55198b9812cf67dcab295a03180e673f0`（不含此证据文本）。

| 实际命令 | 退出码 | 结果与证据 |
|---|---:|---|
| `uv lock --offline`；`uv build --out-dir .local/mcp-dist` | 0 | PASS：源码分发包及由它构建的 wheel；`.local/mcp-lock.log`、`.local/mcp-build.log`。实测 wheel SHA-256 `78265fdeea86ca28a3fd5d597cc74138ade0011326c70fdd80a35c3a4c6bdeba`。 |
| `uv pip sync --offline --python .local/mcp-installed/Scripts/python.exe .local/mcp-locked-requirements.txt`；`uv pip install --offline --no-deps --python .local/mcp-installed/Scripts/python.exe .local/mcp-dist/pmo_lite-0.1.0-py3-none-any.whl` | 0 | PASS：隔离安装测试，不发布制品，不改现用环境。 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_mcp_configuration.py tests/test_mcp_transition.py tests/test_runtime.py::test_mcp_stdio --tb=short`（`PMO_TEST_MCP_ENTRYPOINT` 指向隔离安装的 `pmo-mcp.exe`） | 0 | PASS：13 项 / 19.78 秒，`.local/mcp-installed-test.log`。Windows、随机隔离 PostgreSQL、合成令牌、真实回环 HTTP 和 SDK；仓库外启动发行入口枚举 40 项，调用项目列表与 REST 一致，错误配置和未交付令牌受控拒绝。 |

这证明本机发行包和 SDK 可工作；Codex/DSH 目标 Host 证据仍分别 BLOCKED_EXTERNAL。没有推送、分发或部署。

## 0029 逐项授权、副作用与查询审计

源条款：§8.1–8.4、§12.2 T01/T02/T04/T05/T07/T09、§20.3。合成、Windows、隔离 PostgreSQL 16、回环 HTTP 和真实 Python SDK stdio。基准 SHA `6c0dfe2a745a6f9cb7bd86925aa2e2f69a3284b9`，固定导出源码树 `b0a49af21ad802215de95f832d791b20fda09953`。实现：`pmo/projects.py`、`pmo/audit_store.py`、`migrations/versions/0029_agent_read_audit.py`、`scripts/mcp_test_evidence.py`。

| 实际命令（仓库的固定源码导出目录，使用仓库 venv） | 退出码 | 结果及证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -p scripts.mcp_test_evidence --pmo-mcp-evidence .local/rc0029-mcp-clean.json --pmo-mcp-check-replays -q --tb=short --junitxml=.local/rc0029-backend-clean.xml`（输出实际写到原仓库 `.local` 的绝对路径） | 0 | PASS：258 项 / 338.78 秒，`.local/rc0029-backend-clean.log`；[40 工具逐项证据](../acceptance/mcp-stdio-0029.json)。40/40 发现、闭合 DTO 成功调用、未授权动作及额外输入拒绝；18/18 读工具各有带来源审计且无 Outbox；22/22 写工具各有相同主体的首次收据及重放，无额外成功审计或事件。 |

组织与项目范围、令牌过期/撤销/委托人失权、关键旧 revision 及并发的实测用例见追踪矩阵。未穷举每个工具在所有角色、阶段和并发组合。成功 Agent 查询用服务端已授权项目、委托账户、稳定引用、规则、响应哈希及内部请求号记录，未记录搜索词、正文或令牌；审计写入失败不返回查询数据。原有审计未伪造来源。基线仍是 95 REST 与 40 MCP；0029 不新增路由。

| 承诺目标 Host | 已观测环境 | T09 |
|---|---|---|
| Codex Desktop | `26.1117.3838.0`；自带 CLI `0.171.0-alpha.13`，CLI 实查未登录。未验证目标 Host 枚举、读/写草稿、拒绝和撤销 | BLOCKED_EXTERNAL |
| DSH | 独立桥接探测 `auth_required`；无足以完成本项目隔离连接的授权，Host 版本未核实 | BLOCKED_EXTERNAL |

## 立项后草稿编辑请求重放

依据主方案 §6.1/§20.3、领域不变量 §8、T05。基准 `a3cac31e844c56e979b1edc4ef4cce33d201c231`；最终源码及已移动的同一测试体 `9fceb0ab9b8943420225aa431fbc481f12517952`。Windows、回环 REST/stdio、隔离 PostgreSQL 16.14、合成账号，模型关闭。

`pmo/projects.py` 在查询真实组织和项目范围、核对仍有效的发起人身份后先查该主体的既有草稿编辑回执，再对首次写入检查 `drafting` 和 revision。审批后仍有权的原人、显式授权 Agent 可重放原状态码和响应，不能更新冻结草稿。同键异参冲突、其他主体越权和已撤销令牌仍拒绝；人和 Agent 的同字面键不混用。不增加角色、跨阶段写权限或迁移。

| 实际命令（本机 `.venv/Scripts/python.exe`） | 退出码 | 结果与证据 |
|---|---:|---|
| `python -m pytest -q tests/test_reference_vectors_permissions.py::test_draft_update_replay_after_submission_preserves_live_authority --tb=short --junitxml=.local/draft-replay-before.xml` | 1 | FAIL：实际独立裁决进入 `designing` 后，原发起人重试得到 404 而非原 200；`.local/draft-replay-before.log`。 |
| `python -m pytest -q tests/test_reference_vectors_permissions.py tests/test_agent_tokens.py tests/test_scope_migration.py tests/test_human.py tests/test_mcp_action_matrix.py tests/test_runtime.py tests/test_reviews.py --tb=short --junitxml=.local/draft-replay-backend.xml` | 0 | PASS：55 项 / 72.61 秒。当前权限、两种主体分别重放、直接 HTTP 与 stdio 一致、撤销拒绝、成功审计和 Outbox 不双写。`.local/draft-replay-backend.log`。新用例随后原样移到 `tests/test_project_draft_replay.py`，避免混入冻结向量索引。 |
