import {chromium, expect} from '@playwright/test'
import {returnToLedger, showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL, sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const exact=name=>({name,exact:true})
const button=(root,name)=>root.getByRole('button',exact(name))

async function project(page, tab){
  await button(page,'查看 Synthetic review 的项目资料').click()
  const dialog=page.getByRole('region',exact('Synthetic review · 项目资料'))
  await dialog.getByRole('tab',exact(tab)).click()
  return dialog
}

async function actor(role,width){
  const context=await browser.newContext({viewport:{width,height:840}})
  await context.addCookies([{name:'pmo_session',value:sessions[role].cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),sessions[role].csrf)
  const page=await context.newPage();await page.goto(url)
  return page
}

async function post(page, action, suffix){
  const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix))
  await action.click()
  const res=await waiting
  expect(res.request().headers()['if-match']).toMatch(/^[1-9]/)
  expect(res.request().headers()['idempotency-key']).toBeTruthy()
  expect(res.status(),await res.text()).toBe(200)
  return (await res.json()).data
}

try {
  const gap='补充一份核验流水追踪表'
  const reason='客户已提出新增交付材料要求'
  const pm=await actor('pm',1280), drawer=await project(pm,'变更台账')
  await button(drawer,'申请新增需求').click()
  const form=drawer.getByRole('form',exact('新增需求申请'))
  await form.getByLabel('需求说明').fill(gap)
  await form.getByLabel('优先级').selectOption('P1')
  await form.getByLabel('计划确认日期').fill('2026-12-09')
  await form.getByLabel('验收标准').fill('追踪表中的每条流水均可查到来源')
  await form.getByLabel('新增原因').fill(reason)
  const request=await post(pm,button(form,'送审新增需求'),'/changes/requirement-additions')
  expect(request.status).toBe('pending')
  await drawer.getByRole('tab',exact('需求')).click()
  await expect(drawer.getByText('Export results',{exact:true})).toBeVisible()
  await expect(drawer.getByText(gap,{exact:true})).toHaveCount(0)

  const reviewer=await actor('reviewer',504)
  await showNavigation(reviewer)
  await button(reviewer,'我的审批收件箱').click()
  const inbox=reviewer.getByRole('region',exact('我的审批收件箱'))
  const pending=inbox.getByRole('listitem').filter({hasText:'需求变更'})
  await expect(pending.getByText('可批准 / 可拒绝',{exact:false})).toBeVisible()
  const opened=reviewer.waitForResponse(r=>{
    const u=new URL(r.url())
    return u.pathname.endsWith('/changes')&&u.searchParams.get('changeId')===request.changeId
  })
  await button(pending,'打开记录').click()
  expect((await opened).status()).toBe(200)
  const review=reviewer.getByRole('region',exact('Synthetic review · 项目资料'))
  await expect(button(review,'申请新增需求')).toHaveCount(0)
  await button(review.getByRole('row').filter({hasText:reason}),'审核变更').click()
  const decision=reviewer.getByRole('dialog',exact('审核变更'))
  await decision.getByLabel('裁决',{exact:true}).selectOption('approve')
  await decision.getByLabel('裁决理由').fill('核对范围与验收标准后批准。')
  expect((await post(reviewer,button(decision,'确认裁决'),`/changes/${request.changeId}/approve`)).status).toBe('applied')
  await expect(review.getByRole('row').filter({hasText:reason}).getByText('已生效',{exact:true})).toBeVisible()
  await returnToLedger(reviewer)
  await showNavigation(reviewer)
  await button(reviewer,'我的审批收件箱').click()
  await expect(inbox.getByText('可处理 · 0')).toBeVisible()
  await pm.reload()
  const restored=await project(pm,'需求')
  await expect(restored.getByText(gap,{exact:true})).toBeVisible()
  await restored.getByRole('tab',exact('变更台账')).click()
  await expect(restored.getByRole('row').filter({hasText:reason}).getByText('已生效',{exact:true})).toBeVisible()
} finally {
  await browser.close()
}
