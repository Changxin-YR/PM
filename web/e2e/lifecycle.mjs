import {chromium, expect} from '@playwright/test'

const url = process.env.PMO_BROWSER_URL
const sessions = JSON.parse(process.env.PMO_TEST_SESSIONS)
const browser = await chromium.launch({channel:process.env.PMO_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':'chromium')})
const projectName = 'Synthetic review'
const exact = name => ({name, exact:true})
const button = (scope, name) => scope.getByRole('button', exact(name))
const dialog = (page, name) => page.getByRole('dialog', exact(name))
const project = page => button(page, '查看 '+projectName+' 的项目资料')
const drawer = page => dialog(page, projectName+' · 项目资料')

async function closeDialog(panel) {
  await button(panel,'Close this dialog').click()
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

async function requestStage(page, name, next) {
  const panel=await open(page,'阶段流转')
  await button(panel,'申请阶段流转').click()
  const form=dialog(page,'申请阶段流转')
  await form.getByLabel('目标阶段',{exact:true}).selectOption(next)
  await form.getByLabel('申请原因',{exact:true}).fill(name)
  const state=await write(page,'POST','/transition-requests',button(form,'提交流转申请'))
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

try {
  const pm=await actor('pm')
  if(process.env.PMO_TEST_PHASE==='delivery') {
    await directStage(pm,'有效里程碑均已完工，并已核验交付凭证。','delivering','交付验收')
  } else if(process.env.PMO_TEST_PHASE==='exception') {
    const command=JSON.parse(process.env.PMO_TEST_REQUIREMENT_EXCEPTION)
    const customer=await actor('client_representative'), reviewer=await actor('reviewer')
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
    const receipt=await write(customer,'POST','/requirement-exceptions',button(panel,'签署书面例外'))
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
      return dialog(reviewer,'关口人工工作台')
    }
    const workbench=await openClosing()
    await expect(workbench.getByText(deviation,{exact:true})).toBeVisible()
    await button(workbench,'登记裁决').click()
    const judgment=workbench.getByLabel('裁决',{exact:true})
    await expect(judgment.locator('option[value=pass]')).toBeDisabled()
    await judgment.selectOption('conditional_pass')
    const reason='独立核验客户书面接受的具体需求偏差，成本与风险仍按正常结项规则检查。'
    await workbench.getByLabel('裁决理由',{exact:true}).fill(reason)
    const result=await write(reviewer,'POST','/reviews/[^/]+/decisions',button(workbench,'确认裁决'))
    expect(result.decision).toMatchObject({stage:'closed',rationale:reason,option:'conditional_pass'})
    expect(result.requirementException.receiptHash).toBe(receipt.receiptHash)
    await reviewer.reload()
    const history=await openClosing()
    await expect(history.getByText('裁决接受的具体需求偏差',{exact:true})).toBeVisible()
    await expect(history.getByText(deviation,{exact:true})).toBeVisible()
    await expect(history.getByText(reason,{exact:true})).toBeVisible()
  } else if(process.env.PMO_TEST_PHASE==='tasks_reports') {
    const panel=await open(pm,'补强任务')
    const title='按复核记录更新交付清单'
    await button(panel,'新建补强任务').click()
    await panel.getByLabel('标题',{exact:true}).fill(title)
    await panel.getByRole('combobox',exact('来源记录')).click()
    await pm.getByRole('option',{name:/落地评审/}).click()
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
    const inbox=dialog(owner,'我的终止确认')
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

    const risks=await open(reviewer,'风险')
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
    const workbench=dialog(reviewer,'关口人工工作台')
    await button(workbench,'登记裁决').click()
    await workbench.getByLabel('裁决',{exact:true}).selectOption('pass')
    const reason='独立核对了验收、双口径财务对账和风险关闭凭据。'
    await workbench.getByLabel('裁决理由',{exact:true}).fill(reason)
    const outcome=await write(reviewer,'POST','/reviews/[^/]+/decisions',button(workbench,'确认裁决'))
    expect(outcome.decision).toMatchObject({stage:'closed',rationale:reason})
    await expect(workbench.getByText(reason,{exact:true})).toBeVisible()
    await closeDialog(workbench)

    await pm.reload()
    await directStage(pm,'关闭后的独立项目复盘。','retrospective','复盘')
    const retro=await open(pm,'复盘与教训')
    await expect(retro.getByText('结项复盘',{exact:true})).toBeVisible()
    await button(retro,'填写复盘').click()
    const answers=dialog(pm,'填写项目复盘')
    const written=JSON.parse(process.env.PMO_TEST_ANSWERS)
    for(const [i,q] of ['目标与实际结果','交付表现及原因','可复用资产','经验教训与建议'].entries()) {
      await answers.getByLabel(q,{exact:true}).fill(written[i])
    }
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
    for(const answer of written) await expect(readonly.getByText(answer,{exact:true})).toBeVisible()
    await expect(button(readonly,'编辑复盘')).toHaveCount(0)
  }
} finally { await browser.close() }
