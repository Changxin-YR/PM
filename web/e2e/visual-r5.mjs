import { chromium, expect } from '@playwright/test'
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const url = new URL(process.env.PMO_BROWSER_URL)
const output = process.env.PMO_R5_VISUAL_OUTPUT
const sessions = JSON.parse(process.env.PMO_TEST_SESSIONS)
const projects = JSON.parse(process.env.PMO_R5_VISUAL_PROJECTS)
const options = { channel: 'msedge', locale: 'zh-CN', timezoneId: 'Asia/Shanghai' }
const result = { url: url.origin, browser: 'Microsoft Edge', frontendTree:process.env.PMO_R5_VISUAL_FRONTEND_TREE,
  checks: [], browserErrors: [] }
const button = (page, name) => page.getByRole('button', {name, exact:true}).first()
const heading = (page, name) => page.getByRole('heading', {name, exact:true}).first()
const projectNav = ['overview','charter','milestones','requirements','baseline','changes','evidence','risks','costs',
  'health','reviews','lifecycle','tasks','reports','stakeholders','members','retrospective','assistant']
const personalNav = [
  ['33','ledger','项目台账'], ['36','approvals','我的审批收件箱'], ['37','personal-tasks','我的补强待办'],
  ['38','termination','我的终止确认'], ['39','appointments','人员任命'], ['40','incoming-risk','风险接收'],
  ['41','alerts','预警中心'], ['42','spending','立项前支出'], ['43','agent-tokens','我的 Agent 令牌'],
]
const adminNav = [['44','audit','审计记录'], ['45','directory','组织人员'], ['46','org-tokens','组织令牌']]
const currentProject = new WeakMap()
const activeRequests = new WeakMap()

await mkdir(output, {recursive:true})
const browser = await chromium.launch({channel:options.channel})

async function authenticate(context, role) {
  const session = sessions[role]
  await context.addCookies([{name:'pmo_session', value:session.cookie, domain:url.hostname,
    path:'/api/v1', httpOnly:true, sameSite:'Strict'}])
  await context.addInitScript(csrf => sessionStorage.setItem('pmo.csrf', csrf), session.csrf)
}

function watch(page) {
  const requests = new Set()
  activeRequests.set(page,requests)
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/')) requests.add(request)
  })
  page.on('requestfinished', request=>requests.delete(request))
  page.on('requestfailed', request=>requests.delete(request))
  page.on('pageerror', e => result.browserErrors.push(e.message))
  page.on('console', message => {
    const detail=message.text()
    if (message.type()==='error'&&!detail.startsWith('Failed to load resource:')) result.browserErrors.push(detail)
  })
  page.on('response', r => {
    if (r.status() >= 500) result.browserErrors.push(`${r.status()} ${new URL(r.url()).pathname}`)
  })
}

async function visited(context, role) {
  if (role) await authenticate(context, role)
  const page = await context.newPage()
  watch(page)
  return page
}

async function projectPage(page, key, project=projects.execution) {
  const pane=page.locator(`#project-pane-${key}`)
  if (currentProject.get(page)!==project) {
    await page.goto(`${url.origin}/#project=${project}&tab=overview`)
    await page.reload()
    await expect(page.locator('#project-pane-overview')).toBeVisible()
    currentProject.set(page,project)
  }
  if (!await pane.isVisible()) {
    const toggle=button(page,'打开导航')
    if (await toggle.isVisible()) await toggle.click()
    await page.locator(`#project-tab-${key}`).click()
  }
  await expect(pane).toBeVisible()
  return pane
}

async function globalPage(page, key, name) {
  if (currentProject.has(page)) {
    await button(page,'返回项目台账').click()
    currentProject.delete(page)
  }
  if (key!=='ledger' || !await heading(page,name).isVisible()) {
    const toggle=button(page,'打开导航')
    if (await toggle.isVisible()) await toggle.click()
    await button(page,name).click()
  }
  await expect(heading(page,name)).toBeVisible()
  return page.locator('main')
}

async function settled(scope) {
  await expect(scope.locator('[aria-busy="true"]:visible, .el-loading-mask:visible')).toHaveCount(0, {timeout:12000})
}

