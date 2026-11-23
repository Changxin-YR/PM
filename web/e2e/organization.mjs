import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL
const session=JSON.parse(process.env.PMO_TEST_SESSIONS).pm
const username=process.env.PMO_TEST_USERNAME
const password=process.env.PMO_TEST_MEMBER_PASSWORD
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})

async function submit(page,action,method,suffix,{rev}={}){
  const response=page.waitForResponse(r=>r.request().method()===method&&new URL(r.url()).pathname.endsWith(suffix))
  await action.click()
  const sent=await response
  expect(sent.request().headers()['idempotency-key']).toBeTruthy()
  if(rev!==undefined)expect(sent.request().headers()['if-match']).toBe(String(rev))
  expect(sent.status(),await sent.text()).toBe(200)
  return sent
}

try{
  const context=await browser.newContext({viewport:{width:1240,height:860}})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),session.csrf)
  const admin=await context.newPage();await admin.goto(url)
  await button(admin,'组织人员').click()
  const panel=admin.getByRole('region',{name:'组织人员',exact:true})
  await panel.getByRole('tab',{name:'邀请',exact:true}).click()
  await button(panel,'邀请人员').click()
  const form=panel.getByRole('form',{name:'邀请人员',exact:true})
  const address=`${username}@example.test`
  await form.getByLabel('受邀邮箱').fill(address)
  await form.getByLabel('职务提示').selectOption('reviewer')
  await submit(admin,button(form,'签发邀请'),'POST','/admin/users/invitations')
  const code=await panel.getByLabel('一次性邀请码').inputValue()
  expect(code.length).toBe(43)
  await expect(panel.getByRole('row').filter({hasText:address})).toContainText('待接受')

  const visitor=await browser.newContext({viewport:{width:440,height:820}})
  const member=await visitor.newPage();await member.goto(url)
  await button(member,'接受组织邀请').click()
  const redeem=member.getByRole('form',{name:'接受组织邀请',exact:true})
  await redeem.getByLabel('邀请码').fill(code)
  await redeem.getByLabel('显示姓名').fill('合成组织成员')
  await redeem.getByLabel('账号',{exact:true}).fill(username)
  await redeem.getByLabel('设置密码',{exact:true}).fill(password)
  await redeem.getByLabel('再次输入密码').fill(password)
  const accepted=await submit(member,button(redeem,'确认接受'),'POST','/auth/invitations/accept',{rev:1})
  expect((await accepted.json()).data.isSystemAdmin).toBe(false)
  await expect(member.getByText('账号已开通，请登录。',{exact:true})).toBeVisible()
  await member.getByLabel('密码',{exact:true}).fill(password)
  await button(member,'登录').click()
  await expect(member.getByText('已授权项目 · 0',{exact:true})).toBeVisible()
  await showNavigation(member)
  await expect(button(member,'组织人员')).toHaveCount(0)
  await button(member,'关闭导航').click()

  await panel.getByRole('tab',{name:'成员',exact:true}).click()
  await expect(panel.getByRole('row').filter({hasText:'合成组织成员'})).toContainText('评审人')
  await expect(panel.getByLabel('一次性邀请码')).toHaveCount(0)
  await panel.getByRole('tab',{name:'账号',exact:true}).click()
  const entry=panel.getByRole('row').filter({hasText:username})
  await button(entry,'停用').click()
  const change=panel.getByRole('form',{name:'确认组织操作',exact:true})
  await change.getByLabel('原因').fill('隔离环境模拟员工离职。')
  const stopped=await submit(admin,button(change,'确认变更'),'PATCH','/status',{rev:1})
  expect((await stopped.json()).data.isActive).toBe(false)
  await expect(entry).toContainText('已停用')
  await member.reload()
  await expect(member.getByRole('heading',{name:'登录工作台'})).toBeVisible()
  await admin.reload()
  await button(admin,'组织人员').click()
  await expect(panel.getByRole('row').filter({hasText:username})).toContainText('已停用')
}finally{await browser.close()}
