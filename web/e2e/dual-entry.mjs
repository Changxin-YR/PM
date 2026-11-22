import {chromium, expect} from '@playwright/test'

const url = process.env.PMO_BROWSER_URL
const sessions = JSON.parse(process.env.PMO_TEST_SESSIONS)
const packet = JSON.parse(process.env.PMO_TEST_PACKET)
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const button = (scope,name) => scope.getByRole('button',{name,exact:true})
const view = (page, name) => button(page,`查看 ${name} 的项目资料`)
const projectPath = `/api/v1/projects/${packet.project.projectId}`

async function actor(role) {
  const context = await browser.newContext({baseURL:url,viewport:{width:1280,height:880}})
  const {cookie, csrf} = sessions[role]
  await context.addCookies([{name:'pmo_session',value:cookie,domain:new URL(url).hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(token => sessionStorage.setItem('pmo.csrf',token), csrf)
  const page = await context.newPage()
  const [list] = await Promise.all([page.waitForResponse(r => new URL(r.url()).pathname==='/api/v1/project-overview'),page.goto(url)])
  expect(list.status()).toBe(200)
  return {page, items:(await list.json()).data.items}
}

try {
  const {page:pm, items} = await actor('pm')
  expect(items).toHaveLength(1)
  expect(items[0]).toMatchObject({...packet.project, materialsVisible:true, canEditDraft:false,
    healthSample:{snapshotId:packet.health.snapshotId,asOf:packet.health.asOf,health:packet.health.health}})
  const row = pm.getByRole('row').filter({hasText:packet.project.name})
  await expect(row.getByRole('cell',{name:'执行中',exact:true})).toBeVisible()
  await expect(row.getByRole('cell',{name:String(packet.project.revision),exact:true})).toBeVisible()
  await view(pm,packet.project.name).click()
  const [live] = await Promise.all([pm.waitForResponse(r => new URL(r.url()).pathname===projectPath+'/health'),
    pm.getByRole('tab',{name:'三线健康',exact:true}).click()])
  expect(live.ok()).toBeTruthy()
  expect((await live.json()).data.snapshotId).toBe(packet.health.snapshotId)
  const region = pm.getByRole('region',{name:'三线健康',exact:true})
  const history = region.locator(`time[datetime="${packet.health.asOf}"]`)
  await expect(history).toBeVisible()
  const summary = region.getByText('本次来源与规则版本',{exact:true})
  await summary.click()
  const sources = region.locator('details').filter({has:pm.getByText('本次来源与规则版本',{exact:true})})
  await expect(sources.getByText(`health-snapshot:${packet.health.snapshotId}`,{exact:true})).toBeVisible()
  const satisfaction = region.locator('dl div').filter({hasText:'需求满足率'}).locator('dd')
  await expect(satisfaction).toHaveText(`${(Number(packet.health.requirementSatisfactionRate)*100).toFixed(2)}%`)
  const fixed = await pm.request.get(`${projectPath}/health`,{params:{asOf:packet.health.asOf}})
  expect(fixed.ok()).toBeTruthy()
  expect((await fixed.json()).data).toMatchObject(packet.health)

  const {page:other, items:scoped} = await actor('outsider')
  expect(scoped).toEqual([{...packet.other,materialsVisible:true,canEditDraft:true}])
  await expect(view(other,packet.other.name)).toBeVisible()
  await expect(view(other,packet.project.name)).toHaveCount(0)
  for (const path of [projectPath, `${projectPath}/health`, `/api/v1/reports/${packet.reportId}`,
    `/api/v1/evidence/${packet.evidenceId}?download=true`]) {
    const refused = await other.request.get(path)
    expect(refused.status()).toBe(404)
    expect((await refused.json()).code).toBe('RESOURCE_NOT_FOUND')
    expect(await refused.text()).not.toContain(packet.restricted)
    expect(refused.headers()['content-disposition']).toBeUndefined()
  }
} finally {
  await browser.close()
}
