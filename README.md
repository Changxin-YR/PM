# PM

PMO Lite 独立项目管理系统，V1.0.4 规格的开发仓库。当前为 M0 增量，**不是可上线的完整系统**。原始输入、逐项追踪和阶段证据在 `docs` 下。

## 本地复验（Windows PowerShell）

需要 Python 3.12、uv 及运行中的 Docker Engine。首次初始化会在 `.local` 下生成本机开发数据库随机口令，不设默认管理员，也不提交凭据。Compose 只启动隔离的数据库。

```powershell
.\scripts\dev.ps1 setup
npm.cmd --prefix web ci
.\scripts\dev.ps1 check
.\scripts\dev.ps1 test
.\scripts\dev.ps1 browser
.\scripts\dev.ps1 bootstrap
.\scripts\dev.ps1 api
```

OpenAPI：`http://127.0.0.1:8000/openapi.json`。API 只在回环地址开发。测试通过随机 PostgreSQL schema 创建合成数据，启动单独的 HTTP 和 MCP 进程；不要将测试 URL 指向生产库。API 不会返回未授权的项目。

在第二个 PowerShell 中执行 `.\scripts\dev.ps1 web`，访问 `http://127.0.0.1:5173`。首次使用空库 bootstrap 交互输入组织、账号、姓名和口令，需至少 14 字符，不回显。不能在已初始化库重复运行。系统管理员不自动获得任何项目审批角色。Web 可登录、新建草稿、搜索、分页及改名；送审、成本、结项等尚非可用功能。退出会撤销数据库会话。构建命令 `npm.cmd --prefix web run build`。浏览器测试在 Windows 默认使用已安装的 Microsoft Edge，不需要生产口令或模型密钥。

点击项目名称可维护里程碑（最多八个）及需求草稿。项目进入冻结阶段后，草稿编辑权限不会代替正式变更审批。

项目资料的「联系台账」按项目登记客户、合作方及内部联系人，并记录影响力和期望。登记不会创建账号、绑定客户身份或授予签收权。人工项目成员可分页查看联系详情，Agent 仅在授权项目详情中获得最多 100 条摘要。

在项目资料的「立项卡」页签填写并保存材料；允许草稿信息不完整，保存不表示送审或批准。人工可读完整材料，Agent 通过项目详情只读契约允许的摘要。

工作台的「人员任命」支持发起人选择草稿并按同组织人员 ID 提名 PM 或 Sponsor。提名不会授予项目权限；有独立组织授权的人员在「待我发出的邀请」处理，被提名人从「我的邀请」接受或拒绝。实际业务任命权仍需签核，不为初始管理员自动开通。

项目资料的「项目成员」只展示有效成员角色。发起人自动登记为本草稿的 initiator，不能因此成为 PM 或 Sponsor，也不获得跨阶段的代理授权。

具有组织系统管理角色的人工账号能打开「审计记录」，按请求编号、结果查询本组织事件。HTTP 响应的 `X-Request-ID` 可用于核对记录；错误响应不回显提交的请求体。

MCP stdio 命令：项目 `.venv/Scripts/python.exe -m pmo.mcp_server`，工作目录为仓库根目录。具有 delegator 资格的人工用户通过「我的 Agent 令牌」选择项目、动作和最长 24 小时有效期，确认签发。仅将一次性交付的密钥通过受保护的环境变量 `PMO_AGENT_TOKEN` 传给 MCP 子进程；使用非默认 API 端口时设置 `PMO_API_PORT`。本人可撤销自己的令牌；管理员可从「组织令牌」撤销本组织的令牌，不可查看原密钥。详见 ADR 0004。不通过直接改库为真实用户授权。

本地已有开发库可用 `.\scripts\dev.ps1 migrate` 升级，不重建卷。`.\scripts\dev.ps1 outbox` 处理一批到期项目事件并记录本地消费回执；`.\scripts\dev.ps1 dead-letters` 查询死信标识和错误码，不输出业务载荷。租约 30 秒，失败指数退避，最多 5 次尝试。没有安装后台计划任务；本地事件回执不代表已向用户发送通知或完成任何审批。

数据库与私有附件需作为一个恢复点备份。开发命令、隔离演练和每日保留要求见 [联合备份与恢复手册](docs/ops/backup-and-recovery.md)。

独立部署测试库时，按 [数据库运行账号隔离](docs/ops/database-roles.md) 分开迁移、API 与 Outbox 登录。此流程不会切换现有本机预览的连接账号。
