import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL
const sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const exact=name=>({name,exact:true})

async function actor(role,width=1280){
  const context=await browser.newContext({viewport:{width,height:820}})
  const session=sessions[role]
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),session.csrf)
  const page=await context.newPage();await page.goto(url)
  return page
}
async function health(page){
  const drawer=page.getByRole('region',exact('Synthetic review · 项目资料'))
  await page.getByRole('button',exact('查看 Synthetic review 的项目资料')).click()
  await showNavigation(page)
  await drawer.getByRole('tab',exact('三线健康')).click()
  const state=drawer.getByRole('region',exact('三线健康'))
  await expect(state.getByRole('heading',exact('当前健康'))).toBeVisible()
  return state
}
async function submit(page,path,heading){
  const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&path.test(new URL(r.url()).pathname))
  await page.getByRole('dialog',exact(heading)).getByRole('button',exact('确认提交')).click()
  const result=await waiting
  expect(result.request().headers()['idempotency-key']).toBeTruthy()
  expect(result.request().headers()['if-match']).toBe('1')
  expect(result.status()).toBe(200)
  return (await result.json()).data
}

try{
  const pm=await actor('pm',460)
  const screen=await health(pm)
  await expect(screen.getByText('暂停正式发布',{exact:true})).toBeVisible()
  await expect(screen.getByText('关键里程碑申报缺少已核验佐证',{exact:true})).toBeVisible()
  await expect(screen.getByText('正式快照',{exact:true})).toHaveCount(1)
  await expect(screen.getByText('0.00%',{exact:true})).toHaveCount(0)
  const drawer=pm.getByRole('region',exact('Synthetic review · 项目资料'))
  await showNavigation(pm)
  await drawer.getByRole('tab',exact('成本')).click()
  const effort=drawer.getByRole('region',exact('人天'))
  await expect(effort.getByText('不可用',{exact:true})).toBeVisible()
  await expect(effort.getByText('NaN%',{exact:true})).toHaveCount(0)
  await showNavigation(pm)
  await drawer.getByRole('tab',exact('三线健康')).click()
  const reason='本次关键进展的独立佐证尚未上传。'
  await screen.getByRole('button',exact('申请复核')).click()
  await pm.getByRole('dialog',exact('申诉健康快照')).getByLabel('申诉理由').fill(reason)
  const receipt=await submit(pm,/\/health-snapshots\/[^/]+\/appeals$/,'申诉健康快照')
  expect(receipt.reason).toBe(reason)
  await expect(screen.getByText(reason,{exact:true})).toBeVisible()
  await expect(screen.getByRole('button',exact('申请复核'))).toHaveCount(0)

  const reviewer=await actor('reviewer')
  const history=await health(reviewer)
  await history.getByRole('button',exact('复核申诉')).click()
  const why='已核对原快照来源，维持当时的结果。'
  await reviewer.getByRole('dialog',exact('复核健康申诉')).getByLabel('复核理由').fill(why)
  expect((await submit(reviewer,/\/health-appeals\/[^/]+\/review$/,'复核健康申诉')).verdict).toBe('upheld')
  await pm.reload()
  const restored=await health(pm)
  await expect(restored.getByText(why,{exact:false})).toBeVisible()
  await expect(restored.getByText('暂停正式发布',{exact:true})).toBeVisible()
  await expect(restored.getByText('正式快照',{exact:true})).toHaveCount(1)
  await expect(restored.getByRole('button',exact('申请复核'))).toHaveCount(0)
}finally{await browser.close()}
