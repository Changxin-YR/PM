# M3 工具与目标 Host：部分实测，未整体验收

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
