import {chromium, expect} from '@playwright/test'

const url = new URL(process.env.PMO_BROWSER_URL)
const task = JSON.parse(process.env.PMO_TEST_TASK)
const {owner} = JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform === 'win32'?'msedge':'chromium')})
const exact = name => ({name,exact:true})
const button = (scope,name) => scope.getByRole('button',exact(name))

try{
  const context = await browser.newContext({viewport:{width:460,height:850}})
  await context.addCookies([{name:'pmo_session',value:owner.cookie,domain:url.hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(token => sessionStorage.setItem('pmo.csrf',token),owner.csrf)
  const page=await context.newPage();await page.goto(url.href)
  const trigger=button(page,'我的补强待办'),queue=page.getByRole('dialog',exact('我的补强待办'))
  const items=queue.getByRole('list',exact('待办任务')).getByRole('listitem')
  const pages=queue.getByRole('navigation',exact('补强待办分页'))
  await trigger.click()
  await expect(queue.getByText('未办结 · 24',{exact:true})).toBeVisible()
  await expect(queue.locator('time').first()).toHaveAttribute('datetime',/^\d{4}-/)
  await expect(items).toHaveCount(20)
  await expect(queue.getByText(task.title,{exact:true})).toBeVisible()
  await button(pages,'Go to next page').click()
  await expect(items).toHaveCount(4)
  await expect(queue.getByText('Second project follow-up',{exact:true})).toBeVisible()
  await expect(queue.getByText('Only for the other assignee')).toHaveCount(0)
  await button(pages,'Go to previous page').click()

  const target=items.filter({hasText:task.title})
  const [read]=await Promise.all([page.waitForResponse(response=>{
    const request=new URL(response.url())
    return request.pathname===`/api/v1/projects/${task.projectId}/tasks`&&request.searchParams.get('taskId')===task.taskId
  }),button(target,'查看任务').click()])
  expect((await read.json()).data.items.map(item=>item.taskId)).toEqual([task.taskId])
  const panel=page.getByRole('region',exact('补强任务'))
  await expect(panel.getByText('选中的任务 · 1',{exact:true})).toBeVisible()
  await button(panel,'编辑任务').click()
  await panel.getByLabel('任务状态').selectOption('done')
  const [saved]=await Promise.all([page.waitForResponse(response=>response.request().method()==='PATCH'&&
    new URL(response.url()).pathname===`/api/v1/tasks/${task.taskId}`),button(panel,'保存任务').click()])
  expect(saved.status()).toBe(200)
  expect(saved.request().headers()['if-match']).toBe(String(task.revision))
  expect(saved.request().headers()['idempotency-key']).toBeTruthy()
  await expect(panel.getByRole('status')).toHaveText('任务已更新。')
  await page.reload()
  await trigger.click()
  await expect(queue.getByText('未办结 · 23',{exact:true})).toBeVisible()
  await expect(queue.getByText(task.title,{exact:true})).toHaveCount(0)
}finally{await browser.close()}
