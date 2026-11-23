import {chromium, expect} from '@playwright/test'
import {showNavigation} from './workspace-navigation.mjs'

const url=process.env.PMO_BROWSER_URL, session=JSON.parse(process.env.PMO_TEST_SESSIONS).sponsor
const [due,observedMonth]=JSON.parse(process.env.PMO_TEST_LR08_MONTHS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
try {
  const context=await browser.newContext({viewport:{width:960,height:840}})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,
    path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),session.csrf)
  const page=await context.newPage()
  await page.goto(url)
  await showNavigation(page)
  await page.getByRole('button',{name:'预警中心',exact:true}).click()
  const dialog=page.getByRole('region',{name:'预警中心',exact:true})
  await dialog.getByRole('combobox',{name:'规则'}).selectOption('LR-08')
  async function month(value){
    const wait=page.waitForResponse(r=>{
      const request=new URL(r.url())
      return request.pathname.endsWith('/analytics/health-alerts')&&request.searchParams.get('month')===value
    })
    await dialog.getByLabel('月报归属月份').fill(value)
    await dialog.getByLabel('月报归属月份').blur()
    const response=await wait
    expect(response.status()).toBe(200)
    return (await response.json()).data
  }
  await month(observedMonth)
  await expect(dialog.getByText('暂无授权项目预警')).toBeVisible()
  const alerts=await month(due.slice(0,7))
  await expect(dialog.getByText('授权项目预警 · 1')).toBeVisible()
  await expect(dialog.locator('time[aria-label="列表读取时间"]')).toHaveAttribute('datetime',alerts.asOf)
  await dialog.getByRole('row').filter({hasText:'LR-08'}).locator('.el-table__expand-icon').click()
  await expect(dialog.getByText('复盘到期日',{exact:true})).toBeVisible()
  await expect(dialog.getByText(due,{exact:true})).toBeVisible()
  await expect(dialog.locator('time[aria-label="事件更新时间"]')).toHaveAttribute('datetime',alerts.items[0].asOf)
} finally {
  await browser.close()
}
