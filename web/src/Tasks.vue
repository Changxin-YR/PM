<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {Pencil,Plus,RefreshCw,Save} from 'lucide-vue-next'

const sourceLabels={review:'评审',rule_event:'规则事件',remediation:'风险整改'}
const endpoints={review:'reviews',rule_event:'alerts',remediation:'risks'}
const statusLabels={todo:'待办',in_progress:'进行中',blocked:'阻塞',done:'已完成',cancelled:'已取消'}
const statusTone={todo:'info',in_progress:'primary',blocked:'danger',done:'success',cancelled:'info'} as const
const reviewLabels:Record<string,string>={charter:'立项评审',feasibility:'落地评审',closing:'结项评审'}
type Source=keyof typeof sourceLabels
type Status=keyof typeof statusLabels
type Task={taskId:string;projectId:string;title:string;status:Status;revision:number;sourceType:Source;sourceId:string;
  assigneeId?:string;dueAt?:string;completedAt?:string;canUpdate:boolean}
type Page={items:Task[];page:number;pageSize:number;total:number;canCreate:boolean;projectRevision:number}
type Member={personId:string;name:string}
type SourceRow={reviewId?:string;riskId?:string;alertId?:string;type?:string;statement?:string;ruleId?:string;milestoneName?:string}
type Choice={id:string;label:string}
const props=defineProps<{projectId:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const path=computed(()=>
  'projects/'+props.projectId+'/tasks')
const pageSize=20,rows=ref<Task[]>([]),page=ref(1),total=ref(0),canCreate=ref(false),projectRevision=ref(0)
const loading=ref(false),busy=ref(false),error=ref(''),formError=ref(''),notice=ref('')
const mode=ref<'create'|'update'|null>(null),editing=ref<Task|null>(null),createRevision=ref(0)
const title=ref(''),sourceType=ref<Source>('review'),sourceId=ref(''),assigneeId=ref(''),due=ref(''),status=ref<Status>('todo')
const members=ref<Member[]>([]),membersLoading=ref(false),memberError=ref('')
const sources=ref<Choice[]>([]),sourcesLoading=ref(false),sourceError=ref('')
let loadId=0,sourceLoadId=0,memberLoadId=0,key='',lastRequest=''
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const dateFormat=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'})
const message=(e:unknown)=>e instanceof TypeError?'无法连接服务，请检查网络。':e instanceof Error?e.message:'任务操作失败。'

async function load(next=page.value){
  const id=++loadId;loading.value=true;error.value=''
  try{
    const {data}:{data:Page}=await props.api(path.value+'?page='+next+'&pageSize='+pageSize)
    if(id!==loadId)return
    if(next>1&&!data.items.length){await load(Math.max(1,Math.ceil(data.total/pageSize)));return}
    rows.value=data.items;page.value=data.page;total.value=data.total
    canCreate.value=data.canCreate;projectRevision.value=data.projectRevision
  }catch(e){
    if(id===loadId){rows.value=[];total.value=0;canCreate.value=false;error.value=message(e)}
  }finally{if(id===loadId)loading.value=false}
}

async function loadMembers(){
  const id=++memberLoadId;membersLoading.value=true;memberError.value=''
  try{
    const {data}:{data:{items:Member[]}}=await props.api('projects/'+props.projectId+'/members?pageSize=100')
    if(id===memberLoadId)members.value=[...new Map(data.items.map(row=>[row.personId,row])).values()]
  }catch(e){if(id===memberLoadId){members.value=[];memberError.value=message(e)}}
  finally{if(id===memberLoadId)membersLoading.value=false}
}

async function loadSources(){
  const id=++sourceLoadId,type=sourceType.value
  sources.value=[];sourcesLoading.value=true;sourceError.value=''
  try{
    const {data}:{data:{items:SourceRow[]}}=await props.api('projects/'+props.projectId+'/'+endpoints[type]+'?pageSize=100')
    if(id!==sourceLoadId)return
    sources.value=data.items.flatMap(row=>{
      const source=type==='review'?row.reviewId:type==='rule_event'?row.alertId:row.riskId
      if(!source)return []
      const name=type==='review'?(reviewLabels[row.type??'']??sourceLabels[type]):type==='rule_event'?
        [row.ruleId,row.milestoneName].filter(Boolean).join(' · '):row.statement
      return [{id:source,label:name?name+' · '+source.slice(0,8):source}]
    })
  }catch(e){if(id===sourceLoadId)sourceError.value=message(e)}
  finally{if(id===sourceLoadId)sourcesLoading.value=false}
}

function localDate(value?:string){
  if(!value)return ''
  const date=new Date(value)
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16)
}
function memberName(id:string){return members.value.find(row=>row.personId===id)?.name??id}
function closeForm(){mode.value=null;editing.value=null;formError.value='';key='';lastRequest='';sourceLoadId++}
function openForm(row:Task|null){
  if(row?!row.canUpdate:!canCreate.value)return
  mode.value=row?'update':'create';editing.value=row;createRevision.value=projectRevision.value
  title.value=row?.title??'';due.value=localDate(row?.dueAt);status.value=row?.status??'todo'
  sourceType.value='review';sourceId.value='';assigneeId.value='';formError.value='';notice.value='';key='';lastRequest=''
  if(!row)void loadSources()
}
function changeSource(value:Source){sourceType.value=value;sourceId.value='';void loadSources()}
function refresh(){notice.value='';void load();void loadMembers()}

