<script setup lang="ts">
import {PmoHttpError} from './api-error'
import {computed,nextTick,onMounted,ref} from 'vue'
import {ArrowRight,FolderKanban,LockKeyhole,Pencil,Plus,RefreshCw,Search} from 'lucide-vue-next'
import AppShell from './AppShell.vue'
import AuthLayout from './AuthLayout.vue'
import ProjectPane from './ProjectPane.vue'
import {adminNavigation,globalNavigation,projectNavigation} from './navigation'
import Milestones from './Milestones.vue'
import Requirements from './Requirements.vue'
import Changes from './Changes.vue'
import RequirementBaseline from './RequirementBaseline.vue'
import RequirementExceptions from './RequirementExceptions.vue'
import Evidence from './Evidence.vue'
import Stakeholders from './Stakeholders.vue'
import Charter from './Charter.vue'
import Appointments from './Appointments.vue'
import Members from './Members.vue'
import AuditLog from './AuditLog.vue'
import AgentTokens from './AgentTokens.vue'
import Risks from './Risks.vue'
import Costs from './Costs.vue'
import PreapprovalSpending from './PreapprovalSpending.vue'
import Alerts from './Alerts.vue'
import Reviews from './Reviews.vue'
import ProjectLifecycle from './ProjectLifecycle.vue'
import Retrospective from './Retrospective.vue'
import IncomingRisk from './IncomingRisk.vue'
import TerminationConfirmations from './TerminationConfirmations.vue'
import Reports from './Reports.vue'
import Tasks from './Tasks.vue'
import PersonalTasks from './PersonalTasks.vue'
import PersonalApprovals from './PersonalApprovals.vue'
import Health from './Health.vue'
import OrgDirectory from './OrgDirectory.vue'
import AcceptOrgInvite from './AcceptOrgInvite.vue'
import ProjectAssistant from './ProjectAssistant.vue'
import ProjectOverview from './ProjectOverview.vue'
type Project={projectId:string;code:string;name:string;stage:string;revision:number;createdAt:string}
type ProjectIdentity=Omit<Project,'createdAt'>
type HealthSample={snapshotId:string;asOf:string;health:'green'|'yellow'|'red'|'untrusted';publication:'official'|'preview'|'withheld';ruleVersion:string}
type OverviewRow=Project&{materialsVisible:boolean;canEditDraft:boolean;manager?:{personId:string;name:string};healthSample?:HealthSample}
const user=ref(false),ready=ref(false),busy=ref(false),loading=ref(false),error=ref(''),formError=ref('')
const username=ref(''),password=ref(''),query=ref(''),keyword=ref(''),stageFilter=ref('')
const page=ref(1),total=ref(0),rows=ref<OverviewRow[]>([]),listReadAt=ref('')
const acceptingInvite=ref(false),loginNotice=ref('')
const dialog=ref(false),selected=ref<OverviewRow|null>(null),name=ref(''),code=ref(''),description=ref('')
const csrf=ref(sessionStorage.getItem('pmo.csrf')||'');let sequence=0,attempt='',lastPayload=''
const title=computed(()=>selected.value?'修改草稿名称':'新建项目草稿')
const admin=ref(false)
const materialProject=ref<Project|null>(null),showMaterials=ref(false),materialTab=ref('overview'),focusTaskId=ref<string>()
const focusReviewId=ref<string>(),focusApprovalId=ref<string>(),focusChangeId=ref<string>()
const focusReportId=ref<string>()
const lessonDraftProject=ref<string>()
const entry=ref('ledger'), retainedEntries=ref(new Set<string>())
const activeAccount=ref(''), projectError=ref(''), projectLoading=ref(false)
const authLayout=ref<InstanceType<typeof AuthLayout>>()
const projectHeading=ref<HTMLElement>()
const overviewView=ref<InstanceType<typeof ProjectOverview>>()
const reviewsView=ref<InstanceType<typeof Reviews>>()
const draftCode=ref<HTMLInputElement>(), draftName=ref<HTMLInputElement>()
const exceptionView=ref<InstanceType<typeof RequirementExceptions>>()
const exceptionsAvailable=computed(()=>['delivering','closing_review'].includes(materialProject.value?.stage??''))
const showingExceptions=computed(()=>materialTab.value==='exceptions'||(!exceptionsAvailable.value&&!!exceptionView.value?.navigationLocked))
const groups=computed(()=>{
  if(showMaterials.value){
    return projectNavigation.map(group=>({...group,items:group.items.filter(item=>item.key!=='exceptions'||exceptionsAvailable.value)}))
  }
  return admin.value?[...globalNavigation,adminNavigation]:globalNavigation
})
const activeDestination=computed(()=>showMaterials.value?materialTab.value:entry.value)
const pageTitle=computed(()=>groups.value.flatMap(group=>group.items).find(item=>item.key===activeDestination.value)?.label??'项目台账')
let materialEpoch=0,ledgerScroll=0,deferredProjectLink=false
let ledgerTrigger:HTMLElement|undefined

