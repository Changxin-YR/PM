import {chromium, expect} from '@playwright/test'
import {mkdir, readFile} from 'node:fs/promises'
import {join} from 'node:path'

const url = new URL(process.env.PMO_BROWSER_URL)
const sessions = JSON.parse(process.env.PMO_TEST_SESSIONS)
const [projectId, reportId, longReportId] = JSON.parse(process.env.PMO_R5_REPORTING_SCOPE)
const output = process.env.PMO_R5_REPORTING_OUTPUT
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL ?? (process.platform==='win32'?'msedge':'chromium')})
const button = (scope, name) => scope.getByRole('button', {name, exact:true})
await mkdir(output, {recursive:true})

async function open(role, id) {
  const context = await browser.newContext({viewport:{width:1440,height:930}, locale:'zh-CN', timezoneId:'Asia/Shanghai'})
  await context.addCookies([{name:'pmo_session', value:sessions[role].cookie, domain:url.hostname,
    path:'/api/v1', httpOnly:true, sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf), sessions[role].csrf)
  const page = await context.newPage()
  const target = new URL(url)
  target.hash = `project=${projectId}&tab=reports&report=${id}`
  await page.goto(target.href)
  const detail = page.getByRole('article',{name:'报告详情',exact:true})
  await expect(detail).toBeVisible()
  return {page,detail,context}
}

async function capture(page, region, name) {
  await region.scrollIntoViewIfNeeded()
  await page.screenshot({path:join(output, name), animations:'disabled'})
}

async function post(page, control, suffix) {
  const [response] = await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(suffix)), control.click()])
  expect(response.request().headers()['idempotency-key']).toBeTruthy()
  expect(response.request().headers()['if-match']).toMatch(/^[1-9]/)
  expect(response.status(), await response.text()).toBe(200)
  return (await response.json()).data
}

try {
  const {page, detail} = await open('pm', reportId)
  const candidates = detail.getByRole('region', {name:'周报凭证候选',exact:true})
  await expect(candidates.getByText('7.00 人天',{exact:true})).toBeVisible()
  await expect(candidates.getByText('2.50 人天',{exact:true})).toBeVisible()
  await expect(candidates.getByText('4.50 人天',{exact:true})).toBeVisible()
  await capture(page,candidates,'R5-01-分类差额.png')

  const facts = detail.getByRole('region',{name:'Fact 证据与澄清',exact:true})
  const classification = button(facts,'引用实施 3.00 人天')
  await expect(classification).toBeVisible()
  await classification.click()
  const form = facts.getByRole('form',{name:'Fact 候选',exact:true})
  await expect(form.getByLabel('原话')).toHaveValue('3.00')
  await expect(form.getByLabel('原话')).not.toBeEditable()
  await capture(page,form,'R5-01-分类Fact来源.png')
  await post(page,button(form,'保存候选'),`/reports/${reportId}/facts/proposals`)
  await expect(facts.getByText(/effort_breakdown\/1\/personDays:0/)).toBeVisible()

  const delivery = detail.getByRole('region',{name:'正式周报送达',exact:true})
  await button(delivery,'登记送达').click()
  const dialog = page.getByRole('dialog',{name:'确认周报送达',exact:true})
  await expect(button(dialog,'确认登记')).toBeDisabled()
  await dialog.getByLabel('已授权收件人').selectOption({index:1})
  await post(page,button(dialog,'确认登记'),`/reports/${reportId}/delivery`)
  await expect.poll(async()=>{
    await button(delivery,'刷新送达回执').click()
    return await delivery.getByText('隔离收件箱已确认',{exact:true}).count()
  }).toBe(1)
  await expect(delivery.getByText('待外部验收',{exact:true})).toBeVisible()
  await capture(page,delivery,'R5-04-隔离送达回执.png')
  await page.context().close()

  const {page:reviewer, detail:readonly, context} = await open('reviewer',longReportId)
  await expect(readonly.getByRole('region',{name:'正式周报送达',exact:true})).toHaveCount(0)
  const [download] = await Promise.all([reviewer.waitForEvent('download'),button(readonly,'下载一页周报').click()])
  expect(download.suggestedFilename()).toBe('项目周报-2025-W02-v1.html')
  const printable = await readFile(await download.path(),'utf8')
  const print = await context.newPage()
  await print.setContent(printable)
  await expect(print.getByText(/未分类／待澄清 0.50 人天/)).toBeVisible()
  await expect(print.getByText(/4 项已节录，详见原报告/)).toBeVisible()
  await expect(print.getByText('补充说明已节录，详见原报告。',{exact:true})).toBeVisible()
  const pdf = await print.pdf({path:join(output,'R5-正式周报-长中文-A4.pdf'),preferCSSPageSize:true})
  expect(pdf.toString('latin1').match(/\/Type\s*\/Page\b/g)).toHaveLength(1)
  await context.close()
} finally {
  await browser.close()
}
