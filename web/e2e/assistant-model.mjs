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
  const posts=[]
  page.on('request', request=>{
    if(request.method()==='POST' && request.url().includes('/assistant/suggestions'))posts.push(request.postDataJSON())
  })
  await page.goto(url)
  await page.getByRole('button',{name:'查看 Synthetic review 的项目资料',exact:true}).click()
  const drawer=page.getByRole('dialog',{name:'Synthetic review · 项目资料',exact:true})
  await drawer.getByRole('tab',{name:'项目助理',exact:true}).click()
  const mode=drawer.getByRole('region',{name:'项目助理',exact:true})
  const section=mode.getByRole('region',{name:'模型建议预览',exact:true})
  const preview=section.getByRole('button',{name:'生成预览',exact:true})
  await expect(mode.getByText('正式评分总分',{exact:true})).toBeVisible()
  expect(posts).toEqual([])
  await preview.click()
  await expect(section.getByText('该目标如何验收，什么证据能证明达成？',{exact:true})).toBeVisible()
  await section.getByText('来源',{exact:true}).click()
  await expect(section.getByText(/^charter:/)).toBeVisible()
  expect(await section.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true)

  await mode.getByText('台账问答',{exact:true}).first().click()
  await mode.getByLabel('查询主题').selectOption('cost')
  const ledger=mode.getByText(/金额（CNY）：已核验净额/)
  await expect(ledger).toBeVisible()
  await expect(section.getByText(/如何验收/)).toHaveCount(0)
  await preview.click()
  await expect(section.getByRole('status')).toHaveText(/无法追溯到本次来源/)
  await expect(ledger).toBeVisible()
  expect(posts).toEqual([{mode:'charter'},{mode:'ledger',topic:'cost'}])
} finally {
  await browser.close()
}
