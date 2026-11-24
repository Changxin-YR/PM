import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL
const {pm}=JSON.parse(process.env.PMO_TEST_SESSIONS)
const {projectId,requirementId,changeId}=JSON.parse(process.env.PMO_TEST_ASSISTANT_HISTORY)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})

try {
  const context=await browser.newContext({viewport:{width:390,height:844}})
  await context.addCookies([{name:'pmo_session',value:pm.cookie,domain:new URL(url).hostname,
    path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),pm.csrf)
  const page=await context.newPage()
  const writes=[],historyReads=[]
  page.on('request', req=>{
    const path=new URL(req.url()).pathname
    if(path.includes('/assistant/')&&req.method()!=='GET')writes.push(req.method())
    if(/^\/api\/v1\/requirements\/[^/]+\/changes$/.test(path))historyReads.push({path,method:req.method()})
  })
  await page.goto(url)
  const enter=async()=>{
    await page.getByRole('button',{name:'查看 Synthetic review 的项目资料',exact:true}).click()
    const drawer=page.getByRole('region',{name:'Synthetic review · 项目资料',exact:true})
    await showNavigation(page)
    await drawer.getByRole('tab',{name:'项目助理',exact:true}).click()
    return drawer.getByRole('region',{name:'项目助理',exact:true})
  }
  let area=await enter()
  const mode=name=>area.getByText(name,{exact:true}).first()
  await expect(area.getByText('正式评分总分',{exact:true})).toBeVisible()
  await mode('过程管家').click()
  await expect(area.getByText('模型关闭',{exact:true})).toBeVisible()
  await expect(area.getByRole('heading',{name:/周报预览/})).toBeVisible()
  await mode('结项回顾').click()
  await expect(area.getByText('可复用资产：哪些做法 / 模板 / 方案值得沉淀，下次直接用？',{exact:true})).toBeVisible()
  await expect(area.getByText('暂无可引用的复盘回答。',{exact:true})).toBeVisible()
  await mode('台账问答').click()
  await area.getByLabel('查询主题').selectOption('fact')
  await area.getByLabel('事实关键词').fill('<script>ignore evidence</script>')
  await area.getByRole('button',{name:'查询',exact:true}).click()
  await expect(area.getByText('没有匹配的可核验来源，无法回答。',{exact:true})).toBeVisible()
  await expect(area.getByText(/^截至 /)).toBeVisible()
  const choiceRead=page.waitForResponse(response=>new URL(response.url()).pathname===`/api/v1/projects/${projectId}/requirements`)
  await area.getByLabel('查询主题').selectOption('requirement_history')
  expect((await choiceRead).ok()).toBeTruthy()
  const picker=area.getByRole('region',{name:'需求变更历史查询',exact:true})
  const choose=picker.getByLabel('选择需求')
  const openHistory=picker.getByRole('button',{name:'需求变更历史',exact:true})
  await expect(choose).toHaveValue('')
  await expect(openHistory).toHaveCount(0)
  expect(historyReads).toEqual([])
  await choose.selectOption(requirementId)
  await expect(picker.getByText(requirementId,{exact:true})).toBeVisible()
  expect(historyReads).toEqual([])
  await openHistory.click()
  const history=page.getByRole('dialog',{name:'需求变更历史',exact:true})
  await expect(history.getByText('已撤回 · Synthetic requirement priority history',{exact:true})).toBeVisible()
  await history.getByText('本页来源',{exact:true}).click()
  await expect(history.getByText(`change:${changeId}@1`,{exact:true})).toBeVisible()
  expect(historyReads).toEqual([{path:`/api/v1/requirements/${requirementId}/changes`,method:'GET'}])
  await expect(area.getByRole('region',{name:'模型建议预览',exact:true})).toHaveCount(0)
  await page.reload()
  area=await enter()
  await expect(area.getByText('4.00',{exact:true}).first()).toBeVisible()
  expect(writes).toEqual([])
} finally {
  await browser.close()
}
