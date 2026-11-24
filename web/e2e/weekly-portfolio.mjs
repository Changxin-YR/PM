import {join} from 'node:path'
import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL
const sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const exact=name=>({name,exact:true})
const button=(scope,name)=>scope.getByRole('button',exact(name))
const pageErrors=[]

async function signedIn(role, width) {
  const session=sessions[role]
  const context=await browser.newContext({viewport:{width,height:860}})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,
    path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf), session.csrf)
  const page=await context.newPage()
  page.on('pageerror', error=>pageErrors.push(error.message))
  await page.goto(url)
  expect(new URL(page.url()).origin).toBe(new URL(url).origin)
  await expect(page).toHaveTitle(/PMO/)
  return page
}

async function portfolio(page) {
  await showNavigation(page)
  await button(page,'预警中心').click()
  await page.getByRole('tab', exact('组合周报')).click()
  return page.getByRole('region', exact('组合周报'))
}

async function write(page, control, suffix) {
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix))
  await control.click()
  const sent=await response
  expect(sent.request().headers()['idempotency-key']).toBeTruthy()
  expect(sent.request().headers()['if-match']).toMatch(/^[1-9]/)
  expect(sent.status(),await sent.text()).toBe(200)
  return (await sent.json()).data
}

try {
  const pm=await signedIn('pm',1320)
  const desk=await portfolio(pm)
  await expect(desk.getByText(/个授权项目/)).toBeVisible()
  await desk.getByLabel('周起始日（周一）').fill('2024-12-30')
  await desk.getByRole('combobox',exact('筛选项目')).click()
  await pm.getByRole('option',{name:/REVIEWS · Synthetic review/}).click()
  const publishedRow=desk.getByRole('row',{name:/Synthetic review REVIEWS 正式发布/})
  await expect(publishedRow.getByText('正式发布',exact('正式发布'))).toBeVisible()
  await expect(publishedRow.getByText('4.00',exact('4.00'))).toBeVisible()
  await desk.getByLabel('周起始日（周一）').blur()
  await pm.screenshot({animations:'disabled',path:join(process.env.PMO_TEST_SCREENSHOTS,'weekly-portfolio-1320.png')})
  await button(desk,'查看周报').click()
  const detail=pm.getByRole('article',exact('报告详情'))
  await expect(detail.getByRole('heading',{name:/2025-W01 · v1 · 已发布/})).toBeVisible()

  const candidate=detail.getByRole('region',exact('周报凭证候选'))
  await button(candidate,'提报待核验凭证').click()
  const postingDialog=pm.getByRole('dialog',exact('提报周报凭证'))
  await postingDialog.getByLabel('凭证归属月份（UTC）').selectOption('2025-01')
  const posting=await write(pm,button(postingDialog,'确认提报'),'/effort-posting')
  expect(posting.status).toBe('pending')
  expect(posting.amountSigned).toBe('4.00')
  await expect(candidate.getByText(/已登记候选/)).toBeVisible()

  const review=detail.getByRole('region',exact('逐报告跨周 Fact 对照'))
  await button(review,'逐报告跨周对照').click()
  await expect(review.getByText('与上周相比有变化',{exact:true})).toBeVisible()
  await pm.screenshot({animations:'disabled',path:join(process.env.PMO_TEST_SCREENSHOTS,'weekly-fact-comparison-1320.png')})
  await button(review,'转入澄清').click()
  const clarification=pm.getByRole('dialog',exact('转入 Fact 澄清'))
  await clarification.getByLabel('澄清原因').fill('两周工时口径需要人工核对')
  await expect(button(clarification,'确认转办')).toBeDisabled()
  await clarification.getByRole('checkbox',exact('确认将本周事实转入人工澄清')).check()
  const fact=await write(pm,button(clarification,'确认转办'),'/weekly-clarification')
  expect(fact.validationStatus).toBe('disputed')
  await expect(review.getByText('已转入 Fact 澄清，并生成项目负责人待办。',{exact:true})).toBeVisible()
  await expect(button(detail,'澄清')).toBeVisible()

  const reviewer=await signedIn('reviewer',440)
  const mobile=await portfolio(reviewer)
  await expect(mobile.getByText(/1 个授权项目/)).toBeVisible()
  await mobile.getByLabel('周起始日（周一）').fill('2024-12-30')
  await expect(mobile.getByRole('listitem').getByText('正式发布',{exact:true})).toBeVisible()
  await mobile.getByLabel('周起始日（周一）').blur()
  await reviewer.screenshot({animations:'disabled',path:join(process.env.PMO_TEST_SCREENSHOTS,'weekly-portfolio-440.png')})
  await button(mobile,'查看周报').click()
  await expect(reviewer.getByRole('article',exact('报告详情')).getByRole('heading',{name:/2025-W01 · v1 · 已发布/})).toBeVisible()
  await showNavigation(reviewer)
  await reviewer.getByRole('tab',exact('成本')).click()
  const costs=reviewer.getByRole('region',exact('成本凭证'))
  await button(costs,'核验凭证').click()
  await expect(costs.getByText(/来源周报覆盖 2024-12-30 至 2025-01-05/)).toBeVisible()
  await costs.getByLabel('实际发生时间（UTC）').fill('2025-01-02T10:00')
  await costs.getByLabel('核验说明').fill('已核验合成人员工时记录')
  expect((await write(reviewer,button(costs,'确认核验'),'/verify')).status).toBe('verified')
  await expect(costs.getByText('已核验',{exact:true})).toBeVisible()
  await reviewer.screenshot({animations:'disabled',path:join(process.env.PMO_TEST_SCREENSHOTS,'weekly-effort-verified-440.png')})

  const outsider=await signedIn('outsider',440)
  const visibleProject=await portfolio(outsider)
  await expect(visibleProject.getByText(/1 个授权项目/)).toBeVisible()
  await visibleProject.getByLabel('周起始日（周一）').fill('2024-12-30')
  await expect(visibleProject.getByText('2024-12-30 至 2025-01-05',{exact:true})).toBeVisible()
  await expect(visibleProject.getByRole('listitem').getByText('未见可读的正式周报',{exact:true})).toBeVisible()
  await expect(button(visibleProject,'查看周报')).toHaveCount(0)
  await visibleProject.getByLabel('周起始日（周一）').blur()
  await outsider.screenshot({animations:'disabled',path:join(process.env.PMO_TEST_SCREENSHOTS,'weekly-portfolio-read-scope-440.png')})

  const initiator=await signedIn('initiator',440)
  const unaffiliated=await portfolio(initiator)
  await expect(unaffiliated.locator('.mobile-portfolio').getByText('该范围内没有授权项目',{exact:true})).toBeVisible()
  await expect(unaffiliated.getByText('Synthetic review',{exact:true})).toHaveCount(0)
  expect(pageErrors).toEqual([])
} finally {
  await browser.close()
}
