# R5 周报分类与送达验收

对应 SRC-026、032、033、042、069、072。数据和浏览器验证在隔离 PostgreSQL 中执行。

| 场景 | 实际结果 | 证据 |
|---|---|---|
| 周报申报与成本入账 | `7.00` 人天拆为研发 `2.50`、实施 `3.00`、管理 `1.50`。三个候选待核验时实际仍为 `0.00`，逐项独立核验后实际为 `7.00`。同周已有研发净额 `2.50` 时仅剩 `4.50`。已有未分类 `7.00` 时进入人工对账；历史总量周报仍可按原口径回读与导出。 | `test_r5_reporting.py`、`test_weekly_report_workflow.py`、`R5-01-分类差额.png` |
| 区分报告、候选与实际 | 结构化分项精确引用 `effort_breakdown/{index}/personDays`。同值不同类别各自定位，叙述累计值、错业务时点、错数值、失效版本不能代替本期原话。Fact 经过核验后仍保持与成本台账独立。 | `test_r5_reporting.py`、`R5-01-分类Fact来源.png` |
| 人天和期间反例 | 拒绝分类合计错误、负数、超过两位小数、金额分类、缺依据、无效 UTC 月份、发生时点在周外、版本错误、本人核验和错误附件。相同请求重放、异参冲突和并发去重通过。 | `test_r5_reporting.py`、`test_postings.py` |
| 正式附件 | 同一服务渲染下载附件和隔离发送附件。完整 HTML 字节、SHA-256、报告版本和内容摘要可对照。三个固定文字区块、四条人天分类和长中文补充说明共同打印为实际 A4 单页，显示节录和源报告提示。 | `browser_r5_reporting.py`、`R5-正式周报-长中文-A4.pdf`，实测 `594.96 × 841.92 pt` |
| 隔离收件 | 当前 PM 在已核准收件范围内登记发送，Outbox 处理后回读隔离收件箱时间、消息号和附件摘要。收件授权必须来自不同的在岗 Sponsor。渠道实收记录只认定为隔离回执，外部实际收件仍待验收。 | `test_r5_reporting.py`、`R5-04-隔离送达回执.png` |
| 送达反例与恢复 | 草稿及未正式发布、跨项目授权、角色无权、错误附件摘要、同键异参、已撤销授权和被替换未发送版本被阻止。最小权限工作进程在收件事务提交后、Outbox 确认前中断，重启后仍只有一条收件记录。工作进程未获得隔离作用域时无法发送。 | `test_r5_reporting.py`、`test_outbox.py` |

正式分类抵扣行为受版本化 `report.effort`／`RP-01` 控制，审批前仍可查看已保存报告和已有总量账务，分类候选及核验按正式策略执行。混合报告中明确未分类的投入保留为待澄清，工作台不会替用户归入研发、实施或管理。

送达使用 `report.delivery`／`RP-02` 签核入口。版本 `0043` 存储收件授权、发送候选与隔离收件凭证。正式联调仍需业务签核、真实渠道和明确收件地址范围、实际收件方确认，以及对应供应商的确认或拒绝语义、请求幂等与未知结果核查配置。当前只配置隔离收件箱，外部网络发送和人工复核后重发尚未接通。隔离收件箱异常有失败及未知状态，匹配失败不会生成外部实收记录。

## 复验命令

```powershell
$env:PMO_PILOT_RUNTIME='native'
.venv/Scripts/python.exe -m scripts.pilot_regression -- tests/test_r5_reporting.py tests/test_weekly_report_workflow.py tests/test_outbox.py -q --junitxml=.local/r5-reporting-target.xml
.venv/Scripts/python.exe -m scripts.pilot_regression -- tests/test_r5_reporting.py tests/test_facts.py tests/test_report_analysis.py -q --junitxml=.local/r5-reporting-2.xml
.venv/Scripts/python.exe -m scripts.pilot_regression -- tests/browser_r5_reporting.py tests/test_r5_reporting.py -q --junitxml=.local/r5-reporting-final.xml
```

最后一项实际运行通过 13 条，证据输出到 `.local/r5-reporting-visual/`。分类字段的 MCP 协议通过现有 `pmo_report_submit`、`pmo_report_get` 和 `pmo_fact_propose` 广告发现，正式发送与授权回执均由人操作。
