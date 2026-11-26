import {chromium, expect} from '@playwright/test'
import path from 'node:path'

const origin = process.env.PMO_BROWSER_URL
const scenario = process.env.PMO_R5_SCENARIO
const packet = JSON.parse(process.env.PMO_R5_DATA)
const role = scenario === 'fast' ? 'initiator' : 'reviewer'
const session = JSON.parse(process.env.PMO_TEST_SESSIONS)[role]
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : 'chromium')})
const exact = name => ({name, exact:true})
const button = (scope, name) => scope.getByRole('button', exact(name))

try {
  const context = await browser.newContext({viewport:{width:1440,height:1000}})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(origin).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),session.csrf)
  const page = await context.newPage()
  await page.goto(origin)
  const projectName = 'Synthetic review'
  await button(page, '查看 '+projectName+' 的项目资料').click()
  const drawer = page.getByRole('region', exact(projectName+' · 项目资料'))
  if (scenario === 'fast') {
    await drawer.getByRole('tab',exact('关口送审')).click()
    const row = drawer.getByRole('row').filter({has:page.getByRole('cell', exact(packet.reviewId))})
    await button(row,'打开评审工作台').click()
    const workbench = page.getByRole('region',exact('关口人工工作台'))
    await expect(workbench.getByText('总分：4.20',{exact:true})).toBeVisible()
    await expect(workbench.getByText('本项目使用隔离演练的快速立项规则。正式规则待签核。',{exact:true})).toBeVisible()
    await button(workbench,'确认快速立项并备案').click()
    await workbench.getByLabel('确认与备案说明',{exact:true}).fill('核对冻结材料和独立评分后，由发起人确认并登记评审会备案。')
    await page.evaluate(()=>window.scrollTo(0,0))
    await page.screenshot({path:path.join(process.env.PMO_R5_REVIEW_SCREENSHOTS,'05-快速立项.png'),fullPage:true})
    const confirmation = page.waitForResponse(response=>response.request().method()==='POST'&&
      new URL(response.url()).pathname.endsWith(`/reviews/${packet.reviewId}/confirmations`))
    await button(workbench,'确认并备案').click()
    const response = await confirmation
    expect(response.status()).toBe(200)
    expect((await response.json()).data.decision.stage).toBe('designing')
    await expect(workbench.getByText('快速立项已备案，项目已进入方案细化。',{exact:true})).toBeVisible()
    await workbench.getByText('快速立项备案',{exact:true}).click()
    await expect(workbench.getByText('pmo-lite-charter-fast-r5-v1',{exact:true})).toBeVisible()
    await page.evaluate(()=>window.scrollTo(0,0))
    await page.screenshot({path:path.join(process.env.PMO_R5_REVIEW_SCREENSHOTS,'05-快速立项已备案.png'),fullPage:true})
  } else {
    await drawer.getByRole('tab',exact('复盘与教训')).click()
    const section = drawer.getByRole('region',exact('立项评分结果校准'))
    const table = section.getByRole('table', exact('立项评分与实际结果对照'))
    const row = table.getByRole('row').filter({has:page.getByRole('rowheader',exact('战略对齐'))})
    for (const value of ['4.00', '3', '-1.00', '需在复盘解释']) await expect(row.getByRole('cell', exact(value))).toBeVisible()
    await expect(table.getByRole('row').filter({has:page.getByRole('rowheader', exact('客户/市场吸引力'))}))
      .toContainText('证据不足或不可比')
    const strategy = page.locator('summary').filter({hasText:'战略对齐 · 评价与证据'})
    const strategyDetails = section.locator('.calibration-dimension').filter({has:strategy})
    await strategyDetails.locator('summary').filter({hasText:'战略对齐 · 评价与证据'}).click()
    await strategyDetails.getByText('原始评分与证据',{exact:true}).click()
    await strategyDetails.getByText('版本 2 · 已评价 · 3',{exact:true}).click()
    await expect(strategyDetails.getByText(packet.closingDecisionRef,{exact:true})).toBeVisible()
    await page.evaluate(()=>window.scrollTo(0,0))
    await page.screenshot({path:path.join(process.env.PMO_R5_REVIEW_SCREENSHOTS,'06-评分结果校准.png'),fullPage:true})
  }
  console.log('R5 实际浏览器评审验收通过: '+scenario)
} finally{
  await browser.close()
}
