import {chromium, expect} from '@playwright/test'

const url=process.env.PMO_BROWSER_URL
const session=JSON.parse(process.env.PMO_TEST_SESSIONS).reviewer
const sample=JSON.parse(process.env.PMO_TEST_PROOF)
const browser=await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
try {
  const context=await browser.newContext({timezoneId:'UTC'})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf),session.csrf)
  const page=await context.newPage()
  await page.goto(`${url}/#project=${sample.project}&tab=overview`)
  const button=(scope,name)=>scope.getByRole('button',{name,exact:true})
  await page.getByRole('tab',{name:'风险',exact:true}).click()
  await button(page,'风险处置').click()
  const form=page.getByRole('dialog',{name:'风险处置'})
  await button(form,'核验外部接收证据').click()
  await form.getByLabel('接收人 ID').fill(sample.recipient)
  await form.getByLabel('接收范围').fill(sample.scope)
  await form.getByRole('checkbox',{name:new RegExp(sample.proof)}).check()
  await form.getByLabel('接收时间（本地）').fill(sample.accepted.slice(0,19))
  await form.getByLabel('身份核验依据').fill('Synthetic test only: compared test recipient identity and document')
  const excerpt=form.getByLabel('PDF 接收声明原文')
  const mismatch='Isolated test excerpt that differs from the attached PDF.'
  await excerpt.fill(mismatch)
  await button(form,'签署证据核验').click()
  await expect(form.getByText(/请核对已选 PDF 中的接收声明/)).toBeVisible()
  await expect(excerpt).toHaveValue(mismatch)
  await excerpt.fill(sample.excerpt)
  await button(form,'签署证据核验').click()
  await expect(form.getByText('已记录独立接收证据核验。',{exact:true})).toBeVisible()
} finally {await browser.close()}
