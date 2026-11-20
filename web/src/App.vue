<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {Layers3,LogOut,Plus,RefreshCw,Search,Pencil,FolderKanban} from 'lucide-vue-next'
import Milestones from './Milestones.vue'
import Requirements from './Requirements.vue'
import RequirementBaseline from './RequirementBaseline.vue'
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
type Project={projectId:string;code:string;name:string;stage:string;revision:number;createdAt:string}
const user=ref(false),ready=ref(false),busy=ref(false),loading=ref(false),error=ref(''),formError=ref('')
const username=ref(''),password=ref(''),query=ref(''),keyword=ref(''),page=ref(1),total=ref(0),rows=ref<Project[]>([])
const dialog=ref(false),selected=ref<Project|null>(null),name=ref(''),code=ref(''),description=ref('')
const csrf=ref(sessionStorage.getItem('pmo.csrf')||'');let sequence=0,attempt='',lastPayload=''
const title=computed(()=>selected.value?'修改草稿名称':'新建项目草稿')
const admin=ref(false)
const materialProject=ref<Project|null>(null),showMaterials=ref(false)
function materials(row:Project){materialProject.value=row;showMaterials.value=true}
const stageNames:Record<string,string>={drafting:'草稿',charter_review:'立项评审',rejected:'已拒绝',designing:'方案细化',feasibility_review:'落地评审',executing:'执行中',on_hold:'已暂停',delivering:'交付验收',closing_review:'结项评审',closed:'已结项',retrospective:'复盘',terminated:'已终止',archived:'已归档'}
function clearSession(){ user.value=false;admin.value=false; rows.value=[];selected.value=null;dialog.value=false;showMaterials.value=false;materialProject.value=null;csrf.value='';sessionStorage.removeItem('pmo.csrf') }
async function api(path:string,options:RequestInit={},receiveHeaders?:(h:Headers)=>void){
  const r=await fetch('/api/v1/'+path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.value,...options.headers},cache:'no-store'})
  if(!r.ok){if(r.status===401)clearSession();const body=await r.json().catch(()=>({}));throw Error(({VERSION_CONFLICT:'版本已变化，请重新读取后再保存。',PROJECT_CODE_CONFLICT:'项目编号已存在。',UNAUTHORIZED:'请重新登录或核对账号密码。',FORBIDDEN:'没有此操作的权限。',RATE_LIMITED:'登录尝试过多，请稍后重试。'} as Record<string,string>)[body.code]||`操作失败（${r.status}）`)}
  const body=r.status===204?null:await r.json();receiveHeaders?.(r.headers);return body
}
function message(e:unknown){return e instanceof TypeError?'无法连接服务，请检查网络。':e instanceof Error?e.message:'操作失败。'}
async function load(){const id=++sequence;loading.value=true;error.value='';try{const search=new URLSearchParams({page:String(page.value),pageSize:'20','filters[keyword]':keyword.value});const {data}=await api('projects?'+search);if(id===sequence){rows.value=data.items;total.value=data.total}}catch(e){if(id===sequence){error.value=message(e);rows.value=[];total.value=0}}finally{if(id===sequence)loading.value=false}}
async function startSession(){admin.value=(await api('auth/me')).isSystemAdmin;user.value=true;await load()}
async function login(){busy.value=true;error.value='';try{const r=await api('auth/login',{method:'POST',body:JSON.stringify({username:username.value,password:password.value})});csrf.value=r.csrfToken;sessionStorage.setItem('pmo.csrf',csrf.value);await startSession()}catch(e){error.value=message(e)}finally{password.value='';busy.value=false}}
async function logout(){try{await api('auth/logout',{method:'POST'});clearSession();error.value=''}catch(e){error.value=message(e)}}
function search(){page.value=1;keyword.value=query.value.trim();void load()}
function open(row:Project|null){selected.value=row;code.value=row?.code||'';name.value=row?.name||'';description.value='';formError.value='';attempt='';lastPayload='';dialog.value=true}
async function reread(){if(!selected.value)return;try{selected.value=(await api('projects/'+selected.value.projectId)).data.project;formError.value='';attempt=''}catch(e){formError.value=message(e)}}
async function save(){if(!name.value.trim()||(!selected.value&&!/^[A-Za-z0-9][A-Za-z0-9_-]{1,59}$/.test(code.value))){formError.value='请填写项目名称及有效编号（2 至 60 位字母、数字、下划线或短横线）。';return}
  busy.value=true;formError.value='';const row=selected.value;const body=JSON.stringify(row?{fields:{name:name.value.trim()}}:{code:code.value,name:name.value.trim(),description:description.value});const sig=body+':'+(row?.revision??0);if(!attempt||sig!==lastPayload){attempt=crypto.randomUUID();lastPayload=sig}
  try{await api(row?'projects/'+row.projectId:'projects',{method:row?'PATCH':'POST',headers:{'Idempotency-Key':attempt,...(row?{'If-Match':String(row.revision)}:{})},body});dialog.value=false;await load()}catch(e){formError.value=message(e)}finally{busy.value=false}}
