# PostgreSQL 运行账号隔离

适用于一套独立的 PMO 数据库（`public` 业务 schema，迁移 0016 起）。这是经过本机合成数据演练的配置流程，不是生产切换授权。开发便捷脚本仍使用 Compose 专用开发账号，不自动修改已有数据库的授权。

| 登录 | 职责 | 禁止 |
| --- | --- | --- |
| 迁移账号 | 数据库、schema 对象所有者；运行 Alembic、空库初始化、应用 ACL | 不进入常驻 API、MCP 或 worker 环境 |
| API 账号 | 执行业务事务、读取授权数据、追加审计及 Outbox | 无 DDL、业务授权台账写入、审计改写或 Outbox 投递状态修改权限 |
| Outbox 账号 | 领取事件、更新租约/投递状态、追加回执和通知 | 无账号、凭据、项目资料、审计、原件卷访问权限 |

## 初始化与升级

1. 由 DBA 在已授权的隔离实例创建三个不同的登录角色；不复用 PostgreSQL 超级用户。使用 SCRAM 和受保护的认证配置；密码不要放入 SQL 文件、命令行或 Git。三个角色都不须 `SUPERUSER`、`CREATEROLE`、`CREATEDB`、`REPLICATION` 或 `BYPASSRLS`；两个运行角色不得加入任何组或拥有任何数据库对象。由 DBA 将专用数据库的所有权交给迁移账号。限制 `pg_hba.conf` 和网络，只允许对应服务访问该数据库；备份和集群级管理凭据单独保管。
2. 停止目标库的 API、规则扫描、报告分析和 Outbox。只在迁移终端注入迁移账号的 URL，运行 `alembic upgrade head`；首次为空库时仍由迁移账号独立运行 `pmo.bootstrap`。已有库必须先备份并核对当前数据库与对象归属，不批量 `REASSIGN OWNED` 到真实环境。
3. 将同一迁移 URL 注入 `PMO_MIGRATION_DATABASE_URL`，运行以下命令。替换为 DBA 已建好的登录名；该命令不会创建账户或业务授权记录。

```powershell
.\.venv\Scripts\python.exe -m pmo.database_roles --dedicated-database --api-role pmo_api --worker-role pmo_worker
```

4. 成功后，以隔离进程分别向 API 和 `python -m pmo.outbox` 注入各自的 `DATABASE_URL`。运行服务不要继承 `PMO_MIGRATION_DATABASE_URL` 或其他服务的凭据；MCP stdio 仅凭受限应用令牌调用 HTTP。迁移/ACL 失败时保留隔离状态，不通过授权所有表或回退到所有者凭据绕过。

从 0023 起，在持有 API 数据库权限（非迁移权限）和只读私有原件目录的独立进程运行 `python -m pmo.rule_scan --watch`；本机已迁移隔离开发库可用 `scripts/dev.ps1 rules` 处理一批。扫描处理增量输入以及中国时区每日 09:00 日槽，停机后扫描当前材料，不补造历史。Outbox 另行运行；快照不依赖投递的即时成功。进程只输出批次计数；健康日志及访问控制按实际部署环境管理。

从 0025 起，报告分析默认 `PMO_REPORT_ANALYSIS=0`。要启用，在 API 和 `python -m pmo.report_analysis_worker --watch` 同步设 `PMO_REPORT_ANALYSIS=1`；只给独立分析进程注入 API 运行账号的 `DATABASE_URL`、显式 `PMO_REPORT_MODEL_URL`（HTTPS，隔离测试允许回环 HTTP）及可选的网关密钥 `PMO_REPORT_MODEL_TOKEN`。地址对应工具禁用的 JSON 转发网关；不是任一厂商的聊天接口地址，真实适配另验。该进程不挂附件卷、不持迁移或 Outbox 凭据。需先审批报告文字的外发范围和网络 ACL。作业租约 40 秒，单次调用最长 12 秒；退出码 1 或作业长期排队应报警。开启/关闭无需重写报告或补做人工签核。

该命令在同一事务内撤销专用库和 `public` 对 `PUBLIC`、两个运行账号的既有授权（含列级权限），按白名单重授。保留不属于这三个主体的已有授权；同一实例中其他数据库的访问仍需 DBA 控制。从备份恢复后不继承原库 ACL，必须对应新环境重新应用并验证。新迁移新增的表默认不授予运行账号权限；开发人员需更新白名单和回归后才可启用。

PostgreSQL 的行锁要求一定的列级 UPDATE 权限。对仅供并发核验的授权表，ACL 只使一列满足锁权限，并由迁移所有者控制的语句触发器拒绝运行账号实际 UPDATE。审计可读及追加，不能覆盖、删行或截断；查询 API 仍按应用权限裁剪。数据库隔离不替代 API 项目 RBAC，也不把 API 连接凭据当成普通用户的凭据。泄漏 API 凭据仍可能读到服务可处理的数据并发起被许可的数据库写入，须单独保护和轮换。

复验入口：`tests/test_database_roles.py`。测试新建并清理专用合成数据库与三套测试角色，不运行在已有业务库上。目标环境的网段、认证配置、凭据轮换与运维签核须另留证据。
