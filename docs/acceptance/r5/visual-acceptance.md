# PMO R5 前端核验

## 界面实施与差异

视觉依据为用户执行包的 UI-R4 状态 01–47 和 R5 补充状态 01–06。原型 01 与 32 保留独立证据编号，共用原有阶段流转组件。系统中使用实际角色、状态、时点及版本。合成运行数据与原型的展示项目分别取值。

| 核验项 | 本机实际界面 |
| --- | --- |
| 导航和排版 | 蓝白主题，200 px 侧栏和 52 px 顶栏，24 px 页标题及 16 px 区块标题。项目模式和全局模式共用一个侧栏。窄屏由同一个导航面板接管，仍支持 Esc、焦点返回和项目页键盘切换。 |
| 登录和邀请 | 保留双栏品牌页和 380 px 表单、原帮助文字、登录和邀请码入口。品牌区保留图标，原型中的离线标识不在产品显示。 |
| 概览、立项和健康 | 概览优先展示当前四指标，最近阶段和台账活动并列。冻结立项值以可选文字显示。当前健康和历史快照独立成区，正式和预览状态、规则版本、来源和人工申诉均可达。 |
| 表格、详情和操作 | 13 px 表格、浅色表头、约 42 px 常规行和长内容换行。宽表由自身容器滚动。需求历史和交付弹窗继续使用同一证据选择及提交路径，分页与筛选沿用原业务。 |
| 助理与评审 | 保留四模式、查询和预览控件。立项材料与评分、建议草稿和历史教训分别显示。工作台分数刻度沿用实际 1–5 维度，不把建议混作正式裁决。 |
| 周报与成本 | 人天分类位于周报表单，正式报告显示送达回执区域。重估从既有任务列表打开全宽详情。金额和人天使用独立分类表和对账口径。 |

## 功能保留与复现

[功能保留矩阵](feature-parity.csv) 基于 `fe7c50b` 与当前 Vue SFC 模板的字段、事件、守卫、表格列和组件调用。`apiMethodAndPath` 列列出组件的相关请求表达式。事件经共用提交函数发起时，可由 `afterHandler` 和组件源码追踪实际请求体及重试。`originalTest` 和 `afterTest` 是相关回归入口，业务正反例以各套测试断言和运行结果为准。文案在图标按钮、两个尺寸的布局之间保留语义。新增业务状态是否通过由对应业务验收证据决定。

```powershell
node scripts/r5_feature_parity.cjs
$env:PMO_PILOT_RUNTIME = 'native'
$env:PMO_R5_VISUAL_OUTPUT = '.local/r5-visual-repro'
.venv/Scripts/python.exe -m scripts.pilot_regression -- tests/browser_visual_r5.py -q --junitxml=.local/r5-visual-repro.xml
```

截图脚本启动真正的 Microsoft Edge 并连接隔离 PostgreSQL `55444` 上临时建立的工作台。它使用角色有效的会话和真实页面导航，输出 PNG、`index.html` 与 `checks.json`。桌面检查 1440、1280、1024 CSS px，窄屏检查 820、390 CSS px，浏览器缩放由 Edge `chrome.tabs.setZoom` / `getZoom` 设置和核实，并从页面读取 `devicePixelRatio`。重放、权限撤销、A4 和真实模型交由业务回归和独立外部验收记录。

Edge `158` 上 283 项布局与导航断言全通过。浏览器运行错误与 HTTP 5xx 均为 0，前端 Git 树为 `516a43fce95773c48040b6de64daa977f72ee1fb`。覆盖 UI-R4 全部 47 个编号、R5 的周报分类、超支重估、正式周报送达界面以及 390 px 移动导航焦点和宽表滚动。响应式与原生缩放均覆盖登录、邀请、19 个项目入口和 12 个全局入口。评分锚点、快速立项和评分实际结果另有专门场景截图。

全量 Edge 首轮识别到需求指标排版在百分比后插入空格，打断原有的指标文本。`a8d6287` 恢复原文本格式，现有草稿至需求审核用例原样复测通过，日志在 `.local/r5-visual-draft-fixed.log`。

- 全站截图浏览和尺寸明细在本机 `.local/r5-evidence/gallery-final/index.html` 与 `checks.json`，测试输出分卷记录在 `.local/r5-evidence/edge-final-*.log` 与 `.xml`。
- 周报分类凭证差额、结构化 Fact 来源及隔离收件回执在 `.local/r5-evidence/gallery-final/`。
- 评分提示、快通前后、实际结果校准及超支重估独立裁决在 `.local/r5-evidence/screenshots/`。
- 可解压检查的统一交付包为 `.local/PMO-R5-Edge-视觉截图与补充证据.zip`。
