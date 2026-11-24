<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {Mail, RefreshCw, Search, Send} from 'lucide-vue-next'
import {isUnconfirmedWrite} from './api-error'

const roleLabels={pm:'项目经理',sponsor:'Sponsor',member:'项目成员',reviewer:'评审人',client_representative:'客户代表'}
type Role=keyof typeof roleLabels
type Item={invitationId:string;projectId:string;projectName:string;nomineeName:string;role:Role;revision:number}
type ProjectChoice={projectId:string;name:string;code:string;revision:number;stage:string;roles:Role[]}
type PersonChoice={personId:string;name:string}
type Page<T>={items:T[];total:number}
type AppointmentWrite={path:string;revision:number;body:string;key:string;success:string;acceptedProject?:string}
const props=defineProps<{active?:boolean;openingProject?:boolean;api:(path:string,opts?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{changed:[];openProject:[id:string]}>()
const busy=ref(false),loading=ref(false),error=ref(''),notice=ref('')
const acceptedProject=ref<string>()
const inbox=ref<Item[]>([]),queue=ref<Item[]>([])
const pid=ref(''),person=ref(''),role=ref<Role|''>('')
const projects=ref<ProjectChoice[]>([]),projectPage=ref(1),projectTotal=ref(0),projectLoading=ref(false),projectError=ref('')
const pickerOpen=ref(false),people=ref<PersonChoice[]>([]),peoplePage=ref(1),peopleTotal=ref(0),peopleLoading=ref(false),peopleError=ref('')
const peopleQuery=ref(''),peopleKeyword=ref(''),chosenPerson=ref<PersonChoice>()
const retry=ref<AppointmentWrite>(),locked=computed(()=>busy.value||!!retry.value)
const selectedProject=computed(()=>projects.value.find(project=>project.projectId===pid.value))
const choices=computed<Role[]>(()=>selectedProject.value?.roles??[])
const projectLabel=computed(()=>projects.value.some(project=>project.stage!=='drafting')?'项目草稿或已有项目':'项目草稿')
const pageSize=20
let peopleRead=0

function msg(e:unknown){return e instanceof Error?e.message:'请求失败。'}
function roleName(value:Role){return roleLabels[value]??value}

watch(choices,options=>{if(!role.value||!options.includes(role.value))role.value=options[0]??''})

async function refreshQueues(){
  loading.value=true
  try{
    const [mine,todo]=await Promise.all([props.api('me/role-invitations'),props.api('me/appointment-queue')])
    inbox.value=mine.data;queue.value=todo.data
  }catch(e){inbox.value=[];queue.value=[];error.value=msg(e)}
  finally{loading.value=false}
}

async function readProjects(page=projectPage.value){
  projectLoading.value=true;projectError.value=''
  try{
    const {data}:{data:Page<ProjectChoice>}=await props.api(`onboarding/projects?page=${page}&pageSize=${pageSize}`)
    const last=Math.max(1,Math.ceil(data.total/pageSize))
    if(page>last){await readProjects(last);return}
    projects.value=data.items;projectPage.value=page;projectTotal.value=data.total
    if(!projects.value.some(project=>project.projectId===pid.value))pid.value=''
  }catch(e){projects.value=[];projectTotal.value=0;projectError.value=msg(e)}
  finally{projectLoading.value=false}
}

async function refresh(){
  error.value=''
  await Promise.all([refreshQueues(),readProjects()])
}

watch(()=>props.active,active=>{
  if(active===false||locked.value||loading.value||projectLoading.value)return
  notice.value='';acceptedProject.value=undefined;void refresh()
},{immediate:true})

async function readPeople(page=1){
  const read=++peopleRead
  peopleLoading.value=true;peopleError.value='';people.value=[]
  try{
    const params=new URLSearchParams({keyword:peopleKeyword.value,page:String(page),pageSize:String(pageSize)})
    const {data}:{data:Page<PersonChoice>}=await props.api(`onboarding/people?${params}`)
    if(read!==peopleRead)return
    const last=Math.max(1,Math.ceil(data.total/pageSize))
    if(page>last){await readPeople(last);return}
    people.value=data.items;peoplePage.value=page;peopleTotal.value=data.total
  }catch(e){if(read===peopleRead){peopleTotal.value=0;peopleError.value=msg(e)}}
  finally{if(read===peopleRead)peopleLoading.value=false}
}

function openPeople(){
  peopleRead++;peopleQuery.value='';peopleKeyword.value='';people.value=[];peopleTotal.value=0;peopleError.value=''
  peopleLoading.value=false;pickerOpen.value=true
}
function searchPeople(){const keyword=peopleQuery.value.trim();if(keyword.length>=2){peopleKeyword.value=keyword;void readPeople()}}
function pickPerson(item:PersonChoice){chosenPerson.value=item;person.value=item.personId;pickerOpen.value=false}

async function write(request:AppointmentWrite){
  if(busy.value)return
  busy.value=true;error.value='';notice.value='';acceptedProject.value=undefined
  try{
    await props.api(request.path,{method:'POST',headers:{'If-Match':String(request.revision),'Idempotency-Key':request.key},body:request.body})
    retry.value=undefined
    notice.value=request.success;acceptedProject.value=request.acceptedProject
    emit('changed');await refresh()
  }catch(e){
    if(isUnconfirmedWrite(e)){
      retry.value=request;error.value='未能确认任命请求结果，可重试上次提交。'
    }else{
      retry.value=undefined
      await refresh();error.value=msg(e)
    }
    emit('changed')
  }finally{busy.value=false}
}
function propose(){
  const project=selectedProject.value
  if(locked.value||!project||!role.value||!choices.value.includes(role.value))return
  const body=JSON.stringify({personId:person.value.trim(),role:role.value})
  const endpoint=role.value==='pm'||role.value==='sponsor'?'role-invitations':'member-invitations'
  void write({path:`projects/${project.projectId}/${endpoint}`,revision:project.revision,body,key:crypto.randomUUID(),success:'已提交提名，尚未授予项目权限。'})
}
function decide(item:Item,action:'issue'|'accept'|'decline'){
  if(locked.value)return
  const success={issue:'邀请已发出。',accept:'已接受任命。返回项目台账可打开已获授权的项目。',decline:'已拒绝任命。'}[action]
  void write({path:`role-invitations/${item.invitationId}/${action}`,revision:item.revision,body:'{}',key:crypto.randomUUID(),success,
    acceptedProject:action==='accept'?item.projectId:undefined})
}
</script>
<template>
  <section class="appointments" aria-label="人员任命">
    <div class="page-heading">
      <h1>人员任命</h1>
      <el-button aria-label="刷新任命列表" title="刷新任命列表" :disabled="loading||projectLoading||locked" @click="refresh"><RefreshCw :size="16"/> 刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <el-button v-if="retry" class="retry-appointment" :loading="busy" @click="write(retry)">重试上次提交</el-button>
    <div v-if="notice" class="appointment-notice">
      <p role="status">{{notice}}</p>
      <el-button v-if="acceptedProject" :loading="openingProject" @click="emit('openProject',acceptedProject)">打开已任命项目</el-button>
    </div>
    <div class="appointment-layout">
      <div v-loading="loading" class="appointment-queues">
        <section class="panel appointment-panel">
          <h2>我的邀请</h2>
          <p v-if="!loading&&!error&&!inbox.length" class="appointment-empty"><Mail :size="28" aria-hidden="true"/>暂无待确认邀请</p>
          <div v-for="i in inbox" :key="i.invitationId" class="item">
            <span>{{i.projectName}} · {{roleName(i.role)}}</span>
            <el-button :disabled="locked" @click="decide(i,'accept')">接受任命</el-button>
            <el-button :disabled="locked" @click="decide(i,'decline')">拒绝任命</el-button>
          </div>
        </section>
        <section class="panel appointment-panel">
          <h2>待我发出的邀请</h2>
          <p v-if="!loading&&!error&&!queue.length" class="appointment-empty"><Mail :size="28" aria-hidden="true"/>暂无待办</p>
          <div v-for="i in queue" :key="i.invitationId" class="item">
            <span>{{i.projectName}} · {{i.nomineeName}} · {{roleName(i.role)}}</span>
            <el-button :disabled="locked" @click="decide(i,'issue')"><Send :size="14"/> 发出邀请</el-button>
          </div>
        </section>
      </div>
      <section class="panel appointment-panel nomination">
        <h2>新提名</h2>
        <form @submit.prevent="propose">
          <div class="appointment-field">
            <label>{{projectLabel}}
              <select v-model="pid" required :disabled="locked||projectLoading||!projects.length">
                <option value="" disabled>选择项目</option>
                <option v-for="project in projects" :key="project.projectId" :value="project.projectId" :title="project.code">{{project.name}}</option>
              </select>
            </label>
            <p v-if="selectedProject" class="field-context">项目编号 <code>{{selectedProject.code}}</code></p>
            <p v-if="projectLoading" class="field-context" aria-live="polite">正在读取可任命的项目…</p>
            <el-alert v-else-if="projectError" :title="projectError" type="error" :closable="false" show-icon/>
            <p v-else-if="!projectTotal" class="field-context">暂无可由你提名的项目。可从项目台账新建本人草稿。</p>
            <el-pagination v-if="projectTotal>pageSize" aria-label="可提名项目分页" :current-page="projectPage" :total="projectTotal"
              :page-size="pageSize" :pager-count="5" :disabled="locked||projectLoading" layout="prev,pager,next" @current-change="readProjects"/>
          </div>
          <div class="appointment-field">
            <div class="person-entry">
              <label>被提名人 ID<input v-model="person" required maxlength="36" :disabled="locked"
                pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"></label>
              <el-button :disabled="locked||!selectedProject" @click="openPeople"><Search :size="16"/> 查找人员</el-button>
            </div>
            <p v-if="chosenPerson?.personId===person" class="field-context">已选择 {{chosenPerson.name}}</p>
          </div>
          <label>项目角色
            <select v-model="role" required :disabled="locked||!choices.length">
              <option v-if="!choices.length" value="" disabled>选择项目后查看可提名角色</option>
              <option v-for="option in choices" :key="option" :value="option">{{roleName(option)}}</option>
            </select>
          </label>
          <p v-if="role==='client_representative'" class="field-context">客户代表获得项目角色后，签署材料还需与本项目经核验的客户联系人建立绑定。</p>
          <el-button type="primary" native-type="submit" :disabled="!selectedProject||!choices.length||!!retry||projectLoading" :loading="busy">提交提名</el-button>
        </form>
      </section>
    </div>
    <el-dialog v-model="pickerOpen" title="查找被提名人" width="min(560px,94vw)" destroy-on-close>
      <form aria-label="查找被提名人" @submit.prevent="searchPeople">
        <div class="person-entry">
          <label>姓名关键词<input v-model="peopleQuery" maxlength="120" required minlength="2" autocomplete="off"></label>
          <el-button type="primary" native-type="submit" :disabled="peopleQuery.trim().length<2" :loading="peopleLoading"><Search :size="16"/> 搜索人员</el-button>
        </div>
      </form>
      <el-alert v-if="peopleError" :title="peopleError" type="error" :closable="false" show-icon/>
      <p v-else-if="!peopleKeyword" class="field-context">请输入至少 2 个字符，查询同组织的有效人员。</p>
      <p v-else-if="!peopleLoading&&!peopleTotal" class="field-context">没有匹配的人员。</p>
      <ul v-if="people.length" class="people-results" aria-label="人员搜索结果">
        <li v-for="candidate in people" :key="candidate.personId" class="item">
          <span>{{candidate.name}}<small><code>{{candidate.personId}}</code></small></span>
          <el-button size="small" :aria-label="`选择 ${candidate.name}`" @click="pickPerson(candidate)">选择</el-button>
        </li>
      </ul>
      <el-pagination v-if="peopleTotal>pageSize" aria-label="人员搜索分页" :current-page="peoplePage" :total="peopleTotal"
        :page-size="pageSize" :pager-count="5" :disabled="peopleLoading" layout="prev,pager,next" @current-change="readPeople"/>
    </el-dialog>
  </section>
</template>
<style scoped>
.appointment-layout{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:16px}
.appointment-queues,.nomination form{display:grid;gap:16px}
.appointment-queues .panel+.panel{margin-top:0}
.appointment-panel{min-width:0;padding:0}
.appointment-panel h2{margin:0;padding:16px 20px;border-bottom:1px solid var(--pmo-line)}
.nomination form{padding:20px;justify-items:start}
.nomination form label{width:100%}
.appointment-field{width:100%;min-width:0}
.appointment-field .el-pagination{margin-top:8px}
.field-context{margin:8px 0 0;color:var(--pmo-muted);font-size:13px;overflow-wrap:anywhere}
.person-entry{display:flex;align-items:end;flex-wrap:wrap;gap:8px}
.person-entry label,.nomination .person-entry label{flex:1 1 160px;width:auto;min-width:0}
.people-results{list-style:none;margin:16px 0 0;padding:0}
.people-results .item{padding-inline:0}
.people-results small{display:block;color:var(--pmo-muted);font-size:12px}
.retry-appointment{margin-bottom:16px}
.appointment-empty{display:grid;justify-items:center;align-content:center;gap:12px;min-height:126px;margin:0;padding:20px;color:var(--pmo-muted)}
.item{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:12px 20px}
.item+.item{border-top:1px solid var(--pmo-line)}
.item span{flex:1;min-width:min(130px,100%);overflow-wrap:anywhere}
.appointment-notice{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:0 0 16px;color:var(--pmo-primary-hover);font-weight:500}
.appointment-notice p{margin:0}
@media(max-width:760px){.appointment-layout{grid-template-columns:1fr}}
</style>
