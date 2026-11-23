import {join} from 'node:path'
import {showNavigation} from './workspace-navigation.mjs'
import {chromium, expect} from '@playwright/test'

const url=process.env.PMO_BROWSER_URL
const sessions=JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const exact=name=>({name,exact:true})

async function open(role, width){
  const session=sessions[role]
  const context=await browser.newContext({viewport:{width,height:900}})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf), session.csrf)
  const page=await context.newPage()
  await page.goto(url)
  await showNavigation(page)
  await page.getByRole('button',exact('预警中心')).click()
  await page.getByRole('tab',exact('每周三线')).click()
  return {context,page,panel:page.getByRole('region',exact('每周三线'))}
}

try{
  for (const width of [1280,390]) {
    const {context,page,panel}=await open('pm',width)
    await expect(panel.getByText('REVIEWS · Synthetic review',{exact:true})).toBeVisible()
    await expect(panel.getByText('总体 绿',{exact:true})).toBeVisible()
    await expect(panel.getByText('时间 黄',{exact:true})).toBeVisible()
    await panel.getByText('周内最近采样 · 原因和出处',{exact:true}).click()
    await expect(panel.getByText('关键里程碑预测延期',{exact:false})).toBeVisible()
    await expect(panel.getByText('延后 4 天',{exact:false})).toBeVisible()
    await expect(panel.getByText(process.env.PMO_TEST_SNAPSHOT,{exact:true})).toBeVisible()
    expect(await panel.evaluate(node=>node.scrollWidth-node.clientWidth)).toBeLessThanOrEqual(1)
    await page.screenshot({path:join(process.env.PMO_TEST_SCREENSHOTS,`weekly-health-${width}.png`)})
    await context.close()
  }
  const {panel}=await open('outsider',390)
  await expect(panel.getByText('本周无授权项目采样',{exact:true})).toBeVisible()
  await expect(panel.getByText('Synthetic review',{exact:false})).toHaveCount(0)
}finally{await browser.close()}
