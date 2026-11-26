# R5 工作台使用手册

在 [独立合成工作台](http://127.0.0.1:18788/) 试用。`sim-pm-a` 管理实施样例 `SIM-EXEC-001`，`sim-pm-b` 管理立项草稿 `SIM-NEW-001`。在私密交互式 PowerShell 中读取合成口令。所有演练使用合成材料。

```powershell
$env:PMO_PILOT_RUNTIME='native'
$env:PMO_PILOT_INSTANCE='r5-preview'
.venv\Scripts\python.exe -m scripts.pilot_seed login pm-a
```

| 任务 | 页面与操作 |
|---|---|
| 开项目、送审、处理审批 | 从项目台账进入项目资料。立项卡、方案及结项材料分别在对应关口提交。审议与补强待办从左侧个人入口查看。 |
| 记人天和核验实际 | 在日报周报填写本期总量和已知分类，明确来源。发布后检查周报凭证差额。按月和类别提报，独立核验后成本台账才增加实际；历史未分类仍可查。 |
| 超支重估 | 在三线健康和预警中心查看差异，从项目补强任务打开“查看重估”。PM 提交书面材料，现任独立有权人按适用策略记录投入决议。 |
| 正式周报 | PM 核对、提交并确认周报。下载正式 A4 摘要，在浏览器预览或保存为 PDF。有关送达的状态、版本及附件摘要在该报告的送达区域查询。 |
| 查来源与复盘 | 在项目助理选择立项、过程、结项或台账模式，核查数据时点及出处。本地试用环境默认走人工路径。归档前完成四问及人工确认教训。 |

隔离预览运行于 PostgreSQL `16.14`、`55454/pmo_pilot`，API 端口 `18789`。重新启动同一隔离实例时，用下列命令。历史试点使用另一实例。

```powershell
npm.cmd --prefix web run build
.venv\Scripts\python.exe -m scripts.pilot_local init
.venv\Scripts\python.exe -m scripts.pilot_local start
.venv\Scripts\python.exe -m scripts.pilot_seed seed
```
