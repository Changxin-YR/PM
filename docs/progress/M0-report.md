# M0：进行中，不满足退出门槛

起点 `45307c2`；输入审计提交 `5672e30`。开发分支 `dev/pmo-v1`。Windows、CPython 3.12.15、Docker PostgreSQL 16.14、Python MCP SDK 1.30.0。锁文件为 uv.lock。

| 实测命令 / 场景 | 实际结果 |
|---|---|
| 包内 08、15、19 各用隔离 Python 运行 | 全部 exit 0；仅证明规格一致性 |
| Alembic upgrade head → downgrade base → upgrade head | 三次 exit 0；0001，系统目录 421 个字段，不含 Alembic 自身表 |
| `python -m pytest -q --tb=short` | 4 passed / 16.00s，exit 0，独立 PG schema、真实 HTTP/stdio |
| `python -m mypy pmo --ignore-missing-imports` | 8 个源文件，exit 0；未启用 strict，不等于所有函数都有注解 |

测试范围：非空库拒绝降级、同令牌并发创建只写一次、幂等原响应重放、异载荷冲突、组织创建授权、无自动任命、新建项目查询、跨组织不可见、审计写入故障导致业务数据及授权回滚、stdio 三工具发现与调用、回包 Schema、非法分页、凭据撤销立即拒绝。协议客户端是 Python SDK，不是 Codex/DSH Host 验收。

已处理环境问题：系统默认 Git 缺少 HTTPS helper，使用已安装的 bundled Git fetch；依赖站下载超时，用 uv.lock 指定的 wheel 并逐项比对 SHA-256 后离线安装。没有改动机器全局依赖。

仍缺：完整 REST 契约与人类会话、细粒度 RBAC/授权管理、草稿更新及 revision、更多跨实体约束和事务负例、审计独立权限、Outbox 消费与重试、前端构建及 Playwright。3/40 工具、3/95 操作不等于 M0/M3 完成。CI 配置已提供，云上运行未验证。

## 后续增量：草稿乐观锁

前一代码提交 `b4fb4b2`。新增 `PATCH /api/v1/projects/{projectId}` 和第四个真实工具 `pmo_project_draft_update`。只允许本人 drafting 草稿，字段白名单为 name/description；请求签名包括项目、版本、字段。重复重放优先于旧版本冲突，但在权限复查之后。两个独立凭据同 revision 的并发请求得到 200/409，数据库最终 revision=2。`scripts/dev.ps1 test` 实际 5 passed / 9.51s，`scripts/dev.ps1 check` 三套静态检查及 mypy 退出 0。当前基线操作/工具均已实现 4 项。不要将这些用例外推到未实现的业务操作。

## 人工会话和浏览器验证

草稿锁提交 `9ad90df`。迁移 0002 增加两张工程支撑表；测试库升级/回滚/再升级和开发库升级成功。新增人工登录、读取当前身份及退出；采用 Argon2id、服务器端限速、HttpOnly SameSite cookie、CSRF 和会话摘要。真实人工与 Agent 共用项目记录，项目修订、审计、权限检查复用同一服务。仅本地 HTTP 开发允许非 Secure Cookie。生产未获准部署。

`scripts/dev.ps1 test` 六项通过（16.76s）；`mypy pmo --ignore-missing-imports` 十一个源文件通过；Vue 类型检查和 Vite 构建 exit 0（主包超过 500kB 警告）；`scripts/dev.ps1 browser` 首次 1 passed / 13.40s，真实 Edge、Vite、HTTP 和 PostgreSQL。以上是草稿链路证据，不是整期验收。

## 项目范围和里程碑

起点 `56b26f5`。迁移 0003 新增显式授权来源；被委托人获任 PM 不会使自动草稿令牌继续读取已送审项目。新增里程碑列表、创建、草稿修订三条 REST 和 MCP，以及 Web 项目资料抽屉。运行时基线 10/95 REST、7/40 MCP；真实 SDK 覆盖三个新增工具。最终 `scripts/dev.ps1 test` 8 passed / 11.47s，`scripts/dev.ps1 browser` 1 passed / 11.24s；`scripts/dev.ps1 check` 及 `npm.cmd --prefix web run build` exit 0。

负例覆盖：不同令牌争第八个槽位、同序号冲突、异项目读写、旧版本、冻结计划直接修改、非法 actualDate、自动授权跨阶段以及成员撤销。浏览器初测因项目名称改为可操作按钮导致旧单元格定位失效，更新为检查真实按钮后全程通过。延期预警、完成证据和三道关口未计入上述验收。

