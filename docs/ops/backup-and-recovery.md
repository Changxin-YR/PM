# PostgreSQL 与私有附件联合备份

这是本地及隔离环境演练手册，不是生产切换授权。备份包含全库数据、会话表、审计和原件，按敏感数据处理。归档和演练目标必须在项目公开目录及原件目录之外，使用运营账号独占的磁盘目录；Windows 上先检查 NTFS ACL，独立副本用受控加密存储。不上传客户数据，不把备份或日志中的凭据提交到 Git。

## 备份与保留

在仓库根目录，已运行默认开发 Compose 且开发库已迁移后执行：

```powershell
.\scripts\recovery.ps1 backup -OutputDirectory .local\recovery-points
```

独立环境使用已安装且版本与服务端匹配的 `pg_dump` 和 `pg_restore`（开发环境当前为 PostgreSQL 16）。在运行备份的专用账号下，通过受保护的环境读取源 `DATABASE_URL` 和 `PMO_EVIDENCE_DIR`；不要传在命令行参数或写到日志里。源须为一套单独的应用库，`public` schema 下有 Alembic 版本、附件元数据及其对应卷。不要在迁移或存储维护期间运行备份。

```powershell
.\.venv\Scripts\python.exe -m pmo.recovery backup D:\Protected\PMO\points
```

通过 PostgreSQL 导出的同一只读快照生成全库自定义格式 dump 并读取对象清单。只复制快照引用的原件，逐一校验长度和 SHA-256；最后将内含 `database.dump`、`evidence` 与 `manifest.json` 的目录发布为一个恢复点。缺失或不符即失败，不得使用 `.incomplete-*`。`unreferencedCandidates` 也可能包含快照后提交的正常上传；不要凭该计数清理活跃卷。硬中断残留应在确认进程已退出后由运营人员隔离检查。

在经授权的环境把同一命令配置为每日任务，例如 Asia/Shanghai 02:00。Windows 任务计划程序使用上述虚拟环境的 Python 可执行文件，参数为 `-m pmo.recovery backup <受保护目录>`，起始目录为仓库，凭据由任务运行账号的受保护配置注入。设置错过后补运行、禁止同一任务重叠执行，并对非零退出或当日没有成功恢复点告警。仓库不会注册实际计划任务。保留至少最近七个成功备份日的恢复点；同日多份不抵扣漏备日。工具不自动删除备份，`daysPresent` 是目录盘点提示，不是校验或保留合规证明。日志只留时间、点 ID、版本、对象数、退出码，不写敏感负载。

## 每周隔离恢复演练

只使用信任来源、访问受控且完整复制到演练机的一整个恢复点。不要打开来源不明的 PostgreSQL dump；归档中的 SQL 由数据库执行，内置 SHA-256 只校验内容一致性，并不证明来源。目标使用专用隔离 PostgreSQL 实例，通过 `PMO_RESTORE_ADMIN_URL` 指定回环管理连接，该账号须能够创建、恢复和删除演练数据库。不能使用线上数据库的凭据、转发端口或活跃附件目录。默认 Compose 包装只用于本机开发演练。

```powershell
.\scripts\recovery.ps1 restore-drill -Point '.local\recovery-points\<point-id>' -OutputDirectory .local\recovery-drills -TrustedBackup
```

独立环境命令：

```powershell
.\.venv\Scripts\python.exe -m pmo.recovery restore-drill 'D:\Protected\PMO\points\<point-id>' 'D:\Protected\PMO\drills' --trusted-backup
```

命令在创建数据库前检查包和全部原件，再从 `template0` 建立唯一的 `pmo_restore_*` 库，撤销 `PUBLIC` 连接许可，单事务恢复并比对版本及完整对象清单。成功才发布与数据库同名的附件目录并输出 `result=verified`；不会切换正在运行的应用。演练不导入原生产角色与授权、不搬运 tablespace，不能用作生产原位恢复。校验失败会清理本次新建的演练库；异常退出后没有成功回执的库或目录不能使用。

每周记录备份时点、恢复开始/结束、迁移版本、对象数、退出码及抽验项目/附件。隔离环境需要应用复验时，显式连接新库和同名附件目录，保持与外部网络隔离且不启动 Outbox；不要复用生产客户端和会话。记录结果后只清理本次返回的数据库与目标目录，先复核名称和绝对路径。保留计划任务日志、演练记录，并对失败执行补跑和缺失恢复点调查。本文不宣称目标机器或定期计划已验收。
