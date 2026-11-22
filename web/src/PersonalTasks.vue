<script setup lang="ts">
import {ref} from 'vue'
import {ArrowRight,ClipboardList,RefreshCw} from 'lucide-vue-next'

const statuses={todo:'待办',in_progress:'进行中',blocked:'阻塞'}
const sources={review:'评审',rule_event:'规则事件',remediation:'风险整改',fact_clarification:'Fact 澄清',report_analysis:'报告人工复核'}
type Project={projectId:string;code:string;name:string;stage:string;revision:number;createdAt:string}
type Entry={project:Project;task:{taskId:string;title:string;status:keyof typeof statuses;sourceType:keyof typeof sources;
  sourceId:string;dueAt?:string}}
type Page={items:Entry[];page:number;pageSize:number;total:number}
const props=defineProps<{api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{openTask:[project:Project,taskId:string]}>()
const open=ref(false),loading=ref(false),error=ref(''),state=ref<Page|null>(null),readAt=ref('')
const dateFormat=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Shanghai'})
const time=(value:string)=>dateFormat.format(new Date(value))
let readId=0

async function load(page=1){
  const id=++readId;loading.value=true;error.value=''
  try{
    const {data,meta}:{data:Page;meta:{asOf:string}}=await props.api(`me/strengthening-tasks?page=${page}&pageSize=20`)
    if(id!==readId)return
    const last=Math.max(1,Math.ceil(data.total/data.pageSize))
    if(page>last){await load(last);return}
    state.value=data;readAt.value=meta.asOf
  }catch(e){
    if(id===readId){state.value=null;readAt.value='';error.value=e instanceof Error?e.message:'未能读取补强待办。'}
  }finally{if(id===readId)loading.value=false}
}
function show(){state.value=null;readAt.value='';open.value=true;void load()}
function select(entry:Entry){open.value=false;emit('openTask',entry.project,entry.task.taskId)}
</script>

<template>
  <el-button @click="show"><ClipboardList :size="15"/> 我的补强待办</el-button>
  <el-dialog v-model="open" title="我的补强待办" width="min(650px,94vw)" append-to-body>
    <section v-loading="loading" class="queue" aria-label="本人补强待办">
      <div class="queue-toolbar">
        <div v-if="state"><strong>未办结 · {{state.total}}</strong><small>读取于 <time :datetime="readAt">{{time(readAt)}} 北京时间</time></small></div>
        <el-button aria-label="刷新补强待办" title="刷新补强待办" :disabled="loading" @click="load(state?.page)"><RefreshCw :size="15"/></el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
      <p v-else-if="state&&!state.total&&!loading">暂无本人补强待办。</p>
      <ul v-if="state?.items.length" aria-label="待办任务">
        <li v-for="entry in state.items" :key="entry.task.taskId">
          <div class="task-description">
            <strong>{{entry.task.title}}</strong>
            <small>{{entry.project.name}} · {{entry.project.code}}</small>
            <small>{{statuses[entry.task.status]}} · {{sources[entry.task.sourceType]}} <code :title="entry.task.sourceId">{{entry.task.sourceId.slice(0,8)}}</code>
              · <time v-if="entry.task.dueAt" :datetime="entry.task.dueAt">截至 {{time(entry.task.dueAt)}}</time><span v-else>未设期限</span></small>
          </div>
          <el-button :disabled="loading" size="small" @click="select(entry)">查看任务<ArrowRight :size="14"/></el-button>
        </li>
      </ul>
      <nav v-if="state&&state.total>state.pageSize" aria-label="补强待办分页">
        <el-pagination size="small" :current-page="state.page" :total="state.total" :page-size="state.pageSize"
          :pager-count="5" :disabled="loading" layout="prev,pager,next" @current-change="load"/>
      </nav>
    </section>
  </el-dialog>
</template>

<style scoped>
.queue{min-height:5rem;overflow-wrap:anywhere}.queue-toolbar,li{display:flex;justify-content:space-between;align-items:center;gap:.7rem}
.queue-toolbar button{margin-left:auto}ul{list-style:none;margin:.5rem 0;padding:0;max-height:52vh;overflow:auto}
li{padding:.55rem 0;border-top:1px solid var(--el-border-color-light)}.task-description{min-width:0}
small{display:block;color:var(--el-text-color-secondary);margin-top:.1rem}li button{flex:none}
</style>
