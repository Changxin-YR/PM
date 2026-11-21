import {chromium, expect} from '@playwright/test'

const url=process.env.PMO_BROWSER_URL
const sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})

async function enter(page){
  await button(page,'查看 Synthetic review 的项目资料').click()
  const drawer=page.getByRole('dialog',{name:'Synthetic review · 项目资料',exact:true})
  await drawer.getByRole('tab',{name:'日报周报',exact:true}).click()
  return drawer.getByRole('region',{name:'日报与周报',exact:true})
}

async function write(page, action, suffix, method='POST'){
  const waiting=page.waitForResponse(r=>r.request().method()===method&&new URL(r.url()).pathname.endsWith(suffix))
  await action.click()
  const res=await waiting
  expect(res.request().headers()['if-match']).toMatch(/^[1-9]/)
  expect(res.request().headers()['idempotency-key']).toBeTruthy()
  expect(res.status(),await res.text()).toBe(200)
  return (await res.json()).data
}

async function session(role, viewport){
  const context=await browser.newContext({viewport})
  await context.addCookies([{name:'pmo_session',value:sessions[role].cookie,domain:new URL(url).hostname,
    path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),sessions[role].csrf)
  const page=await context.newPage()
  await page.goto(url)
  return page
}

try {
  const pm=await session('pm',{width:1320,height:870})
  const area=await enter(pm)
  await button(area,'生成周报初稿').click()
  const form=area.getByRole('form',{name:'生成周报',exact:true})
  await form.getByLabel('周内日期').fill('2024-12-30')
  const draft=await write(pm,button(form,'生成草稿'),'/reports/weekly-drafts')
  expect(draft.period).toBe('2025-W01')
  expect(draft.status).toBe('draft')
  expect(draft.isCurrentConfirmed).toBe(false)
  const detail=area.getByRole('article',{name:'报告详情',exact:true})
  await expect(detail.getByText('无核验流水',{exact:true})).toBeVisible()
  await expect(detail.getByRole('heading',{name:'需求进展',exact:true})).toBeVisible()
  await expect(detail.getByRole('heading',{name:'风险与阻塞',exact:true})).toBeVisible()
  await button(detail,'编辑草稿').click()
  const editor=area.getByRole('form',{name:'报告草稿',exact:true})
  await editor.getByLabel('申报人天').fill('3.25')
  await editor.getByLabel('补充说明').fill('PM 复核：合成周报已更新。')
  const edited=await write(pm,button(editor,'保存草稿'),`/reports/${draft.reportId}`,'PATCH')
  expect(edited.weeklyBasis.sourceHash).toBe(draft.weeklyBasis.sourceHash)
  expect((await write(pm,button(detail,'提交报告'),`/reports/${draft.reportId}/submit`)).status).toBe('submitted')
  await button(detail,'确认并发布').click()
  const published=await write(pm,button(pm,'发布报告'),`/reports/${draft.reportId}/confirm`)
  expect(published.isCurrentConfirmed).toBe(true)
  expect(published.weeklyBasis).toEqual(draft.weeklyBasis)

  const reviewer=await session('reviewer',{width:448,height:800})
  const readonly=await enter(reviewer)
  await button(readonly.getByRole('row').filter({hasText:'2025-W01'}),'查看').click()
  const recovered=readonly.getByRole('article',{name:'报告详情',exact:true})
  await expect(recovered.getByText('PM 复核：合成周报已更新。',{exact:true})).toBeVisible()
  await expect(button(readonly,'生成周报初稿')).toHaveCount(0)
  await expect(button(recovered,'确认并发布')).toHaveCount(0)
  await pm.reload()
  const reloaded=await enter(pm)
  await button(reloaded.getByRole('row').filter({hasText:'2025-W01'}),'查看').click()
  const restored=reloaded.getByRole('article',{name:'报告详情',exact:true})
  await expect(restored.getByRole('heading',{name:/2025-W01 · v1 · 已发布/})).toBeVisible()
  await restored.locator('summary').filter({hasText:'生成依据'}).click()
  await expect(restored.getByText(draft.weeklyBasis.sourceHash,{exact:true})).toBeVisible()
} finally {
  await browser.close()
}