async function capture(page, id, description, find, {file=true, zoom=100}={}) {
  const check = {id, description, zoom}
  try {
    const scope = await find()
    await expect(scope).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    await settled(scope)
    await expect.poll(()=>activeRequests.get(page)?.size).toBe(0)
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
    Object.assign(check, await scope.evaluate(el => ({
      viewport: innerWidth,
      realScaling: devicePixelRatio,
      bodyWidth: document.documentElement.scrollWidth,
      selected: document.querySelectorAll('.nav-entry.is-current').length,
      navigation: document.querySelectorAll('.context-sidebar').length,
      textLength: el.innerText.trim().length,
    })))
    if (file) {
      check.file = `${id}.png`
      // The full-page screenshot API crops native zoom to CSS viewport dimensions.
      const fullPage=!id.startsWith('zoom-')
      if (fullPage) await page.evaluate(()=>window.scrollTo(0,0))
      await page.screenshot({path:join(output, check.file), animations:'disabled', fullPage})
    }
    expect(check.bodyWidth).toBeLessThanOrEqual(check.viewport)
    expect(check.navigation).toBeLessThanOrEqual(1)
    expect(check.selected).toBe(check.navigation)
    expect(check.textLength).toBeGreaterThan(0)
    check.status = 'PASS'
  } catch (error) {
    check.status = 'FAIL'
    check.error = error.message.slice(0, 750)
  }
  result.checks.push(check)
}

async function authViews(page, prefix='', options={}) {
  await page.goto(url.origin)
  await page.reload()
  await capture(page,`${prefix}13`,'登录',() => page.locator('.login'),options)
  await capture(page,`${prefix}34`,'接受组织邀请',async () => {
    await page.getByText('接受组织邀请',{exact:true}).click()
    return page.locator('.login')
  },options)
}

async function adminViews(page, prefix='', options={}) {
  await page.goto(url.origin)
  await page.reload()
  currentProject.delete(page)
  await expect(heading(page,'项目台账')).toBeVisible()
  for (const [id,key,name] of adminNav) await capture(page,`${prefix}${id}`,name,()=>globalPage(page,key,name),options)
}

const project = (page, key, alternate) => () => projectPage(page, key, alternate)
const click = (page, key, name, selector) => async () => {
  const pane = await projectPage(page, key)
  await button(pane, name).click()
  return selector ? page.locator(selector).filter({visible:true}).first() : pane
}

async function assist(page, mode, section, portfolio=false) {
  const pane=await projectPage(page, 'assistant')
  await pane.getByText(mode, {exact:true}).first().click()
  await expect(heading(pane, section)).toBeVisible()
  if (portfolio) await button(pane, '查看组合周报').click()
  return pane
}

async function report(page, kind) {
  const pane=await projectPage(page, 'reports')
  await button(pane.getByRole('row').filter({hasText:new RegExp(`· ${kind}`)}), '查看').click()
  return pane.getByRole('article',{name:'报告详情'})
}

