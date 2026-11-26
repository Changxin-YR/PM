import {chromium, expect} from '@playwright/test'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {recoverWrite} from './write-recovery.mjs'

const url = process.env.PMO_BROWSER_URL
const sessions = JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const projectName = 'Synthetic review'
const exact = name => ({name, exact:true})
const button = (scope, name) => scope.getByRole('button', exact(name))
const dialog = (page, name) => page.getByRole('dialog', exact(name))
const project = page => button(page, '查看 '+projectName+' 的项目资料')
const drawer = page => page.getByRole('region',exact(projectName+' · 项目资料'))

async function closeDialog(panel) {
  await button(panel,'关闭此对话框').click()
  await expect(panel).toBeHidden()
}

async function closeWorkbench(panel) {
  await button(panel,'返回评审记录').click()
  await expect(panel).toBeHidden()
}

async function actor(role, requiresProject=true) {
  const context = await browser.newContext({viewport:{width:1280,height:820}})
  const session = sessions[role]
  await context.addCookies([{name:'pmo_session', value:session.cookie, domain:new URL(url).hostname, path:'/api/v1', httpOnly:true, sameSite:'Strict'}])
  await context.addInitScript(csrf=>sessionStorage.setItem('pmo.csrf',csrf), session.csrf)
  const page = await context.newPage()
  await page.goto(url)
  await expect(requiresProject?project(page):button(page,'我的终止确认')).toBeVisible()
  return page
}

async function open(page, name) {
  const panel=drawer(page)
  if(!await panel.isVisible()) await project(page).click()
  await panel.getByRole('tab',exact(name)).click()
  return panel
}

async function write(page, method, pattern, control, versioned=true) {
  const endpoint = new RegExp(pattern+'$')
  const waiting = page.waitForResponse(r=>r.request().method()===method&&endpoint.test(new URL(r.url()).pathname))
  await control.click()
  const response = await waiting
  const requestHeaders = response.request().headers()
  expect(requestHeaders['idempotency-key']).toBeTruthy()
  if(versioned) expect(requestHeaders['if-match']).toBeTruthy()
  const body=await response.json()
  expect(response.status(),JSON.stringify(body)).toBe(200)
  return body.data
}

async function requestStage(page, name, next, interrupt=false) {
  const panel=await open(page,'阶段流转')
  await button(panel,'申请阶段流转').click()
  const form=dialog(page,'申请阶段流转')
  await form.getByLabel('目标阶段',{exact:true}).selectOption(next)
  await form.getByLabel('申请原因',{exact:true}).fill(name)
  const state=interrupt?await recoverWrite(page,'/transition-requests',form,'提交流转申请',
    [button(form,'取消'),form.getByLabel('申请原因',{exact:true}),button(panel,'刷新阶段流转')],'network'):
    await write(page,'POST','/transition-requests',button(form,'提交流转申请'))
  expect(state).toMatchObject({toStage:next,status:'pending'})
  await expect(form).toBeHidden()
  return panel
}

async function directStage(page, reason, next, label) {
  const panel=await requestStage(page,reason,next)
  await button(panel,'执行阶段流转').click()
  const state=await write(page,'POST','/projects/[^/]+/transitions',button(dialog(page,'执行阶段流转'),'确认执行流转'))
  expect(state).toMatchObject({toStage:next,status:'applied'})
  await expect(panel.getByLabel('项目生命周期').getByText('当前阶段：'+label,{exact:true})).toBeVisible()
}

