import {chromium,expect} from '@playwright/test'
import path from 'node:path'

const url=new URL(process.env.PMO_BROWSER_URL)
const sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const fixture=JSON.parse(process.env.PMO_TEST_REASSESSMENT)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})

async function open(role){
  const context=await browser.newContext({viewport:{width:1440,height:920},locale:'zh-CN'})
  const session=sessions[role]
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:url.hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(token=>sessionStorage.setItem('pmo.csrf',token),session.csrf)
  const page=await context.newPage()
  await page.goto(url.href)
  await button(page,'查看 '+fixture.projectName+' 的项目资料').click()
  await page.getByRole('tab',{name:'补强任务',exact:true}).click()
  const task=page.getByRole('row').filter({hasText:'复评继续投入（金额）'})
  await button(task,'查看重估').click()
  const detail=page.locator('.reassessment').filter({hasText:'金额超支重估'})
  return {page,detail}
}

async function save(page,detail,label,route){
  const [response]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(route)),
    button(detail,label).click()])
  expect(response.status()).toBe(200)
  expect(response.request().headers()['if-match']).toBeTruthy()
  expect(response.request().headers()['idempotency-key']).toBeTruthy()
  return (await response.json()).data
}

try{
  const pm=await open('pm')
  await expect(pm.detail.getByText('21.00%')).toBeVisible()
  await button(pm.detail,'填写重估').click()
  await pm.detail.getByLabel('原目标与当前情况').fill('原目标维持，新增成本依据已由独立人核验。')
  await pm.detail.getByLabel('差异归因').selectOption('execution')
  await pm.detail.getByLabel('更新建议').fill('维持交付范围，按周复核剩余投入。')
  await pm.detail.getByLabel('已核验附件引用').fill(fixture.evidenceRef)
  const submitted=await save(pm.page,pm.detail,'提交重估','/cost-reassessment')
  expect(submitted.reassessment.state).toBe('decision_required')
  await expect(button(pm.detail,'记录投入决议')).toHaveCount(0)

  const sponsor=await open('sponsor')
  await button(sponsor.detail,'记录投入决议').click()
  await sponsor.detail.getByLabel('裁决理由').fill('保持已批准预算和原范围，继续核心交付。')
  await sponsor.detail.getByLabel('适用条件').fill('下期复核偏差与预测。')
  if(process.env.PMO_R5_SCREENSHOTS){
    await sponsor.page.screenshot({path:path.join(process.env.PMO_R5_SCREENSHOTS,'r5-03-cost-reassessment.png'),fullPage:true})
  }
  const decided=await save(sponsor.page,sponsor.detail,'确认决议','/cost-reassessment/decision')
  expect(decided.status).toBe('done')
  expect(decided.reassessment.history[0].decision.option).toBe('continue')
  await expect(sponsor.detail.locator('summary').first()).toContainText('已决议')
  await expect(sponsor.detail.getByText('保持已批准预算和原范围，继续核心交付。')).toBeVisible()
}finally{await browser.close()}
