import {chromium,expect} from '@playwright/test'
import {mkdir} from 'node:fs/promises'

const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
try {
  const page=await browser.newPage({viewport:{width:1280,height:768}})
  await page.goto(process.env.PMO_BROWSER_URL)
  await page.getByLabel('账号',{exact:true}).fill(process.env.PMO_TEST_USER)
  await page.getByLabel('密码',{exact:true}).fill(process.env.PMO_TEST_PASSWORD)
  await page.getByRole('button',{name:'登录',exact:true}).click()
  await page.getByRole('button',{name:'新建项目'}).click()
  await page.getByLabel('项目编号').fill('E2E-001')
  await page.getByLabel('项目名称',{exact:true}).fill('交付演练')
  await page.getByRole('button',{name:'保存',exact:true}).click()
  await expect(page.getByRole('cell',{name:'交付演练',exact:true})).toBeVisible()
  await page.reload()
  await page.getByRole('button',{name:'修改草稿名称'}).click()
  await page.getByLabel('项目名称',{exact:true}).fill('试点范围复核')
  await page.getByRole('button',{name:'保存',exact:true}).click()
  await expect(page.getByRole('cell',{name:'试点范围复核',exact:true})).toBeVisible()
  await expect(page.getByRole('dialog')).toBeHidden()
  await mkdir('test-results',{recursive:true})
  await page.screenshot({path:'test-results/ledger-desktop.png'})
  await page.setViewportSize({width:390,height:844})
  await page.screenshot({path:'test-results/ledger-mobile.png'})
  await expect(page.getByRole('button',{name:'新建项目'})).toBeInViewport()
  await page.getByRole('button',{name:'退出登录'}).click()
  await expect(page.getByRole('heading',{name:'登录工作台'})).toBeVisible()
} finally {await browser.close()}