async function reviewGate(sender, reviewer, label, stage, points, anchor, kind) {
  const gate = await open(sender,'关口送审')
  await button(gate,'重读阶段与材料').click()
  await expect(gate.getByText(`${label}预检 · 材料齐备`,{exact:true})).toBeVisible()
  await button(gate,`提交${label}评审`).click()
  const receipt = await write(sender,'POST','/projects/[^/]+/reviews',button(dialog(sender,'确认送审'),'确认送审'))
  if(kind==='charter'){
    const row=gate.getByRole('row').filter({has:sender.getByRole('cell',exact(receipt.reviewId))})
    await button(row,'打开评审工作台').click()
    const ownerWorkbench=sender.getByRole('region',exact('关口人工工作台'))
    const meeting=ownerWorkbench.getByRole('region',exact('评审会议资料'))
    await button(meeting,'安排评审会议').click()
    await meeting.getByLabel('会议时间（北京时间）',{exact:true}).fill('2026-11-28T09:30')
    const attendees=meeting.getByLabel('参会人',{exact:true})
    const attendeeId=await attendees.locator('option').filter({hasText:/^reviewer · /}).getAttribute('value')
    await attendees.selectOption(attendeeId)
    const scheduled=await write(sender,'PUT','/reviews/[^/]+/meeting',button(meeting,'保存会议资料'))
    expect(Date.parse(scheduled.scheduledAt)).toBe(Date.parse('2026-11-28T01:30:00Z'))
    expect(scheduled.attendeeIds).toEqual([attendeeId])
    await closeWorkbench(ownerWorkbench)
  }
  await reviewer.reload()
  const panel = await open(reviewer,'关口送审')
  const row = panel.getByRole('row').filter({has:reviewer.getByRole('cell',exact(receipt.reviewId))})
  const loaded = reviewer.waitForResponse(r=>new URL(r.url()).pathname.endsWith(`/reviews/${receipt.reviewId}/workbench`))
  await button(row,'打开评审工作台').click()
  const state = (await (await loaded).json()).data
  const workbench = reviewer.getByRole('region',exact('关口人工工作台'))
  if(kind==='charter'){
    const meeting=workbench.getByRole('region',exact('评审会议资料'))
    await expect(meeting.locator('time')).toHaveAttribute('datetime','2026-11-28T01:30:00Z')
    await expect(button(meeting,'安排评审会议')).toHaveCount(0)
  }
  await button(workbench,'填写评分').click()
  for (const dimension of state.dimensions) {
    const fields = workbench.getByRole('group',exact(dimension.label))
    await fields.getByLabel('评分',{exact:true}).selectOption(points)
    await fields.getByLabel('评分锚点',{exact:true}).selectOption(anchor)
    await fields.getByLabel('评分理由',{exact:true}).fill(dimension.key==='customerAttractiveness'?
      '尚无意向。仍需人工核对其他吸引力依据。':`独立复核合成材料：${dimension.label}。`)
    await fields.getByText(/^材料引用（至少一项）/).click()
    await fields.getByRole('checkbox',exact(state.materialRefs.find(ref=>ref.startsWith(kind+':')))).check()
    if(dimension.criteria.some(item=>item.minimumScore<=Number(points))){
      await fields.getByLabel('保留分数的理由',{exact:true}).fill('合成材料需后续补齐该判断的独立依据。')
    }
  }
  if(kind==='charter'){
    const customer=workbench.getByRole('group',exact('客户/市场吸引力'))
    await button(workbench,'核对评分理由').click()
    const conflict=customer.getByText('理由与锚点存在明确冲突',{exact:true})
    await expect(conflict).toBeVisible()
    await expect(customer.getByText('材料原话「尚无意向」',{exact:true})).toBeVisible()
    await reviewer.evaluate(()=>window.scrollTo(0,0))
    const screenshots=process.env.PMO_R5_REVIEW_SCREENSHOTS??fileURLToPath(new URL('../../.local/r5-review-screenshots/',import.meta.url))
    await reviewer.screenshot({
      path:path.join(screenshots,'02-评分锚点核对.png'),fullPage:true,
    })
  }
  await write(reviewer,'POST','/reviews/[^/]+/scores',button(workbench,'提交评分'))
  await button(workbench,'登记裁决').click()
  await workbench.getByLabel('裁决',{exact:true}).selectOption('pass')
  const rationale = `独立复核${label}材料，合成验证通过。`
  await workbench.getByLabel('裁决理由',{exact:true}).fill(rationale)
  const result = await write(reviewer,'POST','/reviews/[^/]+/decisions',button(workbench,'确认裁决'))
  expect(result.decision).toMatchObject({stage,rationale})
  await expect(workbench.getByText(rationale,{exact:true})).toBeVisible()
  await workbench.getByText('裁决来源',{exact:true}).click()
  await expect(workbench.getByText(`review-decision-snapshot:${result.decision.decisionId}`,{exact:true})).toBeVisible()
}