onMounted(async()=>{try{if(csrf.value)await startSession()}catch(e){error.value=message(e)}finally{ready.value=true}})
</script>
<template>
<header><div class="brand"><Layers3 :size="21"/> PMO <b>Lite</b></div><span class="divider"></span><span>项目管理</span><el-button v-if="user" text class="logout" title="退出登录" aria-label="退出登录" @click="logout"><LogOut :size="17"/></el-button></header>
<main v-if="ready && !user" class="login"><div class="mark"><FolderKanban :size="27"/></div><h1>登录工作台</h1><form @submit.prevent="login"><label>账号<input v-model="username" autocomplete="username" maxlength="120" required></label><label>密码<input v-model="password" autocomplete="current-password" type="password" maxlength="256" required></label><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-button type="primary" native-type="submit" :loading="busy">登录</el-button></form></main>
<main v-else-if="user" class="ledger"><div class="crumb">工作台 / 项目</div><section class="heading"><div><h1>项目台账</h1><span class="count">已授权项目 · {{total}}</span></div><el-button type="primary" @click="open(null)"><Plus :size="16"/> 新建项目</el-button></section>
<form class="toolbar" @submit.prevent="search"><label class="search"><Search :size="17"/><input aria-label="搜索项目名称或编号" placeholder="搜索项目名称或编号" v-model="query"></label><el-button native-type="submit">搜索</el-button><el-button title="刷新项目列表" aria-label="刷新项目列表" @click="load"><RefreshCw :size="15"/></el-button></form>
<el-alert v-if="error" :title="error" type="error" :closable="false"/><el-table v-loading="loading" :data="rows" row-key="projectId" empty-text="没有符合条件的授权项目"><el-table-column prop="code" label="编号" min-width="120"/><el-table-column label="项目名称" min-width="190"><template #default="{row}"><button class="project-link" :aria-label="`查看 ${row.name} 的项目资料`" @click="materials(row)">{{row.name}}</button></template></el-table-column><el-table-column label="阶段" min-width="92"><template #default="{row}"><el-tag type="info" effect="plain">{{stageNames[row.stage]??row.stage}}</el-tag></template></el-table-column><el-table-column prop="revision" label="版本" width="72"/><el-table-column label="创建日期" min-width="110"><template #default="{row}">{{new Date(row.createdAt).toLocaleDateString('zh-CN')}}</template></el-table-column><el-table-column label="操作" width="72"><template #default="{row}"><el-button v-if="row.stage==='drafting'" text title="修改草稿名称" aria-label="修改草稿名称" @click="open(row)"><Pencil :size="16"/></el-button></template></el-table-column></el-table>
<el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="load"/>
<Appointments :projects="rows" :api="api" @changed="load"/>
<PreapprovalSpending :api="api"/>
<Alerts :api="api"/>
<AuditLog v-if="admin" :api="api"/>
<AgentTokens :api="api" :projects="rows" :admin="admin"/>
<el-drawer v-model="showMaterials" :title="`${materialProject?.name??''} · 项目资料`" size="min(760px,100vw)" destroy-on-close>
  <el-tabs v-if="materialProject" :key="materialProject.projectId">
    <el-tab-pane label="里程碑"><Milestones :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane label="需求" lazy><Requirements :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane label="需求基线" lazy><RequirementBaseline :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane label="立项卡" lazy><Charter :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane label="联系台账" lazy><Stakeholders :project-id="materialProject.projectId" :stage="materialProject.stage" :api="api"/></el-tab-pane>
    <el-tab-pane label="项目成员" lazy><Members :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane label="风险" lazy><Risks :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
    <el-tab-pane label="成本" lazy><Costs :project-id="materialProject.projectId" :api="api"/></el-tab-pane>
  </el-tabs>
</el-drawer>
<el-dialog v-model="dialog" :title="title" width="min(440px,94vw)" :close-on-click-modal="false"><form @submit.prevent="save"><label>项目编号<input v-model="code" :disabled="!!selected" maxlength="60" required></label><label>项目名称<input v-model="name" maxlength="160" required></label><label v-if="!selected">说明<textarea v-model="description" maxlength="2000" rows="3"></textarea></label><el-alert v-if="formError" :title="formError" type="error" :closable="false"/><div class="actions"><el-button v-if="selected && formError" @click="reread">重新读取版本</el-button><el-button @click="dialog=false">取消</el-button><el-button type="primary" native-type="submit" :loading="busy">保存</el-button></div></form></el-dialog></main>
</template>
<style scoped>.project-link{font:inherit;color:#176b56;cursor:pointer;border:none;background:none;padding:3px 0;text-align:left}.project-link:hover{text-decoration:underline}</style>
