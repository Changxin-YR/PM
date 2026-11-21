import {chromium, expect} from '@playwright/test'

const url=process.env.PMO_BROWSER_URL
const {pm}=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})

try {
  const context=await browser.newContext({viewport:{width:390,height:844}})
  await context.addCookies([{name:'pmo_session',value:pm.cookie,domain:new URL(url).hostname,
    path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),pm.csrf)
  const page=await context.newPage()
  const writes=[]
  page.on('request', req=>{if(new URL(req.url()).pathname.includes('/assistant/')&&req.method()!=='GET')writes.push(req.method())})
  await page.goto(url)
  const enter=async()=>{
    await page.getByRole('button',{name:'查看 Synthetic review 的项目资料',exact:true}).click()
    const drawer=page.getByRole('dialog',{name:'Synthetic review · 项目资料',exact:true})
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
  await expect(area.getByText('可复用资产',{exact:true})).toBeVisible()
  await expect(area.getByText('暂无可引用的复盘回答。',{exact:true})).toBeVisible()
  await mode('台账问答').click()
  await area.getByLabel('查询主题').selectOption('fact')
  await area.getByLabel('事实关键词').fill('<script>ignore evidence</script>')
  await area.getByRole('button',{name:'查询',exact:true}).click()
  await expect(area.getByText('没有匹配的可核验来源，无法回答。',{exact:true})).toBeVisible()
  await expect(area.getByText(/^截至 /)).toBeVisible()
  await page.reload()
  area=await enter()
  await expect(area.getByText('4.00',{exact:true}).first()).toBeVisible()
  expect(writes).toEqual([])
} finally {
  await browser.close()
}
