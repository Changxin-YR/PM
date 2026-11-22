<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {Layers3,LogOut,Plus,RefreshCw,Search,Pencil,FolderKanban} from 'lucide-vue-next'
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
function materials(row:Project){
  materialProject.value=row;focusTaskId.value=focusReviewId.value=focusApprovalId.value=focusChangeId.value=undefined
  materialTab.value='overview';showMaterials.value=true
}
function openTask(project:Project,taskId:string){materials(project);focusTaskId.value=taskId;materialTab.value='tasks'}
function openChange(changeId:string){focusChangeId.value=changeId;materialTab.value='changes'}
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
const stageNames:Record<string,string>={drafting:'草稿',charter_review:'立项评审',rejected:'已拒绝',designing:'方案细化',feasibility_review:'落地评审',executing:'执行中',on_hold:'已暂停',delivering:'交付验收',closing_review:'结项评审',closed:'已结项',retrospective:'复盘',terminated:'已终止',archived:'已归档'}
const healthNames={green:'绿',yellow:'黄',red:'红',untrusted:'数据不可信'}
const healthTags={green:'success',yellow:'warning',red:'danger',untrusted:'info'} as const
const healthPublication={official:'正式快照',preview:'未正式发布',withheld:'暂缓发布'}
function shanghai(value:string){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function clearSession(){sequence++;user.value=false;admin.value=false;loading.value=false; rows.value=[];total.value=0;page.value=1;query.value='';keyword.value='';stageFilter.value='';listReadAt.value='';selected.value=null;dialog.value=false;showMaterials.value=false;materialProject.value=null;csrf.value='';sessionStorage.removeItem('pmo.csrf')}
async function api(path:string,options:RequestInit={},receiveHeaders?:(h:Headers)=>void){
  const session=csrf.value
  const r=await fetch('/api/v1/'+path,{...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),'X-CSRF-Token':session,...options.headers},cache:'no-store'})
  if(!r.ok){
    if(r.status===401&&session===csrf.value&&path!=='auth/login')clearSession()
    const body=await r.json().catch(()=>({}))
    const descriptions:Record<string,string>={
      POLICY_PENDING:'该操作的业务策略尚待签核。',APPROVAL_STALE:'申请所依据的数据或授权已变化，请拒绝旧申请后重新提交。',
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
    }
    throw Error(descriptions[body.code]||(typeof body.code==='string'?`操作失败（${body.code}）`:`操作失败（${r.status}）`))
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
async function startSession(){admin.value=(await api('auth/me')).isSystemAdmin;user.value=true;await load()}
function enrollment(show:boolean){acceptingInvite.value=show;password.value='';error.value='';loginNotice.value=''}
function accountAccepted(name:string){enrollment(false);username.value=name;loginNotice.value='账号已开通，请登录。'}
async function login(){busy.value=true;error.value='';try{const r=await api('auth/login',{method:'POST',body:JSON.stringify({username:username.value,password:password.value})});csrf.value=r.csrfToken;sessionStorage.setItem('pmo.csrf',csrf.value);await startSession()}catch(e){error.value=message(e)}finally{password.value='';busy.value=false}}
async function logout(){try{await api('auth/logout',{method:'POST'});clearSession();error.value=''}catch(e){error.value=message(e)}}
function search(){page.value=1;keyword.value=query.value.trim();void load()}
function applyStage(event:Event){stageFilter.value=(event.currentTarget as HTMLSelectElement).value;search()}
function open(row:OverviewRow|null){if(row&&!row.canEditDraft)return;selected.value=row;code.value=row?.code||'';name.value=row?.name||'';description.value='';formError.value='';attempt='';lastPayload='';dialog.value=true}
async function reread(){const row=selected.value;if(!row)return;try{const {data}=await api('projects/'+row.projectId);selected.value={...row,...data.project};formError.value=data.project.stage==='drafting'?'':'项目已离开草稿阶段。';attempt=''}catch(e){formError.value=message(e)}}
async function save(){if(!name.value.trim()||(!selected.value&&!/^[A-Za-z0-9][A-Za-z0-9_-]{1,59}$/.test(code.value))){formError.value='请填写项目名称及有效编号（2 至 60 位字母、数字、下划线或短横线）。';return}
  busy.value=true;formError.value='';const row=selected.value;const body=JSON.stringify(row?{fields:{name:name.value.trim()}}:{code:code.value,name:name.value.trim(),description:description.value});const sig=body+':'+(row?.revision??0);if(!attempt||sig!==lastPayload){attempt=crypto.randomUUID();lastPayload=sig}
  try{await api(row?'projects/'+row.projectId:'projects',{method:row?'PATCH':'POST',headers:{'Idempotency-Key':attempt,...(row?{'If-Match':String(row.revision)}:{})},body});dialog.value=false;await load()}catch(e){formError.value=message(e)}finally{busy.value=false}}
onMounted(async()=>{try{if(csrf.value)await startSession()}catch(e){error.value=message(e)}finally{ready.value=true}})
</script>
<template>
<header><div class="brand"><Layers3 :size="21"/> PMO <b>Lite</b></div><span class="divider"></span><span>项目管理</span><el-button v-if="user" text class="logout" title="退出登录" aria-label="退出登录" @click="logout"><LogOut :size="17"/></el-button></header>
<main v-if="ready && !user" class="login">
  <AcceptOrgInvite v-if="acceptingInvite" :api="api" @back="enrollment(false)" @accepted="accountAccepted"/>
  <template v-else><div class="mark"><FolderKanban :size="27"/></div><h1>登录工作台</h1>
    <el-alert v-if="loginNotice" :title="loginNotice" type="success" :closable="false"/>
    <form @submit.prevent="login"><label>账号<input v-model="username" autocomplete="username" maxlength="120" required></label>
      <label>密码<input v-model="password" autocomplete="current-password" type="password" maxlength="256" required></label>
      <el-alert v-if="error" :title="error" type="error" :closable="false"/>
      <el-button type="primary" native-type="submit" :loading="busy">登录</el-button>
      <el-button text :disabled="busy" @click="enrollment(true)">接受组织邀请</el-button>
    </form>
  </template>
</main>
<main v-else-if="user" class="ledger"><div class="crumb">工作台 / 项目</div><section class="heading"><div><h1>项目台账</h1><span class="count">{{keyword||stageFilter?'筛选结果':'已授权项目'}} · {{total}}</span></div><el-button type="primary" @click="open(null)"><Plus :size="16"/> 新建项目</el-button></section>
<form class="toolbar project-filters" @submit.prevent="search">
  <label class="search"><Search :size="17"/><input aria-label="搜索项目名称或编号" placeholder="搜索项目名称或编号" maxlength="160" v-model="query"></label>
  <label class="stage-filter"><span>阶段</span><select aria-label="筛选项目阶段" :value="stageFilter" @change="applyStage"><option value="">全部</option><option v-for="(label,stage) in stageNames" :key="stage" :value="stage">{{label}}</option></select></label>
  <el-button native-type="submit">搜索</el-button><el-button title="刷新项目列表" aria-label="刷新项目列表" @click="load"><RefreshCw :size="15"/></el-button>
  <span v-if="listReadAt" class="list-read">列表读取 <time :datetime="listReadAt">{{shanghai(listReadAt)}} 北京时间</time></span>
</form>
<el-alert v-if="error" :title="error" type="error" :closable="false"/>
<el-table v-loading="loading" :data="rows" row-key="projectId" empty-text="没有符合条件的授权项目">
  <el-table-column prop="code" label="编号" min-width="110"/>
  <el-table-column label="项目名称" min-width="160"><template #default="{row}"><button class="project-link" :aria-label="`查看 ${row.name} 的项目资料`" @click="materials(row)">{{row.name}}</button></template></el-table-column>
  <el-table-column label="项目经理" min-width="95"><template #default="{row}">{{row.manager?.name??(row.materialsVisible?'无有效任命':'不可见')}}</template></el-table-column>
  <el-table-column label="阶段" min-width="92"><template #default="{row}"><el-tag type="info" effect="plain">{{stageNames[row.stage]??row.stage}}</el-tag></template></el-table-column>
  <el-table-column label="最近健康采样" min-width="184"><template #default="{row}: {row: OverviewRow}">
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
<el-pagination v-model:current-page="page" :page-size="20" :total="total" :disabled="loading" layout="prev, pager, next" @current-change="load"/>
<Appointments :projects="rows" :api="api" @changed="load"/>
<PreapprovalSpending :api="api"/>
<Alerts :api="api"/>
<IncomingRisk :api="api"/>
<TerminationConfirmations :api="api"/>
<PersonalApprovals :api="api" :stages="stageNames" @open-approval="openApproval"/>
<PersonalTasks :api="api" @open-task="openTask"/>
<AuditLog v-if="admin" :api="api"/>
<OrgDirectory v-if="admin" :api="api"/>
<AgentTokens :api="api" :projects="rows" :admin="admin"/>
<el-drawer v-model="showMaterials" :title="`${materialProject?.name??''} · 项目资料`" size="min(760px,100vw)" destroy-on-close>
  <el-tabs v-if="materialProject" v-model="materialTab" :key="materialProject.projectId">
    <el-tab-pane name="overview" label="概览"><ProjectOverview :project-id="materialProject.projectId" :active="showMaterials&&materialTab==='overview'" :stages="stageNames" :api="api" @stage-read="stageRead" @navigate="materialTab=$event" @open-change="openChange"/></el-tab-pane>
    <el-tab-pane name="milestones" label="里程碑" lazy><Milestones :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane name="requirements" label="需求" lazy><Requirements :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane name="baseline" label="需求基线" lazy><RequirementBaseline :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane name="exceptions" v-if="['delivering','closing_review'].includes(materialProject.stage)" label="需求例外" lazy><RequirementExceptions :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane name="changes" label="变更台账" lazy><Changes :project-id="materialProject.projectId" :focus-change-id="focusChangeId" :api="api" @clear-focus="focusChangeId=undefined"/></el-tab-pane>
    <el-tab-pane name="evidence" label="证据附件" lazy><Evidence :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane name="charter" label="立项卡" lazy><Charter :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane name="stakeholders" label="联系台账" lazy><Stakeholders :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane name="members" label="项目成员" lazy><Members :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane name="risks" label="风险" lazy><Risks :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane name="costs" label="成本" lazy><Costs :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane name="reviews" label="关口送审" lazy><Reviews :project-id="materialProject.projectId" :project-revision="materialProject.revision" :focus-review-id="focusReviewId" :stage-label="stageNames[materialProject.stage]??materialProject.stage" :api="api" @stage-read="stageRead"/></el-tab-pane>
    <el-tab-pane name="lifecycle" label="阶段流转" lazy><ProjectLifecycle :project-id="materialProject.projectId" :project-revision="materialProject.revision" :focus-approval-id="focusApprovalId" :api="api" @stage-read="stageRead" @clear-focus="focusApprovalId=undefined"/></el-tab-pane>
    <el-tab-pane name="tasks" label="补强任务" lazy><Tasks :project-id="materialProject.projectId" :focus-task-id="focusTaskId" :api="api" @clear-focus="focusTaskId=undefined"/></el-tab-pane>
    <el-tab-pane name="reports" label="日报周报" lazy><Reports :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane name="health" label="三线健康" lazy><Health :project-id="materialProject.projectId" :active="materialTab==='health'" :api="api"/></el-tab-pane>
    <el-tab-pane name="retrospective" label="复盘与教训" lazy><Retrospective :project-id="materialProject.projectId" :project-revision="materialProject.revision" :api="api"/></el-tab-pane>
    <el-tab-pane name="assistant" label="项目助理" lazy><ProjectAssistant v-if="materialTab==='assistant'" :project-id="materialProject.projectId" :revision="materialProject.revision" :api="api" @navigate="materialTab=$event"/></el-tab-pane>
  </el-tabs>
</el-drawer>
<el-dialog v-model="dialog" :title="title" width="min(440px,94vw)" :close-on-click-modal="false"><form @submit.prevent="save"><label>项目编号<input v-model="code" :disabled="!!selected" maxlength="60" required></label><label>项目名称<input v-model="name" maxlength="160" required></label><label v-if="!selected">说明<textarea v-model="description" maxlength="2000" rows="3"></textarea></label><el-alert v-if="formError" :title="formError" type="error" :closable="false"/><div class="actions"><el-button v-if="selected && formError" @click="reread">重新读取版本</el-button><el-button @click="dialog=false">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!!selected&&selected.stage!=='drafting'">保存</el-button></div></form></el-dialog></main>
</template>
<style scoped>
.project-link{font:inherit;color:#176b56;cursor:pointer;border:none;background:none;padding:3px 0;text-align:left;overflow-wrap:anywhere}.project-link:hover{text-decoration:underline}
.project-filters{flex-wrap:wrap}.stage-filter{display:flex;align-items:center;gap:.4rem}.stage-filter select{height:32px;min-width:7rem;border:1px solid #bac6c0;border-radius:4px;background:#fff;color:inherit;font:inherit;padding:0 .45rem}
.list-read,.sample time{color:#516069;font-size:.8rem}.list-read time,.sample time{white-space:nowrap}.sample time{display:block}
</style>