function rememberLedger(){
  ledgerScroll=window.scrollY
  ledgerTrigger=document.activeElement instanceof HTMLElement?document.activeElement:undefined
}
function showEntry(key:string){
  entry.value=key
  retainedEntries.value.add(key)
}
function goTop(){void nextTick(()=>window.scrollTo({top:0}))}
function selectDestination(key:string){
  if(!groups.value.some(group=>group.items.some(item=>item.key===key)))return
  if(showMaterials.value){if(materialTab.value!==key){materialTab.value=key;goTop()}return}
  if(entry.value===key)return
  if(key==='ledger'){backToLedger();return}
  if(entry.value==='ledger')rememberLedger()
  deferredProjectLink=false
  showEntry(key);goTop()
}
function backToLedger(){
  const lockedModule=exceptionView.value?.navigationLocked?'exceptions':reviewsView.value?.navigationLocked?'reviews':''
  if(lockedModule){
    if(lockedModule!=='exceptions'||exceptionsAvailable.value) materialTab.value=lockedModule
    void nextTick(()=>document.getElementById('project-pane-'+lockedModule)?.scrollIntoView({block:'nearest'}))
    return
  }
  materialEpoch++
  showMaterials.value=false
  materialProject.value=null
  lessonDraftProject.value=undefined
  projectLoading.value=false
  projectError.value=''
  const resumeProject=deferredProjectLink
  deferredProjectLink=false
  showEntry('ledger')
  void nextTick(()=>{
    ledgerTrigger?.focus({preventScroll:true})
    window.scrollTo({top:ledgerScroll})
  })
  if(resumeProject)void openLinkedWorkbench()
}
const uuid=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
const {VITE_PMO_WORKBENCH_ORIGIN,VITE_PMO_WORKBENCH_ACCOUNT_ID,VITE_PMO_WORKBENCH_PROJECT_ID}=import.meta.env
function materials(row:Project){
  if(!showMaterials.value&&entry.value==='ledger')rememberLedger()
  materialEpoch++
  projectLoading.value=false
  projectError.value=''
  materialProject.value=row;focusTaskId.value=focusReviewId.value=focusApprovalId.value=focusChangeId.value=focusReportId.value=undefined
  lessonDraftProject.value=undefined
  deferredProjectLink=false
  materialTab.value='overview';showMaterials.value=true;goTop()
  void nextTick(()=>projectHeading.value?.focus({preventScroll:true}))
}
function openTask(project:Project,taskId:string){materials(project);focusTaskId.value=taskId;materialTab.value='tasks'}
function openChange(changeId:string){focusChangeId.value=changeId;selectDestination('changes')}
function openReviewTask(taskId:string){focusTaskId.value=taskId;selectDestination('tasks')}
function draftLessonFromAssistant(){
  if(!materialProject.value)return
  lessonDraftProject.value=materialProject.value.projectId
  selectDestination('retrospective')
}
function openApproval(project:Project,kind:'review'|'transition'|'change',id:string){
  materials(project)
  if(kind==='review'){focusReviewId.value=id;materialTab.value='reviews'}
  else if(kind==='transition'){focusApprovalId.value=id;materialTab.value='lifecycle'}
  else{openChange(id)}
}
function stageRead(projectId:string,stage:string,revision:number){
  if(materialProject.value?.projectId===projectId&&revision>=materialProject.value.revision){
    materialProject.value={...materialProject.value,stage,revision}
    if(materialTab.value==='exceptions'&&!['delivering','closing_review'].includes(stage))materialTab.value='requirements'
  }
  const current=rows.value.find(row=>row.projectId===projectId)
  if(current&&revision>current.revision){
    rows.value=rows.value.map(row=>row===current?{...row,stage,revision,canEditDraft:row.canEditDraft&&stage==='drafting'}:row)
    void load()
  }
}
function projectRead(fresh:ProjectIdentity){
  const current=materialProject.value
  if(!current||fresh.projectId!==current.projectId||fresh.revision<current.revision)return
  materialProject.value={...current,...fresh}
  stageRead(fresh.projectId,fresh.stage,fresh.revision)
}
async function refreshProject(){
  const project=materialProject.value
  if(!project||projectLoading.value)return
  if(materialTab.value==='overview'&&overviewView.value){
    await overviewView.value.refresh()
    return
  }
  const epoch=materialEpoch
  projectLoading.value=true;projectError.value=''
  try{
    const {data}:{data:{project:ProjectIdentity}}=await api('project-overview/'+project.projectId)
    if(epoch===materialEpoch)projectRead(data.project)
  }catch(e){if(epoch===materialEpoch)projectError.value=message(e)}
  finally{if(epoch===materialEpoch)projectLoading.value=false}
}
function stageTone(stage:string){
  if(['rejected','terminated'].includes(stage))return 'danger'
  if(stage==='on_hold')return 'warning'
  if(stage==='closed')return 'success'
  if(['drafting','archived','retrospective'].includes(stage))return 'info'
  return 'primary'
}
const stageNames:Record<string,string>={drafting:'草稿',charter_review:'立项评审',rejected:'已拒绝',designing:'方案细化',feasibility_review:'落地评审',executing:'执行中',on_hold:'已暂停',delivering:'交付验收',closing_review:'结项评审',closed:'已结项',retrospective:'复盘',terminated:'已终止',archived:'已归档'}
const healthNames={green:'绿',yellow:'黄',red:'红',untrusted:'数据不可信'}
const healthTags={green:'success',yellow:'warning',red:'danger',untrusted:'info'} as const
const healthPublication={official:'正式快照',preview:'未正式发布',withheld:'暂缓发布'}
function shanghai(value:string){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function clearSession(){
  sequence++;materialEpoch++
  user.value=false;admin.value=false;activeAccount.value='';username.value='';password.value=''
  loading.value=false;rows.value=[];total.value=0;page.value=1;query.value='';keyword.value='';stageFilter.value='';listReadAt.value=''
  selected.value=null;dialog.value=false;name.value='';code.value='';description.value='';formError.value='';attempt='';lastPayload=''
  showMaterials.value=false;materialProject.value=null;projectLoading.value=false;projectError.value=''
  lessonDraftProject.value=undefined
  entry.value='ledger';retainedEntries.value.clear();deferredProjectLink=false;ledgerScroll=0;ledgerTrigger=undefined
  csrf.value='';sessionStorage.removeItem('pmo.csrf')
}
async function api(path:string,options:RequestInit={},receiveHeaders?:(h:Headers)=>void){
  const session=csrf.value
  const r=await fetch('/api/v1/'+path,{...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),'X-CSRF-Token':session,...options.headers},cache:'no-store'})
  if(!r.ok){
    if(r.status===401&&session===csrf.value&&path!=='auth/login')clearSession()
    const body=await r.json().catch(()=>({}))
    const descriptions:Record<string,string>={
      POLICY_PENDING:'该操作的业务策略尚待签核或生效。',POLICY_SNAPSHOT_STALE:'申请所依据的策略已变化，请重新提交。',APPROVAL_STALE:'申请所依据的数据或授权已变化，请拒绝旧申请后重新提交。',
      INDEPENDENT_APPROVER_REQUIRED:'需要有项目审批授权的独立人员裁决。',INVALID_REQUIREMENT_TRANSITION:'当前状态不允许此操作，请重新查询。',
      PENDING_SCOPE_CHANGE:'需求存在未决范围变更。',INDEPENDENT_CUSTOMER_REQUIRED:'须由未参与本需求交付或内部验收的客户代表确认。',
      VERSION_CONFLICT:'版本已变化，请重新读取后再保存。',PROJECT_CODE_CONFLICT:'项目编号已存在。',UNAUTHORIZED:'请重新登录或核对账号密码。',
      FORBIDDEN:'没有此操作的权限。',RATE_LIMITED:'登录尝试过多，请稍后重试。',
      TRANSITION_STALE:'申请对应的阶段或材料已变化，请使用最新材料重新申请。',
      MILESTONE_COMPLETION_REQUIRED:'全部有效里程碑须完成并附核验凭证。',
      RETROSPECTIVE_ANSWERS_REQUIRED:'请先保存完整的复盘四问。',CONFIRMED_LESSON_REQUIRED:'归档须有至少一条经独立人工确认的教训。',
      REVIEW_SNAPSHOT_STALE:'已冻结的评审材料发生变化，请撤回后重送。',
      ASSISTANT_SOURCE_CHANGED:'预览依据已变化，请重新读取后生成。',REQUEST_IN_PROGRESS:'上一份请求仍在处理，请稍后重试。',
      CUSTOMER_DISPUTE_OPEN:'本版本存在未处理的正式客户争议。',
      REQUIREMENT_DISPUTE_LOCKED:'当前阶段或需求版本不接受争议写入。',
      DISPUTE_ALREADY_WITHDRAWN:'该争议已撤回。',
      HEALTH_APPEAL_LIMIT:'该快照已申诉过一次。',ZERO_BUDGET_BASIS_STALE:'预算基线已变化，请重新读取后声明。',
      INVITATION_INVALID:'邀请码无效、已使用、已撤销或过期。',INVITATION_ALREADY_EXISTS:'该邮箱已注册或有尚未到期的邀请。',
      USERNAME_UNAVAILABLE:'该账号不可用，请另选账号。',ADMIN_ACCOUNT_PROTECTED:'不能在此修改管理员账号状态。',
      ACCOUNT_STATUS_UNCHANGED:'账号状态未变化，请刷新目录。',PERSON_INACTIVE:'成员档案已停用，不能启用其账号。',
      ROLE_OCCUPIED:'该项目角色已有有效任命，请重新选择。',INVITATION_EXISTS:'该人员已有同一项目角色的提名或邀请。',
      INVITATION_EXPIRED:'该项目任命邀请已过期。',INVITATION_NOT_ACTIONABLE:'该任命邀请的状态或项目阶段已变化。',
      AUTHORITY_INACTIVE:'发出此邀请的任命授权已失效，请联系该项目的任命负责人。',
      ROLE_REQUIRES_INDEPENDENT_PERSON:'评审人和客户代表须独立于项目发起人、项目经理和 Sponsor。',
      MEMBERSHIP_STAGE_LOCKED:'项目当前阶段不接受成员撤销，请重新读取项目状态。',
    }
    const code=typeof body.code==='string'?body.code:''
    throw new PmoHttpError(r.status,code,descriptions[code]||(code?`操作失败（${code}）`:`操作失败（${r.status}）`))
  }
  const body=r.status===204?null:await r.json();receiveHeaders?.(r.headers);return body
}
function message(e:unknown){return e instanceof TypeError?'无法连接服务，请检查网络。':e instanceof Error?e.message:'操作失败。'}
async function load(){
  const id=++sequence;loading.value=true;error.value=''
  try{
    const filters=new URLSearchParams({page:String(page.value),pageSize:'20','filters[keyword]':keyword.value,'filters[stage]':stageFilter.value})
    const {data,meta}:{data:{items:OverviewRow[];total:number},meta:{asOf:string}}=await api('project-overview?'+filters)
    if(id!==sequence)return
    const last=Math.max(1,Math.ceil(data.total/20))
    if(page.value>last){page.value=last;await load();return}
    rows.value=data.items;total.value=data.total;listReadAt.value=meta.asOf
  }catch(e){if(id===sequence){error.value=message(e);rows.value=[];total.value=0;listReadAt.value=''}}
  finally{if(id===sequence)loading.value=false}
}
async function openLinkedWorkbench(defaultProject=''){
  const link=new URLSearchParams(location.hash.slice(1))
  const linkedProject=link.get('project')
  const implicit=linkedProject===null&&!!defaultProject
  const project=linkedProject??defaultProject,tab=link.get('tab')??'overview',report=link.get('report')??''
  if(!uuid.test(project)||!['overview','assistant','reports'].includes(tab))return
  const session=csrf.value
  const epoch=materialEpoch
  try{
    const {data}=await api('project-overview/'+project)
    if(!user.value||session!==csrf.value||epoch!==materialEpoch||showMaterials.value||entry.value!=='ledger')return
    if(implicit&&!data.materialsVisible)return
    materials(data.project)
    if(data.materialsVisible){
      focusReportId.value=tab==='reports'&&uuid.test(report)?report:undefined
      materialTab.value=tab
    }
  }catch(e){if(!implicit&&user.value&&session===csrf.value)error.value=message(e)}
}
async function openAssignedProject(projectId:string){
  await openProjectFromEntry(projectId,'appointments')
}
async function openPortfolioReport(projectId:string,reportId:string){
  if(!uuid.test(projectId)||!uuid.test(reportId))return
  await openProjectFromEntry(projectId,showMaterials.value?'assistant':'alerts',reportId)
}
async function openProjectFromEntry(projectId:string,source:'appointments'|'alerts'|'assistant',reportId?:string){
  if(projectLoading.value)return
  const epoch=materialEpoch
  const session=csrf.value
  const sameContext=()=>epoch===materialEpoch&&session===csrf.value&&
    (source==='assistant'?showMaterials.value&&materialTab.value==='assistant':entry.value===source&&!showMaterials.value)
  projectLoading.value=true;error.value=''
  try{
    const {data}=await api('project-overview/'+projectId)
    if(!sameContext())return
    if(!data.materialsVisible)throw new Error('当前账号已无权读取该项目资料。')
    materials(data.project)
    if(reportId){focusReportId.value=reportId;materialTab.value='reports'}
  }catch(e){if(sameContext())(source==='assistant'?projectError:error).value=message(e)}
  finally{if(epoch===materialEpoch)projectLoading.value=false}
}
async function startSession(){
  const account=await api('auth/me')
  const link=new URLSearchParams(location.hash.slice(1))
  admin.value=account.isSystemAdmin
  activeAccount.value=username.value||`账号 ${account.accountId.slice(0,8)}`
  if(link.get('entry')==='agent-tokens'){
    deferredProjectLink=uuid.test(link.get('project')??'')
    showEntry('agent-tokens')
  }
  user.value=true
  await load()
  const defaultProject=!location.hash&&location.origin===VITE_PMO_WORKBENCH_ORIGIN&&account.accountId===VITE_PMO_WORKBENCH_ACCOUNT_ID
    ? VITE_PMO_WORKBENCH_PROJECT_ID : ''
  if(entry.value!=='agent-tokens')await openLinkedWorkbench(defaultProject)
}
function enrollment(show:boolean){
  acceptingInvite.value=show;password.value='';error.value='';loginNotice.value=''
  void nextTick(()=>authLayout.value?.focusForm())
}
function accountAccepted(name:string){enrollment(false);username.value=name;loginNotice.value='账号已开通，请登录。'}
async function login(){if(busy.value)return;busy.value=true;error.value='';try{const r=await api('auth/login',{method:'POST',body:JSON.stringify({username:username.value,password:password.value})});csrf.value=r.csrfToken;sessionStorage.setItem('pmo.csrf',csrf.value);await startSession()}catch(e){error.value=message(e)}finally{password.value='';busy.value=false}}
async function logout(){try{await api('auth/logout',{method:'POST'});clearSession();error.value=''}catch(e){error.value=message(e)}}
function search(){page.value=1;keyword.value=query.value.trim();void load()}
function applyStage(event:Event){stageFilter.value=(event.currentTarget as HTMLSelectElement).value;search()}
function open(row:OverviewRow|null){if(row&&!row.canEditDraft)return;selected.value=row;code.value=row?.code||'';name.value=row?.name||'';description.value='';formError.value='';attempt='';lastPayload='';dialog.value=true}
function focusDraftField(){
  const field=selected.value?draftName.value:draftCode.value
  if(field&&!field.closest('form')?.contains(document.activeElement))field.focus()
}
async function reread(){const row=selected.value;if(!row)return;try{const {data}=await api('projects/'+row.projectId);selected.value={...row,...data.project};formError.value=data.project.stage==='drafting'?'':'项目已离开草稿阶段。';attempt=''}catch(e){formError.value=message(e)}}
async function save(){if(busy.value)return;if(!name.value.trim()||(!selected.value&&!/^[A-Za-z0-9][A-Za-z0-9_-]{1,59}$/.test(code.value))){formError.value='请填写项目名称及有效编号（2–60 个字符，以字母或数字开头，可包含下划线和短横线）。';return}
  busy.value=true;formError.value='';const row=selected.value;const body=JSON.stringify(row?{fields:{name:name.value.trim()}}:{code:code.value,name:name.value.trim(),description:description.value});const sig=body+':'+(row?.revision??0);if(!attempt||sig!==lastPayload){attempt=crypto.randomUUID();lastPayload=sig}
  try{await api(row?'projects/'+row.projectId:'projects',{method:row?'PATCH':'POST',headers:{'Idempotency-Key':attempt,...(row?{'If-Match':String(row.revision)}:{})},body});dialog.value=false;await load()}catch(e){formError.value=message(e)}finally{busy.value=false}}
