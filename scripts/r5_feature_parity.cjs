const { parse }=require('../web/node_modules/@vue/compiler-sfc')
const ts=require('../web/node_modules/typescript')
const { execFileSync }=require('node:child_process')
const fs=require('node:fs')
const base='fe7c50b'
const screens={
App:'ledger|project',AppShell:'shell',AuthLayout:'auth',AcceptOrgInvite:'auth',Appointments:'appointments',Alerts:'alerts',IncomingRisk:'incoming-risk',PersonalApprovals:'approvals',PersonalTasks:'personal-tasks',TerminationConfirmations:'termination',PreapprovalSpending:'spending',AgentTokens:'agent-tokens|org-tokens',AuditLog:'audit',OrgDirectory:'directory',ProjectOverview:'overview',Charter:'charter',Milestones:'milestones',Requirements:'requirements',RequirementAction:'requirements',RequirementHistory:'requirements|assistant',RequirementDisputes:'requirements',RequirementBaseline:'baseline',RequirementMetrics:'baseline',RequirementExceptions:'exceptions',RequirementExceptionReceipt:'exceptions|reviews',Changes:'changes',FormalChangeProposal:'changes',ChangeDecision:'changes',Evidence:'evidence',ScopedEvidencePicker:'shared',Risks:'risks',RiskResolution:'risks|incoming-risk',Costs:'costs',CategoryCosts:'costs',CostReconciliation:'costs',CostReassessment:'tasks',Postings:'costs',Health:'health',Reviews:'reviews',ReviewWorkbench:'reviews',ReviewRationaleCheck:'reviews',ReviewMeeting:'reviews',Tasks:'tasks',Reports:'reports',ReportAnalysis:'reports',ReportFacts:'reports',WeeklyFactReview:'reports',WeeklyEffortPosting:'reports',WeeklyDelivery:'reports',Retrospective:'retrospective',ReviewCalibration:'retrospective',ProjectAssistant:'assistant',AssistantLines:'assistant',AssistantRequirementHistory:'assistant',WeeklyPortfolioReports:'assistant',WeeklyHealthOverview:'alerts|assistant',ProjectPane:'shell',PmoBrand:'shell|auth'
}
const tests={ledger:'project-overview, draft',project:'project-overview, lifecycle, dual-entry',shell:'draft, lifecycle, assistant',auth:'organization, draft',appointments:'draft, organization',alerts:'lr08-monthly, weekly-health',approvals:'personal-approvals',spending:'lr08-monthly, assistant',termination:'lifecycle',audit:'organization, dual-entry',directory:'organization','agent-tokens':'organization, dual-entry','org-tokens':'organization',overview:'project-overview',charter:'draft, lifecycle',milestones:'draft, lifecycle',requirements:'draft, requirement-disputes, requirement-additions',baseline:'draft, formal-changes',exceptions:'lifecycle',changes:'formal-changes, requirement-additions',evidence:'draft, dual-entry, lifecycle',risks:'lifecycle, draft','incoming-risk':'lifecycle',costs:'health, lifecycle',health:'health, weekly-health',reviews:'draft, lifecycle, personal-approvals',tasks:'personal-tasks, lifecycle',reports:'weekly, facts, analysis, lifecycle',retrospective:'lifecycle',assistant:'assistant, assistant-model, weekly-portfolio',shared:'lifecycle, analysis, requirement-disputes'}
const src=(name,original)=>{
 if(!original)return fs.readFileSync(`web/src/${name}.vue`,'utf8')
 try{return execFileSync('git',['show',`${base}:web/src/${name}.vue`],{encoding:'utf8',stdio:['ignore','pipe','ignore']})}
 catch{return ''}
}
const apiCalls=code=>{
 const file=ts.createSourceFile('component.ts',code,ts.ScriptTarget.Latest,true)
 const use=[]
 function visit(n){
   if(ts.isCallExpression(n)&&['props.api','api','fetch'].includes(n.expression.getText(file))){
    const path=n.arguments[0]?.getText(file); const opts=n.arguments[1]?.getText(file)
    const method=opts?.match(/\bmethod\s*:\s*['"]([A-Z]+)['"]/)?.[1]??(opts?.includes('method')||opts?.match(/^[a-z]\w*$/i)?'动态':'GET')
    if(path) use.push(`${method} ${path}`)
   }
   ts.forEachChild(n,visit)
 }
 visit(file)
 return [...new Set(use)].join(' | ')
}
const controls=(name,source)=>{
 if(!source)return []
 const {descriptor,errors}=parse(source)
 if(errors.length)throw Error(`${name}: ${errors[0]}`)
 const requests=apiCalls(descriptor.scriptSetup?.content??'')
 const rows=[]
 const count=new Map()
 const flat=s=>s.replace(/\s+/g,' ').trim()
 const text=(n)=>flat(n.children.filter(c=>c.type===2||c.type===5).map(c=>c.type===5?'{{'+c.content.content+'}}':c.content).join(' '))
 function visit(n,guards=[],labels=[]){
  if(n.type===0){n.children.forEach(c=>visit(c,guards,labels));return}
  if(n.type!==1)return
  const staticProps=Object.fromEntries(n.props.filter(p=>p.type===6).map(p=>[p.name,p.value?.content??p.name]))
  const bound=Object.fromEntries(n.props.filter(p=>p.type===7&&p.name==='bind').map(p=>[p.arg?.content,p.exp?.content]))
  const condition=n.props.filter(p=>p.type===7&&['if','else-if','else','show'].includes(p.name)).map(p=>p.exp?.content??'else')
  const conditions=[...guards,...condition]
  const events=n.props.filter(p=>p.type===7&&['on','model'].includes(p.name)).map(p=>`${p.name==='model'?'v-model':p.arg?.content??'事件'} ${p.exp?.content??''}`)
  const field=['input','select','textarea','el-select','el-input','el-input-number','el-radio-group','el-segmented','el-date-picker','el-upload','el-checkbox','el-table-column'].includes(n.tag)
  const action=['button','el-button','a','summary','form','el-pagination','el-dialog','el-drawer'].includes(n.tag)
  let label=staticProps['aria-label']??bound['aria-label']??staticProps.title??bound.title??staticProps.label??bound.label??text(n)
  if(!label&&field)label=labels.at(-1)
  if(!label&&staticProps.placeholder)label=staticProps.placeholder
  if(!label&&staticProps.prop)label=staticProps.prop
  if(!label)label=n.tag
  if(field||action||events.length){
   const workflowChange=name==='WeeklyEffortPosting'&&label==='提报待核验凭证'
   const identity=workflowChange?label:events.join('')
   const unnumbered=[n.tag,identity,staticProps.prop??'',n.tag==='el-table-column'?label:''].join('\u0000')
   const order=(count.get(unnumbered)??0)+1
   count.set(unnumbered,order)
   const controls=n.props.filter(p=>(p.type===6&&['required','maxlength','minlength','type','pattern','href','native-type','min','max','disabled'].includes(p.name))||
      (p.type===7&&p.name==='bind'&&['required','disabled','loading','data','model-value','current-page','total','options','href','native-type'].includes(p.arg?.content)))
      .map(p=>p.type===6?`${p.name}=${p.value?.content??'true'}`:`${p.arg.content}=${p.exp?.content}`).join(' | ')
   rows.push({key:unnumbered+'\u0000'+order,label,events:events.join(' | '),conditions:conditions.join(' > '),symbol:`${name}.vue:${n.loc.start.line}`,controls,requests})
  }
  const stack=n.tag==='label'?[...labels,text(n)]:labels
  n.children.forEach(c=>visit(c,conditions,stack))
 }
 if(descriptor.template?.ast)visit(descriptor.template.ast)
 return rows
}
const header=['entryKey','component','screenState','fieldOrAction','roleOrCondition','handlerBefore','apiMethodAndPath','payloadOrReadShape','expectedVisibleState','originalTest','afterComponent','afterHandler','afterTest','verdict','evidence']
const rows=[header]
for(const filename of fs.readdirSync('web/src').filter(f=>f.endsWith('.vue')).sort()){
 const name=filename.slice(0,-4),screen=screens[name]??'shared';const related=[...new Set(screen.split('|').map(k=>tests[k]).filter(Boolean))].join(', ')
 const before=controls(name,src(name,true));const after=controls(name,src(name,false));
 const oldByKey=new Map(before.map(row=>[row.key,row]));const newByKey=new Map(after.map(row=>[row.key,row]));
 for(const key of new Set([...oldByKey.keys(),...newByKey.keys()])){
   const was=oldByKey.get(key);const now=newByKey.get(key);const row=now??was
   const verdict=!was?'新增控件或绑定变更，需关联业务用例':!now?'原绑定有变化，需核对替代路径':
     was.events!==now.events?'兼容旧入口并接入分类候选（SRC-042/069/072/074）':
     (was.conditions!==now.conditions||was.controls!==now.controls)?'可见性或属性变更，需专项验证':'原绑定保留（源码）'
   rows.push([screen,filename,screen,row.label,row.conditions,was?.events??'',row.requests,row.controls,row.label,related,
      now?.symbol??'',now?.events??'',`visual-r5（入口/布局）${related?'；'+related:''}`,verdict,
      [was?.symbol?`${base}:${was.symbol}`:'',now?.symbol?`当前：${now.symbol}`:''].filter(Boolean).join(' → ')])
 }
}
const escape=cell=>'"'+(cell??'').replace(/"/g,'""').replace(/\r?\n/g,' ')+'"'
fs.mkdirSync('docs/acceptance/r5',{recursive:true})
fs.writeFileSync('docs/acceptance/r5/feature-parity.csv','\ufeff'+rows.map(row=>row.map(escape).join(',')).join('\n')+'\n')
console.log(`${rows.length-1} control and data presentation rows`)