async function save(){
  if(!mode.value||busy.value)return
  const row=editing.value,pid=props.projectId,revision=row?.revision??createRevision.value
  const target=row?'tasks/'+row.taskId:path.value
  const dueChanged=due.value!==localDate(row?.dueAt),date=due.value?new Date(due.value):null
  formError.value=''
  if(date&&Number.isNaN(date.getTime())){formError.value='到期时间无效。';return}
  let command:object
  if(row){
    if(row.dueAt&&!due.value){formError.value='已有的到期时间不能清空。';return}
    const fieldsTask={...(status.value!==row.status?{status:status.value}:{}),...(date&&dueChanged?{dueAt:date.toISOString()}:{})}
    if(!Object.keys(fieldsTask).length){formError.value='没有需要保存的更改。';return}
    command={fieldsTask}
  }else{
    const name=title.value.trim(),source=sourceId.value.trim(),assignee=assigneeId.value.trim()
    if(!name||!uuid.test(source)||(assignee&&!uuid.test(assignee))){formError.value='请填写标题、有效的来源及责任人。';return}
    command={title:name,sourceType:sourceType.value,sourceId:source,...(assignee?{assigneeId:assignee}:{}),
      ...(date?{dueAt:date.toISOString()}:{})}
  }
  const body=JSON.stringify(command),signature=JSON.stringify([target,revision,body])
  if(!key||signature!==lastRequest){key=crypto.randomUUID();lastRequest=signature}
  busy.value=true
  try{
    await props.api(target,{method:row?'PATCH':'POST',headers:{'Idempotency-Key':key,'If-Match':String(revision)},body})
    if(props.projectId===pid){closeForm();notice.value=row?'任务已更新。':'补强任务已创建。';await load(row?page.value:1)}
  }catch(e){if(props.projectId===pid)formError.value=message(e)}
  finally{if(props.projectId===pid)busy.value=false}
}

watch(()=>props.projectId,()=>{
  closeForm();busy.value=false;canCreate.value=false;notice.value='';rows.value=[];members.value=[];total.value=0
  void load(1);void loadMembers()
},{immediate:true})
</script>

