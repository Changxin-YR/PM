# 隔离部署与运行检查

适用范围：本机合成验证，依据主方案 §9.1、§14 第 6 项。`deploy/compose.yaml` 独立于开发数据库。它只把 Web 发布到 `127.0.0.1`，不发布 PostgreSQL、API 或 MCP 端口，不打开外部模型，不支持直接用于生产或共享访问。正在开发时优先用 `scripts/dev.ps1 api`、`scripts/dev.ps1 web` 热重载，候选版再使用镜像。

## 启动与空库开户

需要 Docker Engine、支持 `--wait` 的 Docker Compose，以及构建时可访问依赖注册表。复跑 smoke 还需本机 `.venv`、Node 和 `npm.cmd --prefix web ci`，Windows 默认使用本机 Edge。下列命令为每套演练生成不同项目名、数据卷和 4 个 256 位随机密码。先确认端口空闲；已占用时改为另一个回环端口。

```powershell
$p = 'pmo104-rc-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$cfg = ".local/$p.env"
New-Item -ItemType Directory -Force .local | Out-Null
@('DB','MIGRATE','API','OUTBOX') | ForEach-Object {
  "PMO_DEPLOY_${_}_PASSWORD=$([Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)))"
} | Set-Content -LiteralPath $cfg -Encoding ascii
Add-Content -LiteralPath $cfg -Encoding ascii -Value 'PMO_DEPLOY_PORT=18760'
$stack = @('compose','-p',$p,'--env-file',$cfg,'-f','deploy/compose.yaml')
docker @stack up --build -d --wait
docker @stack ps -a
```

以上示例使用 PowerShell 7。密码环境文件仅置于受保护的本机目录；不要打印 `compose config` 或完整容器环境，不要把 `.local` 复制到发布包。PostgreSQL 首次启动在专用卷初始化数据库角色，之后不会因编辑 env 自动轮换。`migrate` 以独立所有者登录应用迁移与 ACL 并退出；若退出非零，不启动业务运行服务或跳过迁移。API/规则扫描使用受限 API 账号，Outbox 使用只允许投递的独立账号。Web 通过同源代理访问 API，API 挂载私有原件卷，扫描仅读；Web、Outbox 不挂任何原件。

首次使用空库，以下两个入口二选一：

- 演练：运行 `.venv/Scripts/python.exe scripts/deployment_smoke.py $p`。脚本先验证唯一隔离项目、配置归属、空库及回环端口，再创建合成帐号、15 个项目和里程碑，确认 Outbox 消费回执，用 Edge 取样，并实际停启这套数据库。不得指向任何使用中的环境。
- 手工：在交互终端运行 `docker @stack run --rm --no-deps migrate python -m pmo.bootstrap --organization "演练组织" --username "local-admin" --name "演练管理员"`，按提示输入并确认独立口令。打开 `http://127.0.0.1:18760`，或实际配置的端口。管理员不因此成为项目 Reviewer、Sponsor 或客户代表。

## 探针、日志与中断

`GET /health/live` 只检查应用进程可响应；`GET /health/ready` 校验数据库连通、Alembic head 与私有目录的读写权限，不读附件内容或发起业务写入。未就绪返回 503 和固定状态；详细类别在内部日志。就绪不等同于全部附件完整、Outbox 已清空或定时备份通过。

通过 `docker @stack logs --tail 100 api scheduler outbox` 查看日志。API JSON 记录服务端 `requestId`、`operationId`、状态和耗时，≥1 秒标记 `slow`；不记录请求参数、正文、凭据或异常消息。Nginx、uvicorn 的请求访问日志不启用；保留业务审计用于核对成功/拒绝和来源，不能用运行日志代替审计。对 5xx、持续未就绪、`rules.failed`、`outbox.failed`、死信积压报警，目标环境的告警接收与留存策略仍需部署验证。`docker @stack exec -T outbox python -m pmo.outbox --dead-letters` 只读列出死信元数据。消费回执证明本地去重投递，不是外部邮件或消息送达凭证。

数据库恢复健康后，如服务耗尽重试，运行 `docker @stack up -d --wait api scheduler outbox web`，确认流量、待处理扫描和消费状态。如果 API 容器替换，重新启动 Web 代理以重解析 API 容器地址。要停演练服务可用 `docker @stack stop`，保留数据卷；不要对已有数据删除卷。

## 升级、回退与放行

已存在的目标库先备份数据库与私有原件，验证恢复点并在独立副本演练。暂停写入与 worker 后迁移，启用同一构建的 API/worker 并检查 readiness。迁移失败时保持停写、记录原因；正式写入后的迁移禁止靠强制 downgrade 丢掉审计、确认或证据。必要时恢复到新的隔离库并人工核对差异；不要就地覆盖目标库。参见[账号隔离](database-roles.md)及[联合备份与恢复](backup-and-recovery.md)。

本地镜像走锁定依赖，但基础镜像标签不是生产制品固定摘要。目标环境需固定并审核镜像摘要、使用批准的 HTTPS 和可信代理配置、外置并轮换 secrets，核验实际访问边界、告警、性能和周期恢复。当前演练不代表生产放行。