async function main() {
  const auth = await browser.newContext({...options, viewport:{width:1440,height:930}})
  const anonymous = await visited(auth)
  await authViews(anonymous)
  await auth.close()

  const desktop = await browser.newContext({...options, viewport:{width:1440,height:930}})
  const pm = await visited(desktop,'pm')
  result.userAgent=await pm.evaluate(()=>navigator.userAgent)
  await pm.goto(url.origin)
  for (const [id,key,name] of personalNav) await capture(pm,id,name,()=>globalPage(pm,key,name))
  await globalPage(pm,'ledger','项目台账')
  await capture(pm,'35','新建项目草稿',async () => {
    await button(pm,'新建项目').click()
    return pm.getByRole('dialog',{name:'新建项目草稿'})
  })
  await pm.keyboard.press('Escape')

  for (const [id,key,name] of [
    ['14','overview','项目概览'],['15','charter','立项卡'],['16','milestones','里程碑'],
    ['18','requirements','需求'],['21','baseline','需求基线'],['22','changes','变更台账'],
    ['23','evidence','证据附件'],['24','risks','风险'],['25','costs','成本'],['28','health','三线健康'],
    ['29','reviews','关口送审'],['31','lifecycle','阶段流转'],['11','reports','日报周报'],
    ['09','stakeholders','联系台账'],['08','members','项目成员'],['07','retrospective','复盘与教训'],
  ]) await capture(pm,id,name,project(pm,key))

  await capture(pm,'01','阶段流转申请', click(pm,'lifecycle','申请阶段流转','.el-dialog'))
  await pm.keyboard.press('Escape')
  await capture(pm,'32','阶段流转申请（重复原型）',click(pm,'lifecycle','申请阶段流转','.el-dialog'))
  await pm.keyboard.press('Escape')
  await capture(pm,'02','助理·台账问答',()=>assist(pm,'台账问答','台账结果'))
  await capture(pm,'03','助理·结项回顾',()=>assist(pm,'结项回顾','结项门槛'))
  await capture(pm,'05','助理·过程管家',()=>assist(pm,'过程管家','过程核对'))
  await capture(pm,'04','助理·组合周报',()=>assist(pm,'过程管家','过程核对',true))
  await capture(pm,'06','助理·立项评审',()=>assist(pm,'立项评审','立项材料与评分'))
  await capture(pm,'10','报告详情和事实核验',async () => {
    const article=await report(pm,'日报')
    await button(article,'Fact 证据与澄清').click()
    await expect(article.getByText('已核验 · 中可信',{exact:true})).toBeVisible()
    await button(article,'核验历史').click()
    return article
  })
  await capture(pm,'12','新建补强任务',click(pm,'tasks','新建补强任务'))
  await capture(pm,'17','修改里程碑预测',click(pm,'milestones','修改预测'))
  await capture(pm,'19','需求变更历史',click(pm,'requirements','需求变更历史','.el-dialog'))
  await pm.keyboard.press('Escape')
  await capture(pm,'20','标记需求交付',click(pm,'requirements','标记交付','.el-dialog'))
  await pm.keyboard.press('Escape')
  await capture(pm,'26','成本首次预算分配',click(pm,'costs','申请首次预算分配'))
  await capture(pm,'27','成本对账范围',async () => {
    const pane=await projectPage(pm,'costs')
    const cancel=button(pane,'取消')
    if (await cancel.isVisible()) await cancel.click()
    await pane.getByLabel('对账范围').selectOption('categories')
    return pane
  })
  await capture(pm,'30','评审工作台',async () => {
    const pane=await projectPage(pm,'reviews')
    await pane.getByRole('button',{name:/^(打开(评审)?)?工作台$/}).first().click()
    return pane.getByRole('region',{name:'关口人工工作台'})
  })
  await capture(pm,'R5-03','成本超支重估依据与决议',async () => {
    const pane=await projectPage(pm,'tasks')
    const cancel=button(pane,'取消')
    if (await cancel.isVisible()) await cancel.click()
    await button(pane.getByRole('row').filter({hasText:'复评继续投入（金额）'}),'查看重估').click()
    return pane.locator('.reassessment').first()
  })
  await capture(pm,'47','需求例外',project(pm,'exceptions',projects.exceptions))
  await capture(pm,'R5-01','周报人天分类',async () => {
    const pane=await projectPage(pm,'reports')
    await button(pane,'新建报告草稿').click()
    await pane.getByLabel('填写人天分类').check()
    await expect(pane.getByLabel('研发人天')).toBeVisible()
    return pane
  })
  const cancelReport=button(pm.locator('#project-pane-reports'),'取消')
  if (await cancelReport.isVisible()) await cancelReport.click()
  await capture(pm,'R5-04','正式周报和送达记录',async () => {
    const article=await report(pm,'周报')
    await expect(article.getByText(/送达/).first()).toBeVisible()
    return article
  })
  await desktop.close()

  const adminContext=await browser.newContext({...options, viewport:{width:1440,height:930}})
  const admin=await visited(adminContext,'initiator')
  await adminViews(admin)
  await adminContext.close()

  for (const width of [1280,1024,820,390]) {
    const context=await browser.newContext({...options, viewport:{width,height:900}})
    const page=await visited(context,'pm')
    await page.goto(`${url.origin}/#project=${projects.execution}&tab=overview`)
    for (const key of projectNav) {
      await capture(page,`${width}-${key}`,key,project(page,key),{file:width===390})
    }
    if (width===390) {
      await capture(page,'390-navigation','移动导航打开',async () => {
        await button(page,'打开导航').click()
        const dialog=page.getByRole('dialog',{name:'工作台导航'})
        await expect(button(dialog,'关闭导航')).toBeFocused()
        return dialog
      })
      await page.keyboard.press('Escape')
      await expect(button(page,'打开导航')).toBeFocused()
      await capture(page,'390-delivery-dialog','移动端表格滚动与交付弹窗',async () => {
        const pane=await projectPage(page,'requirements')
        const scroller=pane.locator('.el-table__body-wrapper .el-scrollbar__wrap').first()
        await scroller.evaluate(el=>{el.scrollLeft=el.scrollWidth})
        await expect.poll(()=>scroller.evaluate(el=>el.scrollLeft)).toBeGreaterThan(0)
        await button(pane,'标记交付').click()
        const dialog=page.getByRole('dialog',{name:'标记交付'})
        await expect(button(dialog,'取消')).toBeInViewport()
        return dialog
      })
      await page.keyboard.press('Escape')
    }
    await capture(page,`${width}-exceptions`,'需求例外',project(page,'exceptions',projects.exceptions),{file:width===390})
    for (const [,key,name] of personalNav) {
      await capture(page,`${width}-${key}`,name,()=>globalPage(page,key,name),{file:width===390})
    }
    await context.close()

    const supporting=await browser.newContext({...options,viewport:{width,height:900}})
    const supportingPage=await visited(supporting)
    await authViews(supportingPage,`${width}-`,{file:width===390})
    await authenticate(supporting,'initiator')
    await adminViews(supportingPage,`${width}-`,{file:width===390})
    await supporting.close()
  }
}

