# M3 工具与目标 Host：部分实测，未整体验收

2026-11-20：`tests/test_runtime.py::test_mcp_stdio` 使用真实 SDK 子进程，发现、调用并校验 13/40 个工具的结构化数据。项目、里程碑、需求、立项卡、角色提名、联系台账复用 REST 的范围与事务；撤销后拒绝。人工成员列表和审计列表不暴露为工具。

其余 27 项未实现。2026-11-20 实测从人工签发到 SDK stdio 读、越权拒绝、撤销后原进程即时拒绝，见 tests/test_agent_tokens.py。SDK 不等于目标 Host。T09 未通过，不宣传 Codex/DSH/其他客户端兼容。
