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
