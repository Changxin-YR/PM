# M3 工具与目标 Host：部分实测，未整体验收

2026-11-20：`tests/test_runtime.py::test_mcp_stdio` 使用真实 SDK 子进程，发现、调用并校验 13/40 个工具的结构化数据。项目、里程碑、需求、立项卡、角色提名、联系台账复用 REST 的范围与事务；撤销后拒绝。人工成员列表和审计列表不暴露为工具。

上述 13 项为早期记录。2026-11-20 实测从人工签发到 SDK stdio 读、越权拒绝、撤销后原进程即时拒绝，见 `tests/test_agent_tokens.py`。关口批次后运行时共注册 30/40 项；`tests/test_reviews.py` 用实际签发且含显式项目委托的短时令牌调用新增四项，校验成功/错误的封闭 DTO、额外字段拒绝和送审重放。SDK 不等于目标 Host。T09 未通过，不宣传 Codex/DSH/其他客户端兼容。

2026-11-21（0022）：实际注册 38/40；`tests/test_reports.py` 和 `tests/test_tasks.py` 用显式项目委托的真实 stdio 进程实测报告和任务五项工具及副作用，PM 发布保留人工专用。源码树、命令和退出码见 [M2 实测记录](M2-report.md)。健康、Fact 工具尚未注册；没有任何目标 Host 已通过 T09 的证据。

2026-11-21（0023）：实际注册 39/40；最后一项为 `pmo_fact_propose`。`tests/test_health.py::test_live_snapshot_appeal_and_stdio_scope` 通过真实 stdio 按闭合 Schema 读取健康，核心字段同一时点与 REST 相同；不输出人工复核和输入明细。见 [M2 验证](M2-report.md)。Codex、DSH 等实际目标 Host 版本与互操作证据各自未提供，分别为 BLOCKED_EXTERNAL；SDK 通过不替代目标 Host。

2026-11-21（0024）：`app.openapi()`、`ROUTES` 和 SDK 实际 `list_tools` 对表，40 项注册无多余或漏项。`tests/test_facts.py::test_stdio_candidate_and_human_only_boundaries` 调用最后一项并按封闭 Schema 校验结果，对照 REST 和 Web 同一记录，校验跨项目、非法高信任字段、Agent 直调核验/澄清拒绝及同键重放。见 [M4 测试记录](M4-report.md)。所有既有工具的完整输入、输出、权限、副作用、并发和目标 Host 覆盖仍分别追踪。
