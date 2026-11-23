import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL
const session=JSON.parse(process.env.PMO_TEST_SESSIONS).pm
const model=process.env.PMO_TEST_MODEL==='1'
const archived=process.env.PMO_TEST_ARCHIVED_HISTORY==='1'
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})

async function openReport(page){
  await button(page,'查看 Synthetic review 的项目资料').click()
  const drawer=page.getByRole('region',{name:'Synthetic review · 项目资料',exact:true})
  await showNavigation(page)
  await drawer.getByRole('tab',{name:'日报周报',exact:true}).click()
  const reports=drawer.getByRole('region',{name:'日报与周报',exact:true})
  const versions=reports.getByRole('row').filter({hasText:'2025-W01'})
  if(archived)await expect(versions.getByText('已发布',{exact:true})).toHaveCount(1)
  await button(archived?versions.filter({has:page.getByText('已确认',{exact:true})}):versions,'查看').click()
  if(archived)await expect(reports.getByRole('article',{name:'报告详情'}).getByText('Synthetic historical publication',{exact:true})).toBeVisible()
  const facts=reports.getByRole('region',{name:'Fact 证据与澄清',exact:true})
  await button(facts,'Fact 证据与澄清').click()
  return {drawer, facts, analysis:facts.getByRole('region',{name:'报告分析',exact:true})}
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
  if(archived){
    const rows=initial.analysis.getByRole('article')
    await expect(rows).toHaveCount(3)
    await expect(button(initial.analysis,'转人工处理')).toHaveCount(0)
    await expect(button(initial.analysis,'选用为候选')).toHaveCount(0)
    await expect(button(initial.facts,'提报候选')).toHaveCount(0)
    await button(initial.analysis,'下一页').click()
    await expect(rows).toHaveCount(1)
    await button(initial.analysis,'刷新报告分析').click()
    await expect(rows).toHaveCount(1)
    await page.reload()
    const restored=await openReport(page)
    await expect(restored.analysis.getByText('模型已关闭 · 已关联人工任务',{exact:true})).toHaveCount(3)
  } else {
    const run=await submit(page,button(initial.analysis,model?'开始分析':'转人工处理'),'/analyze')
    expect(run.status).toBe(model?'queued':'manual')
    const outcome=model?'建议已就绪':'待人工处理'
    await expect(initial.analysis.getByText(outcome,{exact:true})).toBeVisible({timeout:15000})
    await expect(initial.facts.getByText('暂无候选事实',{exact:true})).toBeVisible()
    await page.reload()
    const restored=await openReport(page)
    await expect(restored.analysis.getByText(outcome,{exact:true})).toBeVisible()
    await expect(restored.facts.getByText('暂无候选事实',{exact:true})).toBeVisible()
    await showNavigation(page)
    await restored.drawer.getByRole('tab',{name:'项目助理',exact:true}).click()
    const assistant=restored.drawer.getByRole('region',{name:'项目助理',exact:true})
    await assistant.getByText('过程管家',{exact:true}).click()
    await expect(assistant.getByText(model?'候选已生成':'待人工处理',{exact:true})).toBeVisible()
    if(model)await expect(assistant.getByText(/Work took 2\.50 days\./)).toBeVisible()
    else await expect(assistant.getByRole('heading',{name:'待核验候选',exact:true})).toHaveCount(0)
    await showNavigation(page)
    await restored.drawer.getByRole('tab',{name:'日报周报',exact:true}).click()
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
  }
}finally{await browser.close()}
