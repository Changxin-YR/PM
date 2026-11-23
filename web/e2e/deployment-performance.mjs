import {chromium, expect} from '@playwright/test'
import {returnToLedger} from './workspace-navigation.mjs'

const url = new URL(process.env.PMO_BROWSER_URL)
if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1') throw Error('Requires a loopback stack')
const session = JSON.parse(process.env.PMO_SYNTHETIC_SESSION)
const channel = process.env.PMO_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : 'chromium')
const browser = await chromium.launch({channel})
const button = (scope, name) => scope.getByRole('button', {name, exact:true})
const p95 = samples => Math.round([...samples].sort((a,b) => a-b)[Math.ceil(samples.length*0.95)-1])

try {
  const context = await browser.newContext({viewport:{width:1280,height:900}})
  await context.addCookies([{name:'pmo_session',value:session.cookie,domain:url.hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(csrf => sessionStorage.setItem('pmo.csrf', csrf), session.csrf)
  const page = await context.newPage()
  await page.goto(url.href)
  const links = page.getByRole('button', {name:/^查看 RC project \d\d 的项目资料$/})
  await expect(links).toHaveCount(15)
  const search = page.getByLabel('搜索项目名称或编号')
  const filterMs = [], detailMs = [], milestonesMs = []

  // Three unmeasured passes warm the browser and DB pool, then measure 30 distinct UI interactions.
  for (let i = -3; i < 30; i++) {
    const suffix = String((i+15)%15+1).padStart(2,'0')
    const keyword = `RC-SMOKE-${suffix}`
    await search.fill(keyword)
    const startFilter = performance.now()
    const [list] = await Promise.all([page.waitForResponse(r => {
      const u = new URL(r.url())
      return u.pathname === '/api/v1/project-overview' && u.searchParams.get('filters[keyword]') === keyword
    }), button(page,'搜索').click()])
    expect(list.status()).toBe(200)
    const {items, total} = (await list.json()).data
    const [project] = items
    expect(total).toBe(1)
    expect(project).toMatchObject({code:keyword,stage:'drafting',revision:1})
    const link = button(page,`查看 ${project.name} 的项目资料`)
    await expect(links).toHaveCount(1)
    await expect(link).toBeVisible()
    const endFilter = performance.now()

    const startDetail = performance.now()
    const [detail] = await Promise.all([page.waitForResponse(r =>
      new URL(r.url()).pathname === `/api/v1/project-overview/${project.projectId}`), link.click()])
    expect(detail.status()).toBe(200)
    const drawer = page.getByRole('region', {name:`${project.name} · 项目资料`,exact:true})
    await expect(drawer.getByRole('region',{name:'立项概要',exact:true})).toBeVisible()
    const endDetail = performance.now()
    const tab = drawer.getByRole('tab',{name:'里程碑',exact:true})
    const [milestones] = await Promise.all([page.waitForResponse(r =>
      new URL(r.url()).pathname === `/api/v1/projects/${project.projectId}/milestones`), tab.click()])
    expect(milestones.status()).toBe(200)
    await expect(drawer.getByRole('cell',{name:`Acceptance milestone ${suffix}`,exact:true})).toBeVisible()
    const endMilestones = performance.now()
    if (i >= 0) {
      filterMs.push(endFilter-startFilter)
      detailMs.push(endDetail-startDetail)
      milestonesMs.push(endMilestones-endDetail)
    }
    await returnToLedger(page)
    await expect(drawer).toBeHidden()
  }

  const summary = {channel,browser:browser.version(),viewport:'1280x900',projectCount:15,samplesPerOperation:30,
    p95FilterMs:p95(filterMs),p95DetailMs:p95(detailMs),p95MilestonesMs:p95(milestonesMs)}
  console.log(JSON.stringify(summary))
  expect(summary.p95FilterMs).toBeLessThanOrEqual(1000)
  expect(summary.p95DetailMs).toBeLessThanOrEqual(1000)
  expect(summary.p95MilestonesMs).toBeLessThanOrEqual(1000)
} finally {
  await browser.close()
}