async function launch(pm) {
  const initiator = await actor('initiator'), reviewer = await actor('reviewer')
  const charter = (await open(initiator,'立项卡')).getByRole('tabpanel',exact('立项卡'))
  for (const [name, value] of Object.entries({'价值主张':'交付完整归档清单','目标客户':'合成验收客户',
    '客户或市场吸引力':'已确认演练范围。尚无意向','预期收益':'交付可独立核验','成功标准':'导出包含来源编号',
    '粗估金额（元）':'100','粗估人天':'10','预计工期（天）':'12'})) {
    await charter.getByLabel(name,{exact:true}).fill(value)
  }
  await button(charter,'添加风险').click()
  await charter.getByLabel('风险描述',{exact:true}).fill('检查交付来源完整性')
  await charter.getByLabel('应对措施',{exact:true}).fill('在项目工作台核验原件')
  await write(initiator,'PUT','/charter',button(charter,'保存立项卡'))
  await reviewGate(initiator,reviewer,'立项','designing','4','between_3_5','charter')

  await pm.reload()
  const dates = JSON.parse(process.env.PMO_TEST_PLANNED_DATES)
  const milestones = (await open(pm,'里程碑')).getByRole('tabpanel',exact('里程碑'))
  for (const [index,date] of dates.entries()) {
    await button(milestones,'新增里程碑').click()
    await milestones.getByLabel('里程碑名称',{exact:true}).fill(`验收步骤 ${index+1}`)
    await milestones.getByLabel('计划日期',{exact:true}).fill(date)
    await milestones.getByLabel('关键里程碑',{exact:true}).check()
    await write(pm,'POST','/projects/[^/]+/milestones',button(milestones,'保存里程碑'),false)
  }
  const requirements = (await open(pm,'需求')).getByRole('tabpanel',exact('需求'))
  await button(requirements,'新增需求').click()
  await requirements.getByLabel('需求描述',{exact:true}).fill('导出完整交付清单和验收回执编号')
  await requirements.getByLabel('验收标准',{exact:true}).fill('逐条对照原件与回执')
  await requirements.getByLabel('优先级',{exact:true}).selectOption('P0')
  await requirements.getByLabel('计划确认日期',{exact:true}).fill(dates.at(-1))
  await write(pm,'POST','/projects/[^/]+/requirements',button(requirements,'保存需求'),false)

  const costs = (await open(pm,'成本')).getByRole('tabpanel',exact('成本'))
  for (const [name, budget] of [['金额（元）','100'],['人天','10']]) {
    await button(costs.getByRole('region',exact(name)),'修改预算草稿').click()
    await costs.getByLabel(`${name} · 预算草稿`,{exact:true}).fill(budget)
    await write(pm,'PUT','/cost-budgets',button(costs,'保存成本'))
  }
  const risks = (await open(pm,'风险')).getByRole('tabpanel',exact('风险'))
  await button(risks,'登记风险').click()
  await risks.getByLabel('风险描述',{exact:true}).fill('交付原件须由独立人工核验')
  await risks.getByLabel('应对措施',{exact:true}).fill('取得核验证据后由 Reviewer 复核关闭')
  const recordedRisk=await write(pm,'POST','/projects/[^/]+/risks',button(risks,'保存风险'),false)
  expect(recordedRisk.isBlocking).toBe(false)
  await button(risks,'升级为阻塞').click()
  await risks.getByLabel('升级原因',{exact:true}).fill('交付原件暂缺，已阻塞验收准备')
  const promotedRisk=await write(pm,'POST','/risks/[^/]+/escalate',button(risks,'确认升级为阻塞'))
  expect(promotedRisk).toMatchObject({riskId:recordedRisk.riskId,isBlocking:true,status:'open',revision:2})
  await pm.reload()
  const refreshedRisks=(await open(pm,'风险')).getByRole('tabpanel',exact('风险'))
  const visibleRisk=refreshedRisks.getByRole('row').filter({hasText:'交付原件须由独立人工核验'})
  await expect(visibleRisk.getByText('阻塞',{exact:true})).toBeVisible()
  await expect(visibleRisk.locator('time')).toHaveAttribute('datetime',promotedRisk.blockedSince)

  const customer = await actor('client_representative')
  for (const [page, name, result] of [[pm,'PM 确认基线','awaiting_customer'],[customer,'客户确认并冻结基线','frozen']]) {
    const basis = (await open(page,'需求基线')).getByRole('tabpanel',exact('需求基线'))
    await basis.getByLabel('确认说明',{exact:true}).fill('已逐条核对合成需求和验收标准。')
    await basis.getByLabel('已核对本版本的全部需求和验收标准',{exact:true}).check()
    const signed = await write(page,'POST','/requirement-baseline/sign',button(basis,name))
    expect(signed.status).toBe(result)
  }
  await reviewGate(pm,reviewer,'落地','executing','3','score_3','milestone')

  await pm.reload()
  const assistant = (await open(pm,'项目助理')).getByRole('region',exact('项目助理'))
  await assistant.getByText('过程管家',{exact:true}).click()
  await expect(assistant.getByText('模型关闭',{exact:true})).toBeVisible()
}