async function zoomChecks() {
  const profile=await mkdtemp(join(tmpdir(),'pmo-r5-edge-'))
  const extension=join(profile,'zoom')
  await mkdir(extension)
  await writeFile(join(extension,'manifest.json'),JSON.stringify({name:'PMO Edge layout checks',version:'1.0',
    manifest_version:3, permissions:['tabs'], host_permissions:[`${url.origin}/*`], background:{service_worker:'worker.js'}}))
  await writeFile(join(extension,'worker.js'),'chrome.runtime.onInstalled.addListener(() => {})')
  const context=await chromium.launchPersistentContext(profile,{...options,headless:true,
    viewport:{width:1440,height:930},args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]})
  try {
    const worker=context.serviceWorkers()[0]??await context.waitForEvent('serviceworker',{timeout:12000})
    const page=context.pages()[0]
    watch(page)
    await page.goto(`${url.origin}/#project=${projects.execution}&tab=overview`)
    for (const zoom of [1,1.25,1.5]) {
      const actual=await worker.evaluate(async ({zoom,origin})=>{
        const [tab]=await chrome.tabs.query({url:`${origin}/*`})
        await chrome.tabs.setZoom(tab.id,zoom)
        return chrome.tabs.getZoom(tab.id)
      },{zoom,origin:url.origin})
      expect(actual).toBe(zoom)
      await expect.poll(()=>page.evaluate(()=>devicePixelRatio)).toBe(zoom)
      const prefix=`zoom-${zoom*100}-`
      await context.clearCookies()
      await authViews(page,prefix,{file:false,zoom:zoom*100})
      await authenticate(context,'initiator')
      await adminViews(page,prefix,{file:false,zoom:zoom*100})
      await authenticate(context,'pm')
      for (const key of projectNav) {
        await capture(page,`zoom-${zoom*100}-${key}`,key,project(page,key),{file:key==='costs',zoom:zoom*100})
      }
      await capture(page,`zoom-${zoom*100}-exceptions`,'需求例外',project(page,'exceptions',projects.exceptions),{file:false,zoom:zoom*100})
      for (const [,key,name] of personalNav) {
        await capture(page,`zoom-${zoom*100}-${key}`,name,()=>globalPage(page,key,name),{file:false,zoom:zoom*100})
      }
    }
  } finally {
    await context.close()
  }
}

try {
  await main()
  await zoomChecks()
} finally {
  await browser.close()
  await writeFile(join(output,'checks.json'),JSON.stringify(result,null,2)+'\n')
  const pictures=result.checks.filter(check=>check.file).map(check=>(
    `<figure><a href="${check.file}"><img loading="lazy" src="${check.file}" alt="${check.description}"></a><figcaption>${check.id} · ${check.description}</figcaption></figure>`
  ))
  await writeFile(join(output,'index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PMO R5 · Edge 截图</title>
<style>body{font:14px/1.6 "Microsoft YaHei UI",sans-serif;color:#1f2937;background:#f5f7fb;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:12px}figure{margin:0;background:#fff;border:1px solid #e2e8f0;border-radius:7px;overflow:hidden}img{display:block;width:100%;height:210px;object-fit:contain;object-position:top;background:#eef2f8}figcaption{padding:8px 12px}</style>
<h1>PMO R5 实际 Edge 截图</h1><p>隔离合成数据 · <a href="checks.json">布局检查结果</a></p><main>${pictures.join('\n')}</main></html>`)
}
console.log(JSON.stringify({total:result.checks.length, failed:result.checks.filter(c=>c.status!=='PASS').length, browserErrors:result.browserErrors.length}))
expect(result.checks.filter(c=>c.status!=='PASS')).toEqual([])
expect(result.browserErrors).toEqual([])
