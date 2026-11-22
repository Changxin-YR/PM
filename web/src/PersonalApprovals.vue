<script setup lang="ts">
import {ref} from 'vue'
import {ArrowRight,Inbox,RefreshCw} from 'lucide-vue-next'

const gates={charter:'立项评审',feasibility:'落地评审',closing:'结项评审'}
const labels={score:'待评分',decide:'可裁决',confirm_sponsor:'待 Sponsor 确认',confirm_initiator:'待发起人确认',
  approve_transition:'可批准',reject_transition:'可拒绝',approve_change:'可批准',reject_change:'可拒绝'}
const changeTargets={requirement:'需求',milestone:'里程碑',budget:'预算',charter:'立项卡'}
type Project={projectId:string;code:string;name:string;stage:string;revision:number;createdAt:string}
type Base={project:Project;submittedAt:string;actions:(keyof typeof labels)[]}
type Entry=Base&({kind:'review';reviewId:string;reviewType:keyof typeof gates}|{kind:'transition';approvalId:string;
  fromStage:string;toStage:string;awaitsInitiator:boolean}|{kind:'change';changeId:string;targetType:keyof typeof changeTargets})
type Page={items:Entry[];page:number;pageSize:number;total:number}
const props=defineProps<{api:(path:string,options?:RequestInit)=>Promise<any>;stages:Record<string,string>}>()
const emit=defineEmits<{openApproval:[project:Project,kind:Entry['kind'],id:string]}>()
const open=ref(false),loading=ref(false),error=ref(''),state=ref<Page|null>(null),readAt=ref('')
const format=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Shanghai'})
const time=(value:string)=>format.format(new Date(value))
const idFor=(item:Entry)=>item.kind==='review'?item.reviewId:item.kind==='transition'?item.approvalId:item.changeId
let readId=0

async function load(page=1){
  const id=++readId;loading.value=true;error.value=''
  try{
    const {data,meta}:{data:Page;meta:{asOf:string}}=await props.api(`me/approval-queue?page=${page}&pageSize=20`)
    if(id!==readId)return
    const last=Math.max(1,Math.ceil(data.total/data.pageSize))
    if(page>last){await load(last);return}
    state.value=data;readAt.value=meta.asOf
  }catch(e){
    if(id===readId){state.value=null;readAt.value='';error.value=e instanceof Error?e.message:'未能读取审批收件箱。'}
  }finally{if(id===readId)loading.value=false}
}
function show(){state.value=null;readAt.value='';open.value=true;void load()}
function select(item:Entry){open.value=false;emit('openApproval',item.project,item.kind,idFor(item))}
</script>

<template>
  <el-button @click="show"><Inbox :size="15"/> 我的审批收件箱</el-button>
  <el-dialog v-model="open" title="我的审批收件箱" width="min(650px,94vw)" append-to-body>
    <section v-loading="loading" class="queue" aria-label="本人评审、阶段与变更审批">
      <div class="queue-toolbar">
        <div v-if="state"><strong>可处理 · {{state.total}}</strong><small>读取于 <time :datetime="readAt">{{time(readAt)}} 北京时间</time></small></div>
        <el-button aria-label="刷新审批收件箱" title="刷新审批收件箱" :disabled="loading" @click="load(state?.page)"><RefreshCw :size="15"/></el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
      <p v-else-if="state&&!state.total&&!loading">暂无可处理的审批。</p>
      <ul v-if="state?.items.length" aria-label="可处理的审批">
        <li v-for="item in state.items" :key="`${item.kind}:${idFor(item)}`">
          <div class="approval-description">
            <strong v-if="item.kind==='review'">{{gates[item.reviewType]}}</strong>
            <strong v-else-if="item.kind==='transition'">{{stages[item.fromStage]}} → {{stages[item.toStage]}}</strong>
            <strong v-else>{{changeTargets[item.targetType]}}变更</strong>
            <small>{{item.project.name}} · {{item.project.code}}</small>
            <small>{{item.actions.map(action=>labels[action]).join(' / ')}} · <time :datetime="item.submittedAt">{{time(item.submittedAt)}}</time></small>
            <small v-if="item.kind==='transition'&&item.awaitsInitiator" class="waiting">发起人未确认，暂不可批准。</small>
          </div>
          <el-button :disabled="loading" size="small" @click="select(item)">打开记录<ArrowRight :size="14"/></el-button>
        </li>
      </ul>
      <nav v-if="state&&state.total>state.pageSize" aria-label="审批收件箱分页">
        <el-pagination size="small" :current-page="state.page" :total="state.total" :page-size="state.pageSize"
          :pager-count="5" :disabled="loading" layout="prev,pager,next" @current-change="load"/>
      </nav>
    </section>
  </el-dialog>
</template>

<style scoped>
.queue{min-height:5rem;overflow-wrap:anywhere}.queue-toolbar,li{display:flex;justify-content:space-between;align-items:center;gap:.7rem}
.queue-toolbar button{margin-left:auto}ul{list-style:none;margin:.5rem 0;padding:0;max-height:52vh;overflow:auto}
li{padding:.55rem 0;border-top:1px solid var(--el-border-color-light)}.approval-description{min-width:0}
small{display:block;color:var(--el-text-color-secondary);margin-top:.1rem}li button{flex:none}.waiting{color:var(--el-color-warning-dark-2)}
</style>
