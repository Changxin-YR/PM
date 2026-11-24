import {chromium, expect} from '@playwright/test'

const url = new URL(process.env.PMO_BROWSER_URL)
const sessions = JSON.parse(process.env.PMO_TEST_SESSIONS)
const sampleAt = process.env.PMO_TEST_SAMPLE
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform === 'win32'?'msedge':'chromium')})
const exact = name => ({name,exact:true})
const button = (scope,name) => scope.getByRole('button',exact(name))
const row = (page,code) => page.getByRole('row').filter({has:page.getByRole('cell',exact(code))})

async function actor(role,width){
  const context = await browser.newContext({viewport:{width,height:850}})
  const {cookie,csrf}=sessions[role]
  await context.addCookies([{name:'pmo_session',value:cookie,domain:url.hostname,path:'/api/v1',httpOnly:true,sameSite:'Strict'}])
  await context.addInitScript(token => sessionStorage.setItem('pmo.csrf',token),csrf)
  const page=await context.newPage();await page.goto(url.href)
  return page
}

try{
  const pm=await actor('pm',1280)
  const shared=row(pm,'REVIEWS'),own=row(pm,'DASH-OWN'),hidden=row(pm,'DASH-HIDDEN')
  await expect(pm.getByText('已授权项目 · 2',{exact:true})).toBeVisible()
  await expect(shared.getByRole('cell',exact('pm'))).toBeVisible()
  await expect(shared.locator(`time[datetime="${sampleAt}"]`)).toBeVisible()
  await expect(shared.getByText('正式快照',{exact:true})).toBeVisible()
  await expect(own.getByRole('cell',exact('无采样'))).toBeVisible()
  await expect(button(own,'修改草稿名称')).toBeVisible()
  await expect(hidden).toHaveCount(0)

  const filter=pm.getByRole('combobox',exact('筛选项目阶段'))
  await expect(filter).toBeVisible()
  await pm.getByLabel('搜索项目名称或编号').fill('Synthetic review')
  const [filtered]=await Promise.all([pm.waitForResponse(response => {
    const request=new URL(response.url())
    return request.pathname==='/api/v1/project-overview'&&request.searchParams.get('filters[stage]')==='executing'
      &&request.searchParams.get('filters[keyword]')==='Synthetic review'
  }),filter.selectOption('executing')])
  expect((await filtered.json()).data.total).toBe(1)
  await expect(pm.getByText('筛选结果 · 1',{exact:true})).toBeVisible()
  await expect(own).toHaveCount(0)
  await expect(shared.getByRole('cell',exact('执行中'))).toBeVisible()
  await expect(button(shared,'修改草稿名称')).toHaveCount(0)
  await button(shared,'查看 Synthetic review 的项目资料').click()
  const overview=pm.getByRole('region',exact('项目概览'))
  await expect(overview.getByRole('region',exact('立项概要')).getByText('Synthetic client',{exact:true})).toBeVisible()
  const indicators=overview.getByRole('region',exact('当前四指标'))
  await expect(indicators.getByText('数据不可信',{exact:true})).toBeVisible()
  await expect(indicators.locator('dd')).toHaveText(['不可用','不可用','不可用','不可用'])
  await expect(overview.getByRole('region',exact('最近阶段活动')).getByRole('listitem')).toHaveCount(4)
  const activity=overview.getByRole('region',exact('最近台账活动'))
  await expect(activity.getByRole('listitem')).toHaveCount(1)
  await expect(activity.getByText('风险已登记',{exact:true})).toBeVisible()
  await button(indicators,'三线健康').click()
  const health=pm.getByRole('region',exact('三线健康'))
  await expect(health.getByText('暂停正式发布',{exact:true})).toBeVisible()
  await button(health,'记录本次快照').click()
  await expect(health.getByRole('status')).toHaveText('已记录本次计算。')
  await pm.reload()
  await expect(shared.getByText('数据不可信',{exact:true})).toBeVisible()
  await expect(shared.getByText('暂缓发布',{exact:true})).toBeVisible()
  await expect(shared.locator(`time[datetime="${sampleAt}"]`)).toHaveCount(0)
  await filter.selectOption('terminated')
  await expect(pm.getByText('没有符合条件的授权项目',{exact:true})).toBeVisible()

  const applicant=await actor('initiator',460)
  const limited=row(applicant,'REVIEWS')
  await expect(limited.getByRole('cell',exact('不可见'))).toHaveCount(2)
  await expect(limited.locator('time')).toHaveCount(0)
  await expect(button(limited,'修改草稿名称')).toHaveCount(0)
  await expect(row(applicant,'DASH-OWN')).toHaveCount(0)
  await expect(applicant.getByRole('combobox',exact('筛选项目阶段'))).toBeInViewport()
  await button(limited,'查看 Synthetic review 的项目资料').click()
  const minimal=applicant.getByRole('region',exact('项目概览'))
  await expect(minimal.getByText('当前账号仅可查看项目基本信息。',{exact:true})).toBeVisible()
  const reminders=minimal.getByRole('region',exact('我的项目提醒'))
  await expect(reminders.getByText('项目成本超过规则阈值 · LR-04',{exact:true})).toBeVisible()
  await expect(reminders.getByText('待处理',{exact:true})).toBeVisible()
  await expect(minimal.getByRole('region',exact('当前四指标'))).toHaveCount(0)
  await expect(minimal.getByRole('region',exact('最近台账活动'))).toHaveCount(0)
}finally{await browser.close()}
