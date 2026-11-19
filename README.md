# PM

PMO Lite 独立项目管理系统，V1.0.4 规格的开发仓库。当前为 M0 增量，**不是可上线的完整系统**。原始输入、逐项追踪和阶段证据在 `docs` 下。

## 本地复验（Windows PowerShell）

需要 Python 3.12、uv 及运行中的 Docker Engine。首次初始化会在 `.local` 下生成本机开发数据库随机口令，不设默认管理员，也不提交凭据。Compose 只启动隔离的数据库。

```powershell
.\scripts\dev.ps1 setup
.\scripts\dev.ps1 check
.\scripts\dev.ps1 test
.\scripts\dev.ps1 api
```

OpenAPI：`http://127.0.0.1:8000/openapi.json`。API 只在回环地址开发。测试通过随机 PostgreSQL schema 创建合成数据，启动单独的 HTTP 和 MCP 进程；不要将测试 URL 指向生产库。API 不会返回未授权的项目。

MCP stdio 命令：项目 `.venv/Scripts/python.exe -m pmo.mcp_server`，工作目录为仓库根目录。仅接受环境中的 `PMO_AGENT_TOKEN` 和可选的 `PMO_API_PORT`。当前支持 `pmo_project_list`、`pmo_project_get`、`pmo_project_create`。测试夹具签发限时合成令牌并真实调用，尚无面向用户的令牌管理页。不应通过直接改库为真实用户授权。
