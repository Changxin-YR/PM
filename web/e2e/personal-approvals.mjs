import {chromium, expect} from '@playwright/test'

const url = new URL(process.env.PMO_BROWSER_URL)
const sessions = JSON.parse(process.env.PMO_TEST_SESSIONS)
const {reviewId,transition}=JSON.parse(process.env.PMO_TEST_APPROVALS)
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform === 'win32'?'msedge':'chromium')})
const exact = name => ({name,exact:true})
const button = (scope,name) => scope.getByRole('button',exact(name))
const dialog = (page,name) => page.getByRole('dialog',exact(name))
const inbox = page=>dialog(page,'我的审批收件箱')
const empty='暂无可处理的关口评审或阶段审批。'
const openInbox=page=>button(page,'我的审批收件箱').click()
async function actor(role,width){
  const context=await browser.newContext({viewport:{width,height:850}})
  const {cookie,csrf}=sessions[role]
  await context.addCookies([{name:'pmo_session',value:cookie,domain:url.hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(token => sessionStorage.setItem('pmo.csrf',token),csrf)
  const page=await context.newPage();await page.goto(url.href)
  return page
}
async function save(page,path,control){
  const [response]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===path),control.click()])
  const body=await response.json()
  expect(response.status(),JSON.stringify(body)).toBe(200)
  expect(response.request().headers()['if-match']).toBeTruthy()
  expect(response.request().headers()['idempotency-key']).toBeTruthy()
  return body.data
}

try{
  const pm=await actor('pm',1280)
  await openInbox(pm)
  await expect(inbox(pm).getByText(empty,{exact:true})).toBeVisible()

  const reviewer=await actor('reviewer',460)
  await openInbox(reviewer)
  await expect(inbox(reviewer).getByText('可处理 · 1',{exact:true})).toBeVisible()
  await expect(inbox(reviewer).getByText('立项评审',{exact:true})).toBeVisible()
  await expect(inbox(reviewer).getByText(/Second synthetic review/)).toBeVisible()
  await expect(inbox(reviewer).getByText(/已暂停/)).toHaveCount(0)
  await button(inbox(reviewer),'打开记录').click()
  const workbench=dialog(reviewer,'关口人工工作台')
  await button(workbench,'登记裁决').click()
  await workbench.getByLabel('裁决',{exact:true}).selectOption('pass')
  const reviewReason='已经独立核对本轮合成评审证据。'
  await workbench.getByLabel('裁决理由',{exact:true}).fill(reviewReason)
  const receipt=await save(reviewer,`/api/v1/reviews/${reviewId}/decisions`,button(workbench,'确认裁决'))
  expect(receipt.decision).toMatchObject({stage:'designing',rationale:reviewReason})
  await expect(workbench.getByText(reviewReason,{exact:true})).toBeVisible()
  await reviewer.reload()
  await openInbox(reviewer)
  await expect(inbox(reviewer).getByText(empty,{exact:true})).toBeVisible()

  const sponsor=await actor('sponsor',1280)
  await openInbox(sponsor)
  await expect(inbox(sponsor).getByText('可处理 · 1',{exact:true})).toBeVisible()
  await expect(inbox(sponsor).getByText('执行中 → 已暂停',{exact:true})).toBeVisible()
  const [focused]=await Promise.all([sponsor.waitForResponse(r=>{
    const path=new URL(r.url())
    return path.pathname==='/api/v1/approvals'&&path.searchParams.get('approvalId')===transition.requestId
  }),button(inbox(sponsor),'打开记录').click()])
  expect((await focused.json()).data.items.map(item=>item.requestId)).toEqual([transition.requestId])
  const lifecycle=sponsor.getByRole('region',exact('项目生命周期'))
  await expect(lifecycle.getByText('选中的阶段流转',{exact:true})).toBeVisible()
  await button(lifecycle,'裁决阶段流转').click()
  const judge=dialog(sponsor,'裁决阶段流转')
  await judge.getByLabel('流转裁决',{exact:true}).selectOption('approve')
  const reason='确认暂停窗口和恢复责任。'
  await judge.getByLabel('裁决理由',{exact:true}).fill(reason)
  const until=(new Date(Date.now()+86400000)).toISOString().slice(0,10)+'T18:00'
  await judge.getByLabel('暂停至（本地时间）',{exact:true}).fill(until)
  const applied=await save(sponsor,`/api/v1/approvals/${transition.requestId}/decide`,button(judge,'确认流转裁决'))
  expect(applied).toMatchObject({status:'applied',decisionReason:reason})
  await expect(lifecycle.getByText('当前阶段：已暂停',{exact:true})).toBeVisible()
  await expect(lifecycle.getByText(reason,{exact:true})).toBeVisible()
  await sponsor.reload()
  await openInbox(sponsor)
  await expect(inbox(sponsor).getByText(empty,{exact:true})).toBeVisible()
}finally{await browser.close()}