<template>
  <section class="task-panel" aria-label="补强任务">
    <div class="toolbar task-toolbar">
      <span>补强任务 · {{total}}</span>
      <el-button v-if="canCreate" :disabled="!!mode||loading||busy" @click="openForm(null)"><Plus :size="15"/>新建补强任务</el-button>
      <el-button title="刷新补强任务" aria-label="刷新补强任务" :disabled="!!mode||loading||busy" @click="refresh"><RefreshCw :size="15"/></el-button>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
    <p v-if="notice" class="task-notice" role="status">{{notice}}</p>
    <form v-if="mode" class="task-form" @submit.prevent="save">
      <h3>{{editing?editing.title:'新建补强任务'}}</h3>
      <fieldset :disabled="busy">
        <template v-if="mode==='create'">
          <label class="wide">标题<input v-model="title" maxlength="200" required></label>
          <label>来源类型
            <el-select :model-value="sourceType" aria-label="来源类型" :disabled="busy" @change="changeSource">
              <el-option v-for="(label,value) in sourceLabels" :key="value" :value="value" :label="label"/>
            </el-select>
          </label>
          <label>来源记录
            <el-select v-model="sourceId" aria-label="来源记录" :disabled="busy" :loading="sourcesLoading"
              filterable allow-create default-first-option placeholder="选择记录或输入 ID">
              <el-option v-for="choice in sources" :key="choice.id" :value="choice.id" :label="choice.label"/>
            </el-select>
          </label>
          <el-alert v-if="sourceError" class="wide" type="warning" :title="'未能读取来源：'+sourceError" :closable="false"/>
          <label>责任人
            <el-select v-model="assigneeId" aria-label="责任人" :disabled="busy" :loading="membersLoading"
              filterable allow-create default-first-option clearable placeholder="暂不分配">
              <el-option v-for="member in members" :key="member.personId" :value="member.personId" :label="member.name"/>
            </el-select>
          </label>
          <el-alert v-if="memberError" class="wide" type="warning" :title="'未能读取项目成员：'+memberError" :closable="false"/>
        </template>
        <label v-else>状态
          <select v-model="status" aria-label="任务状态" :disabled="busy">
            <option v-for="(label,value) in statusLabels" :key="value" :value="value">{{label}}</option>
          </select>
        </label>
        <label>到期时间<input v-model="due" type="datetime-local"></label>
      </fieldset>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon/>
      <div class="task-actions">
        <el-button :disabled="busy" @click="closeForm">取消</el-button>
        <el-button native-type="submit" type="primary" :loading="busy"><Save :size="15"/>保存任务</el-button>
      </div>
    </form>
    <el-table :data="rows" row-key="taskId" v-loading="loading" :empty-text="error?'任务未读取':'暂无补强任务'">
      <el-table-column label="任务" min-width="170">
        <template #default="{row}"><strong class="task-title">{{row.title}}</strong><small>版本 {{row.revision}}</small></template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="scope"><el-tag size="small" effect="plain" :type="statusTone[(scope.row as Task).status]">{{statusLabels[(scope.row as Task).status]}}</el-tag></template>
      </el-table-column>
      <el-table-column label="责任人" min-width="118">
        <template #default="{row}"><span class="task-title">{{row.assigneeId?memberName(row.assigneeId):'未分配'}}</span></template>
      </el-table-column>
      <el-table-column label="到期 / 完成" min-width="145">
        <template #default="{row}">
          <time v-if="row.dueAt" :datetime="row.dueAt">{{dateFormat.format(new Date(row.dueAt))}}</time><span v-else>未设到期时间</span>
          <small v-if="row.completedAt">完成：<time :datetime="row.completedAt">{{dateFormat.format(new Date(row.completedAt))}}</time></small>
        </template>
      </el-table-column>
      <el-table-column label="来源" min-width="104">
        <template #default="scope"><span :title="scope.row.sourceId">{{sourceLabels[(scope.row as Task).sourceType]}}<small>{{scope.row.sourceId?.slice(0,8)}}</small></span></template>
      </el-table-column>
      <el-table-column label="操作" width="64">
        <template #default="{row}">
          <el-button v-if="row.canUpdate" text title="编辑任务" aria-label="编辑任务" :disabled="!!mode||busy||loading" @click="openForm(row)"><Pencil :size="15"/></el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-if="total>pageSize" :current-page="page" :total="total" :page-size="pageSize" layout="prev,pager,next"
      :disabled="!!mode||loading||busy" @current-change="load"/>
  </section>
</template>

<style scoped>
.task-toolbar{flex-wrap:wrap}
.task-toolbar span{margin-right:auto}
.task-toolbar svg,.task-actions svg{margin-right:4px}
.task-toolbar button:last-child svg{margin-right:0}
.task-notice{margin:8px 0;color:var(--el-color-success-dark-2)}
.task-form{display:grid;gap:12px;padding:12px 0}
h3{margin:0;font-size:15px;overflow-wrap:anywhere}
fieldset{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:0;padding:0;border:0;min-width:0}
.wide{grid-column:1/-1}
.task-actions{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:8px}
.task-actions button{margin:0}
.task-title{overflow-wrap:anywhere}
small{display:block;color:var(--el-text-color-secondary);line-height:1.4}
@media (max-width:520px){fieldset{grid-template-columns:minmax(0,1fr)}}
</style>
