import {chromium, expect} from '@playwright/test'

const url=process.env.PMO_BROWSER_URL
const sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const packet=JSON.parse(process.env.PMO_TEST_FORMAL_CHANGES)
const path=`/api/v1/projects/${packet.project.projectId}/changes`
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})

async function open(role){
  const context=await browser.newContext({viewport:{width:1440,height:900}})
  const {cookie,csrf}=sessions[role]
  await context.addCookies([{name:'pmo_session',value:cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(token=>sessionStorage.setItem('pmo.csrf',token),csrf)
  const page=await context.newPage()
  await page.goto(url)
  await button(page,`查看 ${packet.project.name} 的项目资料`).click()
  await page.getByRole('tab',{name:'变更台账',exact:true}).click()
  await expect(page.getByText(/^变更记录 · /)).toBeVisible()
  return page
}

async function begin(page,kind){
  await button(page,'申请正式变更').click()
  const dialog=page.getByRole('dialog',{name:'申请正式变更',exact:true})
  await dialog.getByRole('combobox',{name:'变更资料类型',exact:true}).selectOption(kind)
  await dialog.getByRole('combobox',{name:'变更目标',exact:true}).selectOption(packet.targets[kind].targetId)
  await dialog.getByLabel('变更原因',{exact:true}).fill(`独立核对当前基线后申请调整 ${kind}`)
  return dialog
}

async function submit(page,action){
  const response=page.waitForResponse(item=>new URL(item.url()).pathname===path&&item.request().method()==='POST')
  await action.click()
  return response
}

try{
  const reviewer=await open('reviewer')
  await expect(button(reviewer,'申请正式变更')).toHaveCount(0)
  const pm=await open('pm')
  const milestone=packet.targets.milestone
  const plan=await begin(pm,'milestone')
  const forecast=await pm.request.patch(`${url}/api/v1/milestones/${milestone.targetId}/forecast`,{
    headers:{'X-CSRF-Token':sessions.pm.csrf,'If-Match':String(milestone.revision),'Idempotency-Key':crypto.randomUUID()},
    data:{newDate:packet.forecastDate,reason:'独立供应商提供了另一预测日期。'},
  })
  expect(forecast.status(),await forecast.text()).toBe(200)
  await plan.getByLabel('计划日期',{exact:true}).fill(packet.plannedDate)
  const stale=await submit(pm,button(plan,'提交正式变更申请'))
  expect(stale.status()).toBe(409)
  await expect(plan.getByText(/基线或资料状态已变化/)).toBeVisible()
  await button(plan,'重新读取当前资料').click()
  await expect(plan.getByLabel('计划日期',{exact:true})).toHaveValue(milestone.plannedDate)
  await plan.getByLabel('计划日期',{exact:true}).fill(packet.plannedDate)
  const revised=await submit(pm,button(plan,'提交正式变更申请'))
  expect(revised.status(),await revised.text()).toBe(200)
  expect(revised.request().headers()['if-match']).toBe(String(milestone.revision+1))
  await expect(plan).toBeHidden()

  const cost=await begin(pm,'budget')
  await cost.getByRole('textbox',{name:/拟变更预算/}).fill('137.5')
  let original
  const sameEndpoint=address=>address.pathname===path
  await pm.route(sameEndpoint,async route=>{
    if(route.request().method()!=='POST'||original){await route.continue();return}
    original=route.request()
    const saved=await route.fetch()
    expect(saved.status(),await saved.text()).toBe(200)
    await route.abort('failed')
  })
  await button(cost,'提交正式变更申请').click()
  await expect(button(cost,'重试上次提交')).toBeVisible()
  await expect(button(cost,'取消')).toBeDisabled()
  const replay=await submit(pm,button(cost,'重试上次提交'))
  expect(replay.status(),await replay.text()).toBe(200)
  expect(replay.request().postData()).toBe(original.postData())
  for(const name of ['if-match','idempotency-key'])expect(replay.request().headers()[name]).toBe(original.headers()[name])
  await expect(cost).toBeHidden()
  await pm.unroute(sameEndpoint)

  const charter=await begin(pm,'charter')
  await charter.getByLabel('预计工期（天）',{exact:true}).fill(String(packet.targets.charter.charter.roughDurationDays+1))
  const saved=await submit(pm,button(charter,'提交正式变更申请'))
  expect(saved.status(),await saved.text()).toBe(200)
  expect(saved.request().postDataJSON().after).toEqual({roughDuration:packet.targets.charter.charter.roughDurationDays+1})
  await expect(charter).toBeHidden()
  await expect(pm.getByRole('cell',{name:'待审批',exact:true})).toHaveCount(3)
}finally{await browser.close()}