## 需求草稿与基线防护

起点 `652615e`，迁移仍为 0003。新增需求创建、分页筛选、草稿修订，真实 stdio 注册并调用三个对应工具。草稿 baselineVersion=0；项目已存在冻结版本时禁止直接创建或修改范围；不提供草稿删除、取消或确认按钮。浏览器显示验收标准和计划确认日期，MCP 返回按 V1 的封闭 DTO 序列化。

`scripts/dev.ps1 test` 9 passed / 10.57s；`mypy pmo --ignore-missing-imports` 18 文件 exit 0；浏览器初测精确定位优先级失败，给原生 select 补充无障碍名称，`scripts/dev.ps1 browser` 实测 1 passed / 21.09s；Vue 类型检查和 Vite 构建退出 0（主包体积警告仍存在）。测试中新设定的冻结记录仅用于拒绝路径，不代表已实现冻结审批。实时查询 8001 `/openapi.json` 有 13 个具名操作。当前 M0 未整体验收。
## 2026-11-20 立项卡复验（起点 81bd2a7）

开发库查询为 0004。立项卡支持草稿增量保存、完整人工读取、受限 Agent 摘要及不可改写的版本快照。恢复本机隔离数据库后，`scripts/dev.ps1 test` 10 passed / 15.60s，`scripts/dev.ps1 browser` 1 passed / 17.00s，含真实 PostgreSQL、HTTP、stdio、Edge；`scripts/dev.ps1 check` 三套规格校验和 21 个源文件类型检查退出 0，`npm.cmd --prefix web run build` 退出 0，仍有包体积警告。立项评审、送审关口和 Q1 尚未验收。

## 2026-11-20 Sponsor/PM 任命（起点 40267a6）

迁移 0005 创建独立任命授权与邀请记录。本机开发库实查为 0005。无预置真实授权。发起人和 Agent 只能提名；任命人发邀请、当事人接受或拒绝均为人工操作。拒绝、过期或越权不会产生有效成员。Web、REST、stdio 均使用相同服务。工程补充接口及签核边界见 ADR 0003。

`scripts/dev.ps1 test` 12 passed / 11.88s；`scripts/dev.ps1 browser` 1 passed / 14.80s（三个独立 Edge 会话提名、发邀请、接受，刷新后台账可见）；`scripts/dev.ps1 check` 三套规格校验与 mypy 23 文件 exit 0；`npm.cmd --prefix web run build` exit 0（主包体积警告）。基线操作 19/95，加上两个补充操作；MCP 12/40。审计、越权、幂等及真实 MCP 有相应用例，并不表示 T02、Q1 或 M0 整体验收通过。

## 2026-11-20 联系台账（起点 5d874d2）

在 0005 的 stakeholders 表上实现项目范围的草稿登记、人工分页读取与受限项目摘要。POST 不接受身份、角色或核验字段，人工列表即使被误列入令牌范围也拒绝 Agent。项目详情至多列出最新 100 条摘要，不返回影响力、期望或身份关联。未实现客户核验或确认权。

真实 PostgreSQL、HTTP、stdio 共 13 passed / 10.93s；Edge 1 passed / 17.05s；三套静态规格校验、mypy（27 文件）、Vue 类型检查与构建通过，主包体积警告仍在。测试检查了跨组织拒绝、幂等重放只写一次审计、成员表不变及 101 条记录下的摘要上限。当前 21/95 个基线 REST（另 2 个工程接口），13/40 个 MCP。M0、T04 尚未整体验收。

## 2026-11-20 本地 Outbox 投递（起点 ce50cd0）

0006 添加消费回执、尝试记录、到期索引及 Stakeholder 类别约束。开发库已升级，容器内 psql 实查为 0006。双消费者争取同一事件、租约过期接管、过期 worker 拒绝、消费成功但未确认后跨进程恢复、重复消费、指数退避、死信脱敏查询、带数据升级和拒绝破坏性降级均在真实 PG 验证。首轮失败由 0005 合成项目夹具漏填 revision 引起；补齐后 `test` 15 passed / 18.22s，`browser` 1 passed / 15.31s，三套静态检查及 mypy 28 文件通过。仅证实本地项目事件收件，不证明 LR 站内通知、定时补跑或业务预警已实现。T10 不判通过。