try {
  const pm=await actor('pm')
  if(process.env.PMO_TEST_PHASE==='launch') {
    await launch(pm)
  } else if(process.env.PMO_TEST_PHASE==='delivery') {
    await directStage(pm,'有效里程碑均已完工，并已核验交付凭证。','delivering','交付验收')
  } else if(process.env.PMO_TEST_PHASE==='withdrawal') {
    const original=process.env.PMO_TEST_REVIEW_ID
    const reason='补核原交付材料后，重新提交结项评审。'
    async function openOriginal() {
      const panel=await open(pm,'关口送审')
      const row=panel.getByRole('row').filter({has:pm.getByRole('cell',exact(original))})
      await button(row,'打开评审工作台').click()
      return pm.getByRole('region',exact('关口人工工作台'))
    }
    const workbench=await openOriginal()
    await button(workbench,'撤回评审').click()
    await workbench.getByLabel('撤回原因',{exact:true}).fill(reason)
    const withdrawn=await write(pm,'POST','/reviews/[^/]+/withdraw',button(workbench,'确认撤回'))
    expect(withdrawn).toMatchObject({review:{reviewId:original,verdict:'withdrawn'},withdrawalReason:reason})
    await pm.reload()
    const history=await open(pm,'阶段流转')
    await expect(history.getByText('当前阶段：执行中',{exact:true})).toBeVisible()
    const reopened=await openOriginal()
    await expect(reopened.getByText(reason,{exact:true})).toBeVisible()
    await closeWorkbench(reopened)
    await directStage(pm,'已重新核对里程碑完成凭证。','delivering','交付验收')
    const ready=await open(pm,'关口送审')
    await button(ready,'提交结项评审').click()
    const receipt=await write(pm,'POST','/projects/[^/]+/reviews',button(dialog(pm,'确认送审'),'确认送审'))
    expect(receipt.reviewId).not.toBe(original)
  } else if(process.env.PMO_TEST_PHASE==='exception') {
    const command=JSON.parse(process.env.PMO_TEST_REQUIREMENT_EXCEPTION)
    const customer=await actor('client_representative'), reviewer=await actor('reviewer')
    const chain=JSON.parse(process.env.PMO_TEST_CLOSEOUT??'null')
    const read=async(page,path)=>{
      const response=await page.request.get(new URL('api/v1/'+path,url).href)
      expect(response.status()).toBe(200)
      return (await response.json()).data
    }
    const direct=async(page,role,path,revision,data)=>page.request.post(new URL('api/v1/'+path,url).href,{
      headers:{'X-CSRF-Token':sessions[role].csrf,'If-Match':String(revision),'Idempotency-Key':crypto.randomUUID()},data,
    })
    const gaps=async()=>new Set((await read(pm,chain.project+'/closing/check')).missing.map(x=>x.code))
    if(chain){
      expect([...(await gaps())]).toEqual(expect.arrayContaining(['COST_RECONCILIATION_REQUIRED','BLOCKING_RISK_UNRESOLVED']))
      const reconciler=await actor('outsider'), otherTab=await actor('outsider')
      const costs=await open(reconciler,'成本'), otherCosts=await open(otherTab,'成本')
      for(const [page,panel] of [[reconciler,costs],[otherTab,otherCosts]]){
        await button(panel,'签署对账').click()
        await dialog(page,'签署成本对账').getByRole('checkbox',{name:new RegExp(chain.costProof)}).check()
      }
      const otherSigning=dialog(otherTab,'签署成本对账')
      const signed=await recoverWrite(otherTab,'/cost-reconciliations',otherSigning,'确认签署对账',
        [button(otherSigning,'取消'),otherSigning.getByLabel('金额（元）超支归因',{exact:true})],{status:502})
      expect(signed.lines.map(x=>[x.type,x.actual])).toEqual([['money','100.00'],['effort','10.00']])
      const signing=dialog(reconciler,'签署成本对账')
      const blocked=reconciler.waitForResponse(x=>x.request().method()==='POST'&&x.url().endsWith('/cost-reconciliations'))
      await button(signing,'确认签署对账').click()
      expect((await blocked).status()).toBe(409)
      await expect(button(signing,'确认签署对账')).toBeDisabled()
      await expect(signing.getByText('版本已变化，请重新读取后再保存。',{exact:true})).toBeVisible()
      await expect(signing.getByText('成本快照或版本已变化，请关闭后重新发起签署。',{exact:true})).toBeVisible()
      await closeDialog(signing)
      expect((await read(reconciler,chain.project+'/cost-reconciliations')).current.receiptId).toBe(signed.receiptId)
      const denied=await direct(pm,'pm',chain.project+'/cost-reconciliations',signed.revision,
        {costHash:signed.costHash,evidenceRefs:[chain.costProof]})
      expect(denied.status()).toBe(403)

      const riskPath=`risks/${chain.risk}`, receiver=await actor('receiver',false)
      const scope='Independent acceptance of the synthetic delivery follow-up'
      const riskPanel=await open(reviewer,'风险')
      await button(riskPanel,'风险处置').click()
      const resolution=dialog(reviewer,'风险处置')
      await button(resolution,'发出接收请求').click()
      await resolution.getByLabel('接收人 ID',{exact:true}).fill(chain.receiver)
      await resolution.getByLabel('接收范围',{exact:true}).fill(scope)
      await resolution.getByRole('checkbox',{name:new RegExp(chain.riskProof)}).check()
      const request=await recoverWrite(reviewer,'/handoff/acceptance',resolution,'提交接收请求',
        [button(resolution,'取消'),button(resolution,'刷新风险处置'),resolution.getByLabel('接收范围',{exact:true})],'network')
      expect(request.kind).toBe('requested')
      expect((await gaps()).has('BLOCKING_RISK_UNRESOLVED')).toBe(true)
      const binding={receiverPersonId:chain.receiver,acceptanceEvidenceRef:chain.riskProof}
      const premature=await direct(reviewer,'reviewer',riskPath+'/handoff',request.riskRevision,binding)
      expect((await premature.json()).code).toBe('RISK_ACCEPTANCE_REQUIRED')
      const deniedReceiver=await direct(customer,'client_representative',riskPath+'/handoff/acceptance',request.riskRevision,
        {...binding,kind:'confirm',decision:'accept',acceptanceScope:scope})
      expect(deniedReceiver.status()).toBe(403)
      const limited=await read(receiver,riskPath+'/resolution')
      expect(limited.audience).toBe('receiver')
      expect(limited.risk.statement).toBeUndefined()
      await button(receiver,'风险接收').click()
      await receiver.getByLabel('接收请求编号',{exact:true}).fill(`risk-request:${chain.risk}:${request.acceptanceId}`)
      await button(receiver,'打开接收请求').click()
      const incoming=dialog(receiver,'风险接收请求')
      await button(incoming,'本人认证接收').click()
      const accepted=await write(receiver,'POST','/handoff/acceptance',button(incoming,'确认本人接收'))
      expect(accepted.kind).toBe('receiver_confirmed')
      expect((await gaps()).has('BLOCKING_RISK_UNRESOLVED')).toBe(true)
      await button(resolution,'刷新风险处置').click()
      await button(resolution,'复核签署移交').click()
      const handoff=await write(reviewer,'POST','/handoff',button(resolution,'确认签署移交'))
      expect(handoff).toMatchObject({status:'transferred',acceptanceId:accepted.acceptanceId})
      await closeDialog(resolution)
      const after=await gaps()
      expect(after.has('BLOCKING_RISK_UNRESOLVED')).toBe(false)
      expect(after.has('COST_RECONCILIATION_REQUIRED')).toBe(false)
    }
    const panel=await open(customer,'需求例外')
    const deviation=command.items[0].deviation
    await panel.getByLabel('具体接受的偏差',{exact:true}).fill(deviation)
    const local=await customer.evaluate(iso=>{
      const date=new Date(iso)
      return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16)
    },command.expiresAt)
    await panel.getByLabel('接受有效截至（本地时间）',{exact:true}).fill(local)
    await panel.getByRole('checkbox',{name:new RegExp(command.evidenceRef)}).check()
    await panel.getByLabel('我作为客户代表明确接受所列需求偏差。',{exact:true}).check()
    const receipt=chain ? await recoverWrite(customer,'/requirement-exceptions',panel,'签署书面例外',
      [button(panel,'重读需求例外'),panel.getByLabel('接受有效截至（本地时间）',{exact:true})],
      {status:409,json:{code:'REQUEST_IN_PROGRESS'}}) :
      await write(customer,'POST','/requirement-exceptions',button(panel,'签署书面例外'))
    expect(receipt.items[0].deviation).toBe(deviation)
    await customer.reload()
    await expect((await open(customer,'需求例外')).getByText('当前有效的客户书面接受',{exact:true})).toBeVisible()

    const gatePanel=await open(pm,'关口送审')
    await expect(gatePanel.getByText('结项预检 · 材料齐备',{exact:true})).toBeVisible()
    await button(gatePanel,'提交结项评审').click()
    await write(pm,'POST','/projects/[^/]+/reviews',button(dialog(pm,'确认送审'),'确认送审'))
    const openClosing=async()=>{
      const reviewing=await open(reviewer,'关口送审')
      await button(reviewing.getByRole('row').filter({has:reviewer.getByRole('cell',exact('结项'))}),'打开评审工作台').click()
      return reviewer.getByRole('region',exact('关口人工工作台'))
    }
    const workbench=await openClosing()
    await expect(workbench.getByText(deviation,{exact:true})).toBeVisible()
    await button(workbench,'登记裁决').click()
    const judgment=workbench.getByLabel('裁决',{exact:true})
    await expect(judgment.locator('option[value=pass]')).toBeDisabled()
    await judgment.selectOption('conditional_pass')
    const reason='独立核验客户书面接受的具体需求偏差，成本与风险仍按正常结项规则检查。'
    await workbench.getByLabel('裁决理由',{exact:true}).fill(reason)
    if(chain?.revoke){
      const response=await direct(customer,'client_representative',
        `${chain.project}/requirement-exceptions/${receipt.exceptionId}/revoke`,receipt.revision,{reason:'Synthetic withdrawal after review opened'})
      expect(response.status()).toBe(200)
      const blocked=reviewer.waitForResponse(x=>x.request().method()==='POST'&&x.url().endsWith('/decisions'))
      await button(workbench,'确认裁决').click()
      const rejection=await blocked
      expect(rejection.status()).toBe(409)
      expect((await rejection.json()).code).toBe('REVIEW_SNAPSHOT_STALE')
      await expect(button(workbench,'登记裁决')).toHaveCount(0)
      await expect(workbench.getByText('送审所依据的材料或授权已变化。',{exact:true})).toBeVisible()
    }else{
    const result=chain ? await recoverWrite(reviewer,'/reviews/[^/]+/decisions',workbench,'确认裁决',
      [button(workbench,'取消'),button(workbench,'重读工作台'),judgment],'network') :
      await write(reviewer,'POST','/reviews/[^/]+/decisions',button(workbench,'确认裁决'))
    expect(result.decision).toMatchObject({stage:'closed',rationale:reason,option:'conditional_pass'})
    expect(result.requirementException.receiptHash).toBe(receipt.receiptHash)
    await reviewer.reload()
    const history=await openClosing()
    await expect(history.getByText('裁决接受的具体需求偏差',{exact:true})).toBeVisible()
    await expect(history.getByText(deviation,{exact:true})).toBeVisible()
    await expect(history.getByText(reason,{exact:true})).toBeVisible()
    }
  } else if(process.env.PMO_TEST_PHASE==='tasks_reports') {
    const panel=await open(pm,'补强任务')
    const title='按复核记录更新交付清单'
    await button(panel,'新建补强任务').click()
    await panel.getByLabel('标题',{exact:true}).fill(title)
    await panel.getByRole('combobox',exact('来源记录')).click()
    await pm.getByRole('option',{name:/^落地评审 · [0-9a-f]{8}$/}).click()
    const task=await write(pm,'POST','/tasks',button(panel,'保存任务'))
    expect(task).toMatchObject({status:'todo',sourceType:'review',title})
    const row=panel.getByRole('row').filter({hasText:title})
    await button(row,'编辑任务').click()
    await panel.getByRole('combobox',exact('任务状态')).selectOption('done')
    const completed=await write(pm,'PATCH','/tasks/[^/]+',button(panel,'保存任务'))
    expect(completed).toMatchObject({taskId:task.taskId,status:'done',revision:task.revision+1})
    await expect(row.getByText('已完成',{exact:true})).toBeVisible()

    const reporting=await open(pm,'日报周报'), note='核对本周材料，未将预估记为实际。'
    await button(reporting,'新建报告草稿').click()
    await reporting.getByLabel('申报人天',{exact:true}).fill('2.50')
    await reporting.getByLabel('补充说明',{exact:true}).fill(note)
    const draft=await write(pm,'POST','/reports/drafts',button(reporting,'保存草稿'),false)
    expect(draft).toMatchObject({status:'draft',structuredFields:{effort_person_days:'2.50'}})
    const report=reporting.getByRole('article',{name:'报告详情'})
    const submitted=await write(pm,'POST','/reports/[^/]+/submit',button(report,'提交报告'))
    expect(submitted).toMatchObject({reportId:draft.reportId,status:'submitted'})
    await button(report,'确认并发布').click()
    const published=await write(pm,'POST','/reports/[^/]+/confirm',button(pm,'发布报告'))
    expect(published).toMatchObject({reportId:draft.reportId,status:'confirmed',isCurrentConfirmed:true})
    await pm.reload()
    const after=await open(pm,'日报周报')
    await button(after,'查看').click()
    await expect(after.getByRole('article',{name:'报告详情'}).getByText(note,{exact:true})).toBeVisible()
    await expect(after.getByRole('row').getByText('已发布',{exact:true})).toBeVisible()
  } else if(process.env.PMO_TEST_PHASE==='approval-pages') {
    const panel=await open(pm,'阶段流转')
    const history=panel.getByRole('list',{name:'阶段流转记录',exact:true})
    const pages=panel.getByRole('navigation',{name:'阶段流转分页',exact:true})
    const current=panel.getByText('当前阶段：执行中',{exact:true})
    await expect(history.getByRole('listitem')).toHaveCount(20)
    await expect(history.getByText('Synthetic history request 21',{exact:true})).toBeVisible()
    await expect(history.getByText('Synthetic history request 01',{exact:true})).toHaveCount(0)
    await button(pages,'下一页').click()
    await expect(history.getByRole('listitem')).toHaveCount(1)
    await expect(history.getByText('Synthetic history request 01',{exact:true})).toBeVisible()
    await expect(current).toBeVisible()
    await button(panel,'刷新阶段流转').click()
    await expect(history.getByText('Synthetic history request 01',{exact:true})).toBeVisible()
    await requestStage(pm,'Synthetic request from the history page.','on_hold',true)
    await expect(history.getByText('Synthetic request from the history page.',{exact:true})).toBeVisible()
    await expect(current).toBeVisible()
  } else if(process.env.PMO_TEST_PHASE==='formal-authority') {
    const delegate=await actor('outsider'), reviewer=await actor('reviewer'), sponsor=await actor('sponsor')
    const refs=JSON.parse(process.env.PMO_TEST_AUTHORITY_REFS)
    const steps=[['on_hold','已暂停','SYNTHETIC-HOLD'],['executing','执行中','SYNTHETIC-RESUME'],
      ['terminated','已终止','SYNTHETIC-TERMINATE']]
    for (const [next,label,source] of steps) {
      if(next==='terminated'){
        await sponsor.reload()
        await requestStage(sponsor,'Sponsor 的终止申请，保留独立 PM 申请。',next)
      }
      await pm.reload()
      const reason=`PM 申请：${label}`
      await requestStage(pm,reason,next)
      await reviewer.reload()
      const noGrant=await open(reviewer,'阶段流转')
      await expect(noGrant.getByText(reason,{exact:true})).toBeVisible()
      await expect(button(noGrant,'裁决阶段流转')).toHaveCount(0)
      await delegate.reload()
      const panel=await open(delegate,'阶段流转')
      const pending=panel.getByRole('listitem').filter({hasText:reason})
      await button(pending,'裁决阶段流转').click()
      const form=dialog(delegate,'裁决阶段流转')
      await form.getByLabel('流转裁决',{exact:true}).selectOption('approve')
      const rationale=`依据组织授权已独立核对：${label}`
      await form.getByLabel('裁决理由',{exact:true}).fill(rationale)
      if(next==='on_hold'){
        const local=new Date(Date.now()+(60-new Date().getTimezoneOffset())*60_000).toISOString().slice(0,16)
        await form.getByLabel('暂停至（本地时间）',{exact:true}).fill(local)
      }
      const outcome=next==='on_hold'?await recoverWrite(delegate,'/approvals/[^/]+/decide',form,'确认流转裁决',
        [button(form,'取消'),form.getByLabel('裁决理由',{exact:true})],{status:502}):
        await write(delegate,'POST','/approvals/[^/]+/decide',button(form,'确认流转裁决'))
      expect(outcome).toMatchObject({status:'applied',toStage:next,decisionReason:rationale,
        authorityRef:refs[next],authoritySourceRef:source})
      await delegate.reload()
      const history=await open(delegate,'阶段流转')
      await expect(history.getByText(`当前阶段：${label}`,{exact:true})).toBeVisible()
      const row=history.getByRole('listitem').filter({hasText:rationale})
      await row.getByText('记录来源',{exact:true}).click()
      await expect(row.getByText(refs[next],{exact:true})).toBeVisible()
      await expect(row.getByText(source,{exact:true})).toBeVisible()
      await expect(row.getByText('流转后项目版本',{exact:true}).locator('xpath=following-sibling::dd[1]'))
        .toHaveText(String(outcome.revision+1))
      await expect(row.getByText(outcome.resolvedBy,{exact:true})).toBeVisible()
    }
  } else if(process.env.PMO_TEST_PHASE==='termination') {
    const requestReason='原实施条件已取消，申请终止落地评审。'
    await requestStage(pm,requestReason,'terminated')
    const owner=await actor('initiator',false), sponsor=await actor('sponsor')
    const stagePanel=await open(sponsor,'阶段流转')
    await expect(stagePanel.getByText('待发起人确认',{exact:true})).toBeVisible()
    await button(stagePanel,'裁决阶段流转').click()
    const judge=dialog(sponsor,'裁决阶段流转')
    await expect(judge.locator('option[value=approve]')).toBeDisabled()
    await closeDialog(judge)
    await button(owner,'我的终止确认').click()
    const inbox=owner.getByRole('region',exact('我的终止确认'))
    await expect(inbox.getByText(requestReason,{exact:true})).toBeVisible()
    await button(inbox,'处理').click()
    await inbox.getByLabel('确认理由',{exact:true}).fill('作为原发起人，确认不再继续该方案。')
    const signed=await write(owner,'POST','/approvals/[^/]+/confirm-termination',button(inbox,'确认终止申请'))
    expect(signed.awaitsInitiator).toBe(false)
    await expect(inbox.getByText('暂无待确认的终止申请。',{exact:true})).toBeVisible()
    await button(stagePanel,'刷新阶段流转').click()
    await expect(stagePanel.getByText('发起人确认',{exact:true})).toBeVisible()
    await button(stagePanel,'裁决阶段流转').click()
    await judge.getByLabel('流转裁决',{exact:true}).selectOption('approve')
    const reason='已独立核对原发起人确认与对应评审版本。'
    await judge.getByLabel('裁决理由',{exact:true}).fill(reason)
    const stopped=await write(sponsor,'POST','/approvals/[^/]+/decide',button(judge,'确认流转裁决'))
    expect(stopped).toMatchObject({status:'applied',toStage:'terminated',decisionReason:reason})
    await sponsor.reload()
    const history=await open(sponsor,'阶段流转')
    await expect(history.getByText('当前阶段：已终止',{exact:true})).toBeVisible()
    await expect(history.getByText(reason,{exact:true})).toBeVisible()
  } else {
    const reviewer=await actor('reviewer'), reconciler=await actor('outsider')
    const gatePanel=await open(pm,'关口送审')
    await expect(gatePanel.getByText('结项预检 · 存在缺口',{exact:true})).toBeVisible()

    const riskRead=reviewer.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/risks')&&r.request().method()==='GET')
    const risks=await open(reviewer,'风险')
    const riskView=(await (await riskRead).json()).data.items[0]
    const riskTable=risks.getByRole('tabpanel',exact('风险'))
    await expect(riskTable.getByRole('cell',exact(riskView.ownerId))).toBeVisible()
    await expect(riskTable.locator(`time[datetime="${riskView.blockedSince}"]`)).toBeVisible()
    await button(risks,'风险处置').click()
    const resolution=dialog(reviewer,'风险处置')
    await button(resolution,'复核关闭').click()
    await resolution.getByRole('checkbox',{name:new RegExp(process.env.PMO_TEST_RISK_PROOF)}).check()
    const risk=await write(reviewer,'POST','/risks/[^/]+/close',button(resolution,'签署关闭'))
    expect(risk.status).toBe('closed')
    await expect(resolution.getByText('已签署风险关闭。',{exact:true})).toBeVisible()
    await closeDialog(resolution)

    const costs=await open(reconciler,'成本')
    await button(costs,'签署对账').click()
    const signing=dialog(reconciler,'签署成本对账')
    await signing.getByRole('checkbox',{name:new RegExp(process.env.PMO_TEST_COST_PROOF)}).check()
    const reconciliation=await write(reconciler,'POST','/cost-reconciliations',button(signing,'确认签署对账'))
    expect(reconciliation.lines.map(x=>[x.type,x.actual,x.forecastRemaining])).toEqual([['money','100.00','0.00'],['effort','10.00','0.00']])
    await expect(costs.getByText('已签署成本对账。',{exact:true})).toBeVisible()

    await button(gatePanel,'重读阶段与材料').click()
    await expect(gatePanel.getByText('结项预检 · 材料齐备',{exact:true})).toBeVisible()
    await button(gatePanel,'提交结项评审').click()
    await write(pm,'POST','/projects/[^/]+/reviews',button(dialog(pm,'确认送审'),'确认送审'))
    const reviewing=await open(reviewer,'关口送审')
    await button(reviewing.getByRole('row').filter({has:reviewer.getByRole('cell',exact('结项'))}),'打开评审工作台').click()
    const workbench=reviewer.getByRole('region',exact('关口人工工作台'))
    await button(workbench,'登记裁决').click()
    await workbench.getByLabel('裁决',{exact:true}).selectOption('pass')
    const reason='独立核对了验收、双口径财务对账和风险关闭凭据。'
    await workbench.getByLabel('裁决理由',{exact:true}).fill(reason)
    const outcome=await write(reviewer,'POST','/reviews/[^/]+/decisions',button(workbench,'确认裁决'))
    expect(outcome.decision).toMatchObject({stage:'closed',rationale:reason})
    await expect(workbench.getByText(reason,{exact:true})).toBeVisible()
    await closeWorkbench(workbench)

    await pm.reload()
    await directStage(pm,'关闭后的独立项目复盘。','retrospective','复盘')
    const assistant=await open(pm,'项目助理')
    await assistant.getByText('结项回顾',{exact:true}).click()
    const questions=[
      '目标对照：立项卡承诺的收益 / 成本 / 周期，实际各是多少？差异主要原因？',
      '过程表现：三线偏差各发生在哪个阶段？是估算问题、执行问题还是范围问题？',
      '可复用资产：哪些做法 / 模板 / 方案值得沉淀，下次直接用？',
      '教训与建议：如果重来一次，立项和方案阶段分别会做什么不同的事？（记录为教训并标注分类）',
    ]
    for(const question of questions) await expect(assistant.getByText(question,{exact:true})).toBeVisible()
    const chat=assistant.getByRole('region',exact('项目对话'))
    await button(chat,'带我做一次复盘四问').click()
    const written=JSON.parse(process.env.PMO_TEST_ANSWERS)
    for(const [i,text] of written.entries()){
      await chat.getByRole('textbox').fill('答：'+text)
      await button(chat,'提问').click()
      await expect(chat.getByRole('log').locator('article').last()).toContainText(i===3?'复盘草稿已整理':`已记下第 ${i+1} 问`)
    }
    await button(chat,'带入复盘表单').click()
    const retro=drawer(pm).getByRole('region',exact('项目复盘与教训'))
    await expect(retro.getByText('结项复盘',{exact:true})).toBeVisible()
    const answers=dialog(pm,'填写项目复盘')
    for(const [i,q] of questions.entries())await expect(answers.getByLabel(q,{exact:true})).toHaveValue(written[i])
    await expect(retro.getByText('复盘答复尚未登记。',{exact:true})).toBeVisible()
    await write(pm,'PUT','/retrospective',button(answers,'保存复盘'))
    await button(retro,'新增教训草稿').click()
    const lesson=dialog(pm,'新增教训草稿')
    const finding='独立的来源能防止台账自证。'
    for(const [field,value] of Object.entries({'教训分类':'成本治理','发生了什么':finding,'根因':'账务和项目工作台来源独立。','建议':'先核验原件，再支持关口裁决。'})) {
      await lesson.getByLabel(field,{exact:true}).fill(value)
    }
    await write(pm,'POST','/projects/[^/]+/lessons',button(lesson,'保存教训草稿'),false)
    await expect(button(retro,'独立确认教训')).toHaveCount(0)
    await reviewer.reload()
    const peer=await open(reviewer,'复盘与教训')
    await button(peer,'独立确认教训').click()
    await write(reviewer,'POST','/lessons/[^/]+/confirm',button(dialog(reviewer,'独立确认教训'),'确认教训'))
    await expect(peer.getByText('已确认',{exact:true})).toBeVisible()

    const archive=await requestStage(pm,'四问已填写，独立复核人已确认教训。','archived')
    await expect(button(archive,'裁决阶段流转')).toHaveCount(0)
    const authorization=await open(reviewer,'阶段流转')
    await button(authorization,'裁决阶段流转').click()
    const form=dialog(reviewer,'裁决阶段流转')
    const archiveReason='已核查四项复盘答复及确认教训的证据。'
    await form.getByLabel('流转裁决',{exact:true}).selectOption('approve')
    await form.getByLabel('裁决理由',{exact:true}).fill(archiveReason)
    const result=await write(reviewer,'POST','/approvals/[^/]+/decide',button(form,'确认流转裁决'))
    expect(result).toMatchObject({toStage:'archived', status:'applied', decisionReason:archiveReason})
    await reviewer.reload()
    const history=await open(reviewer,'阶段流转')
    await expect(history.getByLabel('项目生命周期').getByText('当前阶段：已归档',{exact:true})).toBeVisible()
    await expect(history.getByText(archiveReason,{exact:true})).toBeVisible()
    await pm.reload()
    const readonly=await open(pm,'复盘与教训')
    await expect(readonly.getByText('已归档 · 只读',{exact:true})).toBeVisible()
    await expect(readonly.getByText(finding,{exact:true})).toBeVisible()
    for(const [i,question] of questions.entries()) {
      const record=readonly.locator('dl.answers > div').nth(i)
      await expect(record.getByText(question,{exact:true})).toBeVisible()
      await expect(record.getByText(written[i],{exact:true})).toBeVisible()
    }
    await expect(button(readonly,'编辑复盘')).toHaveCount(0)
  }
} finally { await browser.close() }