onMounted(async()=>{try{if(csrf.value)await startSession()}catch(e){error.value=message(e)}finally{ready.value=true}})
</script>

<template>
  <AuthLayout v-if="ready && !user" ref="authLayout">
    <AcceptOrgInvite v-if="acceptingInvite" :api="api" @back="enrollment(false)" @accepted="accountAccepted"/>
    <template v-else>
      <div class="mark"><FolderKanban :size="27" aria-hidden="true"/></div>
      <h1>登录工作台</h1>
      <p class="auth-subtitle">使用你的组织账号继续</p>
      <el-alert v-if="loginNotice" :title="loginNotice" type="success" :closable="false"/>
      <p v-if="loginNotice" class="account-guidance">请使用刚设置的密码登录。接受项目任命后，可从项目台账打开项目。</p>
      <form @submit.prevent="login">
        <label>账号<input v-model="username" autocomplete="username" maxlength="120" required></label>
        <label>密码<input v-model="password" autocomplete="current-password" type="password" maxlength="256" required></label>
        <el-alert v-if="error" :title="error" type="error" :closable="false"/>
        <el-button type="primary" native-type="submit" :loading="busy">登录</el-button>
        <el-button text :disabled="busy" @click="enrollment(true)">接受组织邀请 <ArrowRight :size="16" aria-hidden="true"/></el-button>
      </form>
      <p v-if="!loginNotice" class="account-guidance">首次使用请向组织管理员领取邀请码。遇到账号或密码问题也可联系管理员。</p>
    </template>
  </AuthLayout>

  <AppShell v-else-if="user" :groups="groups" :active="activeDestination" :title="pageTitle"
    :project="showMaterials && materialProject ? materialProject : undefined" :account="activeAccount"
    @navigate="selectDestination" @back="backToLedger" @logout="logout">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <div v-show="!showMaterials && entry==='ledger'">
      <div class="page-heading">
        <div>
          <h1>项目台账</h1>
          <p class="page-meta" aria-live="polite">{{keyword||stageFilter?'筛选结果':'已授权项目'}} · {{loading&&!listReadAt?'读取中':error?'读取失败':total}}</p>
        </div>
        <el-button type="primary" @click="open(null)"><Plus :size="17" aria-hidden="true"/> 新建项目</el-button>
      </div>
      <section class="panel project-ledger" aria-label="项目清单">
        <form class="toolbar project-filters" @submit.prevent="search">
          <label class="search"><Search :size="17"/><input aria-label="搜索项目名称或编号" placeholder="搜索项目名称或编号" maxlength="160" v-model="query"></label>
          <label class="stage-filter"><span>阶段</span><select aria-label="筛选项目阶段" :value="stageFilter" @change="applyStage"><option value="">全部</option><option v-for="(label,stage) in stageNames" :key="stage" :value="stage">{{label}}</option></select></label>
          <el-button native-type="submit">搜索</el-button><el-button title="刷新项目列表" aria-label="刷新项目列表" @click="load"><RefreshCw :size="15"/></el-button>
          <span v-if="listReadAt" class="list-read">列表读取 <time :datetime="listReadAt">{{shanghai(listReadAt)}} 北京时间</time></span>
        </form>
        <el-table v-loading="loading" :data="rows" row-key="projectId" empty-text="没有符合条件的授权项目">
          <el-table-column prop="code" label="编号" min-width="124"/>
          <el-table-column label="项目名称" min-width="210"><template #default="{row}"><button class="project-link" :aria-label="`查看 ${row.name} 的项目资料`" @click="materials(row)">{{row.name}}</button></template></el-table-column>
          <el-table-column label="项目经理" min-width="95"><template #default="{row}">{{row.manager?.name??(row.materialsVisible?'无有效任命':'不可见')}}</template></el-table-column>
          <el-table-column label="阶段" min-width="92"><template #default="{row}"><el-tag :type="stageTone(row.stage)" effect="light">{{stageNames[row.stage]??row.stage}}</el-tag></template></el-table-column>
          <el-table-column label="最近健康采样" min-width="166"><template #default="{row}: {row: OverviewRow}">
            <div v-if="row.healthSample" class="sample">
              <div><el-tag :type="healthTags[row.healthSample.health]" size="small" effect="plain">{{healthNames[row.healthSample.health]}}</el-tag> <span>{{healthPublication[row.healthSample.publication]}}</span></div>
              <time :datetime="row.healthSample.asOf" :title="row.healthSample.ruleVersion">{{shanghai(row.healthSample.asOf)}}</time>
            </div>
            <span v-else class="list-read">{{row.materialsVisible?'无采样':'不可见'}}</span>
          </template></el-table-column>
          <el-table-column prop="revision" label="版本" width="65"/>
          <el-table-column label="创建日期" min-width="98"><template #default="{row}">{{new Date(row.createdAt).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'})}}</template></el-table-column>
          <el-table-column label="操作" width="62"><template #default="{row}"><el-button v-if="row.canEditDraft" text title="修改草稿名称" aria-label="修改草稿名称" @click="open(row)"><Pencil :size="16"/></el-button></template></el-table-column>
        </el-table>
        <div v-if="listReadAt&&!loading&&!error&&!total&&!keyword&&!stageFilter" class="ledger-guidance">
          <p>收到项目任命后，前往「人员任命」接受邀请。已获授权的项目会显示在这里。</p>
          <el-button text @click="selectDestination('appointments')">查看项目任命邀请 <ArrowRight :size="16" aria-hidden="true"/></el-button>
        </div>
        <el-pagination background v-model:current-page="page" :page-size="20" :total="total" :disabled="loading" layout="total, prev, pager, next" @current-change="load"/>
      </section>
    </div>

    <template v-if="!showMaterials && entry!=='ledger'">
      <div v-if="entry!=='appointments'" class="page-heading"><h1>{{pageTitle}}</h1></div>
      <PreapprovalSpending v-if="entry==='spending'" :api="api"/>
      <PersonalApprovals v-if="entry==='approvals'" :api="api" :stages="stageNames" @open-approval="openApproval"/>
      <PersonalTasks v-if="entry==='personal-tasks'" :api="api" @open-task="openTask"/>
      <AuditLog v-if="admin && entry==='audit'" :api="api"/>
      <OrgDirectory v-if="admin && entry==='directory'" :api="api"/>
      <AgentTokens v-if="entry==='agent-tokens'" key="personal" :api="api" :projects="rows"/>
      <AgentTokens v-if="admin && entry==='org-tokens'" key="organization" :api="api" :projects="rows" organization/>
    </template>
    <Appointments v-if="retainedEntries.has('appointments')" v-show="!showMaterials && entry==='appointments'"
      :active="!showMaterials && entry==='appointments'" :api="api" :opening-project="projectLoading"
      @changed="load" @open-project="openAssignedProject"/>
    <Alerts v-if="retainedEntries.has('alerts')" v-show="!showMaterials && entry==='alerts'"
      :active="!showMaterials && entry==='alerts'" :api="api" @open-report="openPortfolioReport"/>
    <IncomingRisk v-if="retainedEntries.has('incoming-risk')" v-show="!showMaterials && entry==='incoming-risk'"
      :active="!showMaterials && entry==='incoming-risk'" :api="api"/>
    <TerminationConfirmations v-if="retainedEntries.has('termination')" v-show="!showMaterials && entry==='termination'"
      :active="!showMaterials && entry==='termination'" :api="api"/>

    <div v-if="showMaterials && materialProject" :key="materialProject.projectId" class="project-content">
      <div class="page-heading project-heading">
        <div>
          <div class="breadcrumb">项目台账 <span aria-hidden="true">/</span> 项目资料 <span aria-hidden="true">/</span> {{pageTitle}}</div>
          <h1 ref="projectHeading" tabindex="-1">{{materialTab==='overview'?materialProject.name:pageTitle}}</h1>
          <div class="page-meta project-meta">
            <span v-if="materialTab!=='overview'" class="project-identity">{{materialProject.name}}</span>
            <span v-else class="project-code">{{materialProject.code}}</span>
            <el-tag :type="stageTone(materialProject.stage)" effect="light">{{stageNames[materialProject.stage]??materialProject.stage}}</el-tag>
            <span>版本 {{materialProject.revision}}</span>
            <el-button
              :aria-label="materialTab==='overview'?'刷新项目概览':'刷新项目资料'"
              :title="materialTab==='overview'?'刷新项目概览':'刷新项目资料'"
              :loading="projectLoading||(materialTab==='overview'&&overviewView?.loading)" @click="refreshProject">
              <RefreshCw :size="16" aria-hidden="true"/>
            </el-button>
          </div>
        </div>
      </div>
      <div v-if="materialProject.stage==='archived'" class="notice-band"><LockKeyhole :size="17" aria-hidden="true"/>已归档 · 当前资料只读</div>
      <el-alert v-if="projectError" :title="projectError" type="error" :closable="false" show-icon/>
      <ProjectPane name="overview" :active="materialTab==='overview'">
        <ProjectOverview ref="overviewView" :project-id="materialProject.projectId" :active="showMaterials&&materialTab==='overview'" :stages="stageNames" :api="api" @project-read="projectRead" @navigate="selectDestination" @open-change="openChange"/>
      </ProjectPane>
      <ProjectPane name="milestones" :active="materialTab==='milestones'">
        <Milestones :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/>
      </ProjectPane>
      <ProjectPane name="requirements" :active="materialTab==='requirements'">
        <Requirements :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/>
      </ProjectPane>
      <ProjectPane name="baseline" :active="materialTab==='baseline'">
        <RequirementBaseline :project-id="materialProject.projectId" :api="api"/>
      </ProjectPane>
      <ProjectPane v-if="exceptionsAvailable||exceptionView?.navigationLocked" name="exceptions" :active="showingExceptions">
        <RequirementExceptions ref="exceptionView" :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/>
      </ProjectPane>
      <ProjectPane name="changes" :active="materialTab==='changes'">
        <Changes :project-id="materialProject.projectId" :focus-change-id="focusChangeId" :api="api" @clear-focus="focusChangeId=undefined"/>
      </ProjectPane>
      <ProjectPane name="evidence" :active="materialTab==='evidence'">
        <Evidence :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/>
      </ProjectPane>
      <ProjectPane name="charter" :active="materialTab==='charter'">
        <Charter :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/>
      </ProjectPane>
      <ProjectPane name="stakeholders" :active="materialTab==='stakeholders'">
        <Stakeholders :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/>
      </ProjectPane>
      <ProjectPane name="members" :active="materialTab==='members'">
        <Members :project-id="materialProject.projectId" :api="api" @changed="refreshProject"/>
      </ProjectPane>
      <ProjectPane name="risks" :active="materialTab==='risks'">
        <Risks :project-id="materialProject.projectId" :api="api"/>
      </ProjectPane>
      <ProjectPane name="costs" :active="materialTab==='costs'">
        <Costs :project-id="materialProject.projectId" :api="api"/>
      </ProjectPane>
      <ProjectPane name="reviews" :active="materialTab==='reviews'">
        <Reviews ref="reviewsView" :project-id="materialProject.projectId" :project-revision="materialProject.revision" :active="materialTab==='reviews'"
          :focus-review-id="focusReviewId" :stage-label="stageNames[materialProject.stage]??materialProject.stage" :api="api" @stage-read="stageRead" @open-task="openReviewTask"/>
      </ProjectPane>
      <ProjectPane name="lifecycle" :active="materialTab==='lifecycle'">
        <ProjectLifecycle :project-id="materialProject.projectId" :project-revision="materialProject.revision" :focus-approval-id="focusApprovalId" :api="api" @stage-read="stageRead" @clear-focus="focusApprovalId=undefined"/>
      </ProjectPane>
      <ProjectPane name="tasks" :active="materialTab==='tasks'">
        <Tasks :project-id="materialProject.projectId" :focus-task-id="focusTaskId" :api="api" @clear-focus="focusTaskId=undefined"/>
      </ProjectPane>
      <ProjectPane name="reports" :active="materialTab==='reports'">
        <Reports :project-id="materialProject.projectId" :focus-report-id="focusReportId" :api="api"/>
      </ProjectPane>
      <ProjectPane name="health" :active="materialTab==='health'">
        <Health :project-id="materialProject.projectId" :active="materialTab==='health'" :api="api"/>
      </ProjectPane>
      <ProjectPane name="retrospective" :active="materialTab==='retrospective'">
        <Retrospective :project-id="materialProject.projectId" :project-revision="materialProject.revision" :api="api"
          :requested-draft="lessonDraftProject===materialProject.projectId" @draft-opened="lessonDraftProject=undefined"/>
      </ProjectPane>
      <ProjectPane name="assistant" :active="materialTab==='assistant'">
        <ProjectAssistant v-if="materialTab==='assistant'" :project-id="materialProject.projectId" :project-label="`${materialProject.code} · ${materialProject.name}`"
          :revision="materialProject.revision" :api="api" @navigate="selectDestination" @draft-lesson="draftLessonFromAssistant" @open-report="openPortfolioReport"/>
      </ProjectPane>
    </div>
    <el-dialog v-model="dialog" :title="title" width="min(560px,94vw)" align-center
      :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @opened="focusDraftField">
      <form class="draft-form" @submit.prevent="save">
        <div class="form-field">
          <label for="draft-code">项目编号 <span v-if="!selected" aria-hidden="true">*</span></label>
          <input id="draft-code" ref="draftCode" v-model="code" :disabled="!!selected" aria-label="项目编号" :aria-describedby="selected?undefined:'draft-code-hint'" maxlength="60" required>
          <small v-if="!selected" id="draft-code-hint">2–60 个字符，以字母或数字开头，可包含下划线和短横线</small>
        </div>
        <div class="form-field">
          <label for="draft-name">项目名称 <span aria-hidden="true">*</span></label>
          <input id="draft-name" ref="draftName" v-model="name" aria-label="项目名称" maxlength="160" required>
        </div>
        <label v-if="!selected">说明<textarea v-model="description" maxlength="2000" rows="4"></textarea></label>
        <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon/>
        <div class="actions">
          <el-button v-if="selected && formError" :disabled="busy" @click="reread">重新读取版本</el-button>
          <el-button :disabled="busy" @click="dialog=false">取消</el-button>
          <el-button type="primary" native-type="submit" :loading="busy" :disabled="!!selected&&selected.stage!=='drafting'">保存</el-button>
        </div>
      </form>
    </el-dialog>
  </AppShell>
  <main v-else class="initial-session" aria-busy="true"><span role="status">正在读取会话…</span></main>
</template>

<style scoped>
.account-guidance{margin:12px 0;color:var(--pmo-muted);font-size:13px}
.ledger-guidance{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;
  margin-top:16px;padding:8px 12px;border-radius:var(--pmo-radius-control);background:var(--pmo-soft);color:var(--pmo-secondary)}
.ledger-guidance p{margin:0}
</style>
