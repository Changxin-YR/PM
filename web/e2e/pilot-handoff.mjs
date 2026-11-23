import {chromium, expect} from '@playwright/test'
import {join} from 'node:path'

const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const {url, a, b, otherReport, logins, screenshots} = JSON.parse(Buffer.concat(chunks).toString('utf8'))
const edge = await chromium.launch({channel:'msedge'})
const initial = `/#project=${a.id}&tab=`

async function signIn(path, login) {
  const context = await edge.newContext({viewport:{width:1320,height:900}})
  const page = await context.newPage()
  await page.goto(url + path)
  await page.getByLabel('账号',{exact:true}).fill(login.username)
  await page.getByLabel('密码',{exact:true}).fill(login.password)
  await page.getByRole('button',{name:'登录',exact:true}).click()
  return {context,page}
}

try {
  const {page,context} = await signIn(initial + 'assistant', logins['pm-a'])
  const assistant = page.getByRole('region',{name:'项目助理',exact:true})
  await expect(assistant).toBeVisible()
  for (const [name, endpoint] of [['过程管家','process'], ['结项回顾','closing'], ['台账问答','ledger']]) {
    const loaded = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(`/assistant/${endpoint}`))
    await assistant.getByText(name,{exact:true}).click()
    expect((await loaded).ok()).toBeTruthy()
  }
  await expect(assistant.getByRole('heading',{name:'台账结果',exact:true})).toBeVisible()
  await expect(assistant.getByRole('region',{name:'模型建议预览'})).toHaveCount(0)
  await page.screenshot({path:join(screenshots,'pilot-assistant.png')})

  await page.goto(url + initial + `reports&report=${a.reportId}`)
  // A hash-only navigation across workbenches does not reinitialize the current session.
  await page.reload()
  const details = page.getByRole('article',{name:'报告详情'})
  const analysis = details.getByRole('region',{name:'报告分析',exact:true})
  await expect(analysis.getByRole('button',{name:'转人工处理'})).toBeVisible()
  await page.screenshot({path:join(screenshots,'pilot-report-analysis.png')})

  await page.goto(url + initial + `reports&report=${otherReport}`)
  await page.reload()
  await expect(page.getByText('报告不属于当前项目。',{exact:true})).toBeVisible()
  await expect(details).toHaveCount(0)
  await context.close()

  const tokens = await signIn(`/#project=${b.id}&entry=agent-tokens`, logins.init)
  const drawer = tokens.page.getByRole('region',{name:'我的 Agent 令牌',exact:true})
  await expect(drawer.getByRole('button',{name:'刷新令牌',exact:true})).toBeVisible()
  await tokens.context.close()
  console.log('PASS: pilot Edge login, four assistant modes, report analysis, cross-project report refusal and token drawer')
} finally {
  await edge.close()
}
