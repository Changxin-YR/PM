import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL, sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const exact=name=>({name,exact:true})
const button=(scope,name)=>scope.getByRole('button',exact(name))

async function actor(role,width){
  const context=await browser.newContext({viewport:{width,height:840}})
  await context.addCookies([{name:'pmo_session',value:sessions[role].cookie,domain:new URL(url).hostname,
    path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),sessions[role].csrf)
  const page=await context.newPage()
  await page.goto(url)
  return page
}

async function open(page){
  await button(page,'查看 Synthetic review 的项目资料').click()
  const project=page.getByRole('region',exact('Synthetic review · 项目资料'))
  await showNavigation(page)
  await project.getByRole('tab',exact('需求')).click()
  const row=project.getByRole('row').filter({hasText:'Export results'})
  await row.locator('.el-table__expand-icon').click()
  await button(project,'客户争议').click()
  return page.getByRole('dialog',exact('客户争议'))
}

async function submit(page,form,label,suffix){
  const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix))
  await button(form,label).click()
  const result=await waiting
  expect(result.request().headers()['if-match']).toMatch(/^[1-9]\d*$/)
  expect(result.request().headers()['idempotency-key']).toBeTruthy()
  expect(result.status(),await result.text()).toBe(200)
  return (await result.json()).data
}

try {
  const note='客户复核发现结果导出缺少校验列', retraction='已重新核对导出原件，撤回本条争议'
  const customer=await actor('client_representative',430), report=await open(customer)
  await expect(report.getByText('没有正式客户争议')).toBeVisible()
  await button(report,'提出正式争议').click()
  await report.getByLabel('争议理由').fill(note)
  const receipt=await submit(customer,report,'提交争议','/disputes')
  await expect(report.getByText(note,exact(note))).toBeVisible()
  await expect(report.getByText('未解决',exact('未解决'))).toBeVisible()

  const pm=await actor('pm',1240), view=await open(pm)
  await expect(view.getByText(note,exact(note))).toBeVisible()
  await expect(button(view,'撤回此争议')).toHaveCount(0)
  await expect(button(view,'提出正式争议')).toHaveCount(0)

  await pm.reload()
  await button(pm,'查看 Synthetic review 的项目资料').click()
  await pm.getByRole('tab',exact('三线健康')).click()
  await button(pm,'记录本次快照').click()
  await expect(pm.getByText('已记录本次计算。',{exact:true})).toBeVisible()
  await pm.reload()
  await button(pm,'预警中心').click()
  await pm.getByRole('tab',exact('每周三线')).click()
  const weekly=pm.getByRole('region',exact('每周三线'))
  await weekly.getByText('周内最近采样 · 原因和出处',{exact:true}).click()
  const cause=weekly.locator('ul.causes > li').filter({hasText:'有效客户争议'})
  await expect(cause.getByText(`requirement-dispute:${receipt.disputeId}@1`,{exact:true})).toBeVisible()

  await customer.reload()
  const history=await open(customer)
  await button(history,'撤回此争议').click()
  await history.getByLabel('撤回说明').fill(retraction)
  const withdrawn=await submit(customer,history,'确认撤回',`/disputes/${receipt.disputeId}/withdraw`)
  expect(withdrawn).toMatchObject({revision:2,reason:note,withdrawalReason:retraction})
  await pm.reload()
  const updated=await open(pm)
  await expect(updated.getByText('已撤回',exact('已撤回'))).toBeVisible()
  await expect(updated.getByText(retraction)).toBeVisible()
  await expect(updated.getByText(note,exact(note))).toBeVisible()
} finally {
  await browser.close()
}
