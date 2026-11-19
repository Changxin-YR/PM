# V1.0.4｜接口响应与序列化规范

1. **REST 仍是应用服务的稳定入口**：`12_OpenAPI_路由请求基线.yaml` 定义95个操作的路径、方法、输入及最小权限标记。其通用 `OkResponse.data` 只是设计基线，M0 必须使用 Pydantic 生成具体的资源级响应 DTO，不可用空对象或万能 `dict` 冒充完整接口。
2. **MCP 已有业务级最小输出合同**：`11_MCP_工具契约.json` 中40个工具，每个 `outputSchema` 的 `ok=true` 分支具有特定 `data` 的结构化必填字段，并拒绝额外未知字段。它不意味着实现 SDK 后能自然运行，M0 必须与实际 MCP Tool structuredContent 协商和 Host 行为测试。
3. **权威数值**：成本/人天/比率的计算全部在服务器用 Python `Decimal`/PostgreSQL NUMERIC；HTTP/MCP JSON 输出原则上用十进制字符串，如 `"100.00"`、`"0.1500"`。前端转换仅用于展示，不能回写前端计算结果作为权威记账。
4. **不可信数据**：健康快照返回 `{dataTrust:"untrusted", asOf, ruleVersion, sourceRefs}`，可附旧快照但显式 `historical=true`；不得用旧快照覆盖本次结果，也不得把零分母展示为 100%。
5. **创建与写入**：返回稳定 ID、资源 revision、operationId、asOf/sourceRefs/ruleVersion；同幂等键同请求返回原始稳定业务结果（含重复时的语义状态码）。
6. **分页**：项目/需求/成本等列表必须返回 `items,page,pageSize,total`；授权过滤应在查询 SQL 层实现，不能先取全部数据再本地删减。
7. **M0 可调整**：例如某些项目状态查询需要增加 `gateGaps` 字段、Tool 某个输出需要添加一个状态枚举，允许按工程 ADR 同步修改 REST DTO、MCP JSON Schema、前端 TS 类型和测试；但不能把已封闭的结果对象降级为 `additionalProperties=true`。
8. **错误结果**：MCP 调用失败应使用 MCP SDK 支持的标准工具错误承载方式（例如 `isError=true`），并返回受控错误 code/message/retryable，不暴露 SQL 堆栈、令牌或内部绝对路径。不同 SDK 版本的真实结构要经锁版本后的运行测试确认。
