import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL
const sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})

async function actor(role,width){
  const context=await browser.newContext({viewport:{width,height:820}})
  const session=sessions[role]
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),session.csrf)
  const page=await context.newPage();await page.goto(url)
  return page
}

async function reports(page){
  await button(page,'查看 Synthetic review 的项目资料').click()
  const drawer=page.getByRole('region',{name:'Synthetic review · 项目资料',exact:true})
  await showNavigation(page)
  await drawer.getByRole('tab',{name:'日报周报',exact:true}).click()
  return drawer.getByRole('region',{name:'日报与周报',exact:true})
}

async function facts(reporting,week){
  await button(reporting.getByRole('row').filter({hasText:week}),'查看').click()
  const area=reporting.getByRole('region',{name:'Fact 证据与澄清',exact:true})
  await button(area,'Fact 证据与澄清').click()
  return area
}

async function save(page,action,suffix){
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix))
  await action.click()
  const sent=await response
  expect(sent.request().headers()['idempotency-key']).toBeTruthy()
  expect(sent.request().headers()['if-match']).toMatch(/^[1-9]/)
  expect(sent.status(),await sent.text()).toBe(200)
  return (await sent.json()).data
}

async function propose(page,area,value){
  await button(area,'提报候选').click()
  const form=area.getByRole('form',{name:'Fact 候选'})
  await form.getByLabel('主题').fill('合成交付')
  await form.getByLabel('属性').fill('申报人天')
  await form.getByLabel('业务时点（上海）').fill('2024-12-30T08:00')
  await form.getByLabel('值',{exact:true}).fill(value)
  await form.getByLabel('原话').fill(value)
  return save(page,button(form,'保存候选'),'/facts/proposals')
}

async function verify(page,area){
  await button(area,'核验').click()
  const form=area.getByRole('form',{name:'Fact 核验'})
  return save(page,button(form,'记录核验'),'/verify')
}

try{
  const pm=await actor('pm',440)
  const pmReports=await reports(pm)
  const first=await facts(pmReports,'2025-W01')
  expect((await propose(pm,first,'2.50')).validationStatus).toBe('candidate')
  const reviewer=await actor('reviewer',1240)
  const reviewerReports=await reports(reviewer)
  expect((await verify(reviewer,await facts(reviewerReports,'2025-W01'))).validationStatus).toBe('verified')
  const conflicting=await facts(pmReports,'2025-W02')
  expect((await propose(pm,conflicting,'3.80')).validationStatus).toBe('disputed')
  const secondReview=await facts(reviewerReports,'2025-W02')
  expect((await verify(reviewer,secondReview)).validationStatus).toBe('disputed')
  await expect(button(secondReview,'澄清')).toHaveCount(0)
  await button(conflicting,'刷新 Fact').click()
  await button(conflicting,'澄清').click()
  const form=conflicting.getByRole('form',{name:'Fact 澄清'})
  const reason='与本期独立原始表单核对后，采用后续回执。'
  await form.getByLabel('说明').fill(reason)
  expect((await save(pm,button(form,'确认澄清'),'/clarify')).validationStatus).toBe('verified')
  await pm.reload()
  const restored=await facts(await reports(pm),'2025-W02')
  await expect(restored.getByText('已核验 · 中可信',{exact:true})).toBeVisible()
  await expect(restored.getByText(reason,{exact:true})).toBeVisible()
  await button(restored,'核验历史').click()
  await expect(restored.getByText(/r3 · 完成澄清/)).toBeVisible()
}finally{await browser.close()}
