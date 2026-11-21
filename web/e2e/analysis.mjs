import {chromium, expect} from '@playwright/test'

const url=process.env.PMO_BROWSER_URL
const session=JSON.parse(process.env.PMO_TEST_SESSIONS).pm
const model=process.env.PMO_TEST_MODEL==='1'
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})

async function openReport(page){
  await button(page,'查看 Synthetic review 的项目资料').click()
  const drawer=page.getByRole('dialog',{name:'Synthetic review · 项目资料',exact:true})
  await drawer.getByRole('tab',{name:'日报周报',exact:true}).click()
  const reports=drawer.getByRole('region',{name:'日报与周报',exact:true})
  await button(reports.getByRole('row').filter({hasText:'2025-W01'}),'查看').click()
  const facts=reports.getByRole('region',{name:'Fact 证据与澄清',exact:true})
  await button(facts,'Fact 证据与澄清').click()
  return {facts, analysis:facts.getByRole('region',{name:'报告分析',exact:true})}
}

async function submit(page,action,suffix){
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix))
  await action.click()
  const sent=await response
  expect(sent.request().headers()['idempotency-key']).toBeTruthy()
  expect(sent.request().headers()['if-match']).toBe('1')
  expect(sent.status(),await sent.text()).toBe(200)
  return (await sent.json()).data
}

try{
  const context=await browser.newContext({viewport:{width:model?1240:440,height:820}})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),session.csrf)
  const page=await context.newPage();await page.goto(url)
  const initial=await openReport(page)
  const run=await submit(page,button(initial.analysis,model?'开始分析':'转人工处理'),'/analyze')
  expect(run.status).toBe(model?'queued':'manual')
  const outcome=model?'建议已就绪':'待人工处理'
  await expect(initial.analysis.getByText(outcome,{exact:true})).toBeVisible({timeout:15000})
  await expect(initial.facts.getByText('暂无候选事实',{exact:true})).toBeVisible()
  await page.reload()
  const restored=await openReport(page)
  await expect(restored.analysis.getByText(outcome,{exact:true})).toBeVisible()
  await expect(restored.facts.getByText('暂无候选事实',{exact:true})).toBeVisible()
  if(model){
    await expect(restored.analysis.getByText('Work took 2.50 days.',{exact:true})).toBeVisible()
    await expect(restored.analysis.getByText(/\/narrative:0/)).toBeVisible()
    await button(restored.analysis,'选用为候选').click()
    const form=restored.facts.getByRole('form',{name:'Fact 候选'})
    await expect(form.getByLabel('值',{exact:true})).toHaveValue('2.50')
    const candidate=await submit(page,button(form,'保存候选'),'/facts/proposals')
    expect(candidate.validationStatus).toBe('candidate')
    expect(candidate.trust).toBe('low')
    expect(candidate.quote).toBe('Work took 2.50 days.')
  }else{
    await expect(restored.analysis.getByText('模型已关闭 · 已关联人工任务',{exact:true})).toBeVisible()
    await expect(button(restored.facts,'提报候选')).toBeVisible()
  }
}finally{await browser.close()}
