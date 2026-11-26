import { chromium, expect } from '@playwright/test'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'
import path from 'node:path'

const root=fileURLToPath(new URL('../../',import.meta.url))
const instance=path.join(root,'.local','pmo-r6-preview')
const output=path.join(root,'.local','r6-evidence','park-browser')
const origin='http://127.0.0.1:18790'
const runtime=JSON.parse(await readFile(path.join(instance,'runtime.json'),'utf8'))
expect(runtime.web).toBe(origin)
expect(runtime.models).toEqual({})
const credentials=JSON.parse(await readFile(path.join(instance,'r6-logins.json'),'utf8'))
const receipts=JSON.parse(await readFile(path.join(instance,'R6-PARK-001-receipts.json'),'utf8'))
const project=receipts.steps.project.data.projectId
const result={项目:'东湖园区设备报修与回单试点',项目入口:[],组织入口:[],交谈:[],正式周报:[],页面错误:[],服务错误:[]}
await mkdir(output,{recursive:true})
const browser=await chromium.launch({channel:'msedge'})
async function login(role,hash=''){
  const context=await browser.newContext({viewport:{width:1380,height:950},locale:'zh-CN',timezoneId:'Asia/Shanghai'})
  const page=await context.newPage()
  page.on('pageerror',err=>result.页面错误.push(err.message))
  page.on('response',r=>{if(r.status()>=500&&r.url().startsWith(origin))result.服务错误.push([new URL(r.url()).pathname,r.status()])})
  await page.goto(origin+'/'+hash)
  await page.getByRole('textbox',{name:'账号',exact:true}).fill('r6-'+role)
  await page.getByLabel('密码',{exact:true}).fill(credentials[role])
  await page.getByRole('button',{name:'登录',exact:true}).click()
  return page
}
try{
  const pm=await login('m-pm',`#project=${project}&tab=overview`)
  const workspace=pm.getByRole('region',{name:result.项目+' · 项目资料',exact:true})
  await expect(workspace.getByRole('heading',{name:result.项目,exact:true})).toBeVisible()
  const tabs=workspace.getByRole('tablist',{name:'项目模块'}).getByRole('tab')
  const names=await tabs.allTextContents()
  for(const raw of names){
    const name=raw.trim(),button=workspace.getByRole('tab',{name,exact:true})
    await button.click();await expect(button).toHaveAttribute('aria-selected','true')
    const pane=workspace.locator('#'+await button.getAttribute('aria-controls'))
    await expect(pane).toBeVisible();await pm.waitForLoadState('networkidle')
    expect((await pane.innerText()).trim().length, name).toBeGreaterThan(0)
    result.项目入口.push(name)
  }
  expect(result.项目入口.length).toBeGreaterThanOrEqual(18)
  await workspace.getByRole('tab',{name:'项目助理',exact:true}).click()
  const assistant=workspace.getByRole('region',{name:'项目助理',exact:true})
  const chat=assistant.getByRole('region',{name:'项目对话',exact:true})
  const log=chat.getByRole('log'),panel=chat.locator('article').last()
  const select=mode=>assistant.getByText(mode,{exact:true}).first().click()
  async function send(text,expected){
    const before=await log.locator('article').count()
    await chat.getByRole('textbox').fill(text);await chat.getByRole('button',{name:'提问',exact:true}).click()
    await expect(log.locator('article')).toHaveCount(before+1)
    await expect(panel.locator('.reply')).toContainText(expected)
    const answer=await panel.locator('.reply').innerText()
    result.交谈.push({问题:text,答复:answer})
  }
  await select('立项评审');await send('立项评分有哪些弱项？','正式评审');await send('依据呢？',/对照下面 \d+ 条来源/)
  await expect(panel.locator('details')).toBeVisible();await panel.locator('summary').click();await expect(panel.locator('code').first()).toBeVisible()
  await send('展开第1条','可以对照')
  await send('这些数据是什么时候的？','上次读取');await send('再查一次','正式评审')
  await select('过程管家');await expect(log.locator('article')).toHaveCount(0)
  await send('帮我起草本周周报','周报草稿');await send('有哪些事实需要澄清？','澄清')
  await send('帮我起草西湖冷库的周报','无法确定唯一可读的项目')
  await expect(panel.getByRole('group')).toHaveCount(0)
  await select('结项回顾');await send('这个项目结项还差什么？','已归档');await send('带我做一次复盘四问','接着问下一题')
  for(const [index,text] of ['两期投入七十八人天，结算七万八千元。','地下车库检修窗口由发起人登记并获审批。','三张离线回单逐单复核，原件与签收表匹配。','下次上线前邀请物业复演断网补传。'].entries()){
    await send(text,index<3?'已记下':'复盘草稿已整理')
  }
  await expect(chat.getByRole('button',{name:'带入复盘表单'})).toHaveCount(0)
  await select('台账问答');await send('当前三线如何？',/时间|三线|快照/)
  await send('预算和实际投入相差多少？','实际少用 2000.00 元')
  await expect(panel).toContainText('获批预算 80.00 人天，实际少用 2.00 人天。')
  await send('展开第1条','实际少用 2000.00 元')
  await send('哪些里程碑预测延期了？',/预测|延期|里程碑|未发现|未知/)
  await send('哪些项目还没过立项评审就在花钱了？','立项前支出');await send('本周哪些项目亮了黄灯或红灯？','项目')
  await send('教训“回单”',/教训|回单|来源/);await send('需求“班组收到派工后确认接单”的变更历史？',/变更记录/)
  await send('教训“天文台望远镜”',/没有|未找到|未知/)
  await expect(panel.locator('details')).toHaveCount(0)
  await send('依据呢？','还没有可引用的项目记录')
  await send('替我批准归档','无法识别')
  await select('立项评审');await expect(log.locator('article')).toHaveCount(5)
  await chat.getByRole('button',{name:'新对话'}).click();await expect(log.locator('article')).toHaveCount(0)
  await select('过程管家');await send('帮我起草本周周报','周报草稿');await pm.screenshot({path:path.join(output,'过程管家对话.png')})
  await chat.getByRole('button',{name:'查看项目报告'}).click();await expect(workspace.getByRole('tab',{name:'日报周报',exact:true})).toHaveAttribute('aria-selected','true')
  await workspace.getByRole('tab',{name:'项目助理',exact:true}).click();await select('过程管家');await expect(log.locator('article')).toHaveCount(4)
  await pm.setViewportSize({width:390,height:844});await expect(chat.getByRole('textbox')).toBeVisible()
  expect(await pm.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
  await pm.screenshot({path:path.join(output,'窄屏对话.png')})
  const admin=await login('admin');const nav=admin.getByRole('navigation',{name:'工作台导航'})
  await expect(nav.getByRole('button',{name:'组织人员',exact:true})).toBeVisible()
  for(const text of await nav.getByRole('button').allTextContents()){
    const label=text.trim(),button=nav.getByRole('button',{name:label,exact:true});await button.click()
    await expect(button).toHaveClass(/is-current/);await admin.waitForLoadState('networkidle');result.组织入口.push(label)
  }
  expect(result.组织入口).toContain('审计记录');expect(result.页面错误).toEqual([]);expect(result.服务错误).toEqual([])
  const print=await browser.newPage({locale:'zh-CN'})
  for(const [index,effort] of ['38.00','40.00'].entries()){
    const period=index+1
    const name=`园区试点第${period}期周报`
    const html=await readFile(path.join(output,'..',name+'.html'))
    const digest=createHash('sha256').update(html).digest('hex')
    expect(digest).toBe(receipts.隔离收件[String(period)].原件摘要)
    await print.setContent(html.toString('utf8'))
    await expect(print.getByText('申报人天',{exact:true})).toBeVisible()
    await expect(print.getByText(effort,{exact:true}).first()).toBeVisible()
    const pdf=await print.pdf({path:path.join(output,name+'.pdf'),preferCSSPageSize:true,printBackground:true})
    expect(pdf.toString('latin1').match(/\/Type\s*\/Page\b/g)).toHaveLength(1)
    result.正式周报.push({期次:period,申报人天:effort,页数:1,收件原件摘要:digest})
  }
  await print.close()
  result.结果='通过'
}finally{
  await writeFile(path.join(output,'实测.json'),JSON.stringify(result,null,2)+'\n')
  await browser.close()
}
console.log(`项目入口 ${result.项目入口.length}，组织及个人入口 ${result.组织入口.length}，对话 ${result.交谈.length} 轮。`)
