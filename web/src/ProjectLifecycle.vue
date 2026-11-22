<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {ArrowRight,Gavel,Play,RefreshCw,RotateCcw,Send} from 'lucide-vue-next'

type Entry={requestId:string;projectId:string;fromStage:string;toStage:string;status:string;revision:number;
  requestedBy:string;requestedAt:string;reason:string;canExecute:boolean;canDecide:boolean;stale:boolean;
  decisionId?:string;decisionReason?:string;authorityRef?:string;authoritySourceRef?:string;
  holdUntil?:string;awaitsInitiator?:boolean;initiatorConfirmedAt?:string}
type Inbox={items:Entry[];projectId:string;projectRevision:number;stage:string;requestable:string[];canResubmit:boolean;policyPending:boolean}
type Mode='request'|'execute'|'decide'|'resubmit'
type Intent={mode:Mode;revision:number;fromStage:string;toStage?:string;requestId?:string}
type Decision='approve'|'reject'

const props=defineProps<{projectId:string;projectRevision?:number;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{stageRead:[projectId:string,stage:string,revision:number]}>()
const stages:Record<string,string>={drafting:'草稿',charter_review:'立项评审',rejected:'已拒绝',designing:'方案细化',
  feasibility_review:'落地评审',executing:'执行中',on_hold:'已暂停',delivering:'交付验收',closing_review:'结项评审',
  closed:'已结项',retrospective:'复盘',terminated:'已终止',archived:'已归档'}
const statuses:Record<string,string>={pending:'待处理',rejected:'已拒绝',applied:'已执行'}
const modes={
  request:{title:'申请阶段流转',submit:'提交流转申请',icon:Send},
  execute:{title:'执行阶段流转',submit:'确认执行流转',icon:Play},
  decide:{title:'裁决阶段流转',submit:'确认流转裁决',icon:Gavel},
  resubmit:{title:'退回草稿重提',submit:'确认退回草稿',icon:RotateCcw},
}
const state=ref<Inbox|null>(null),loading=ref(false),saving=ref(false),dialog=ref(false)
const error=ref(''),formError=ref(''),notice=ref('')
const intent=ref<Intent|null>(null),destination=ref(''),reason=ref(''),decision=ref<Decision|''>(''),holdUntil=ref('')
const busy=computed(()=>loading.value||saving.value)
const outdated=computed(()=>!!state.value&&(props.projectRevision??0)>state.value.projectRevision)
const destinations=computed(()=>state.value?.requestable??[])
let attempt:{signature:string;key:string}|undefined

function stageName(stage:string){return stages[stage]??stage}
function time(iso:string){return new Date(iso).toLocaleString('zh-CN',{hour12:false})}
function message(e:unknown,fallback:string){return e instanceof TypeError?'无法连接服务，请检查网络。':e instanceof Error?e.message:fallback}
function eligible(mode:'execute'|'decide',item?:Entry){
  return !!item&&!!state.value&&item.status==='pending'&&!item.stale&&item.revision===state.value.projectRevision&&
    (mode==='execute'?item.canExecute===true:item.canDecide===true)
}
async function read(){
  const params=new URLSearchParams({projectId:props.projectId})
  const {data}:{data:Inbox}=await props.api(`approvals?${params}`)
  state.value=data
  if(!outdated.value)emit('stageRead',data.projectId,data.stage,data.projectRevision)
}
async function refresh(){
  if(busy.value)return
  loading.value=true;error.value=''
  try{await read()}catch(e){state.value=null;error.value=message(e,'未能读取阶段流转。')}finally{loading.value=false}
}
onMounted(refresh)
watch(()=>props.projectRevision,revision=>{if(revision!==state.value?.projectRevision)void refresh()})

function begin(mode:Mode,item?:Entry){
  const inbox=state.value
  if(!inbox||busy.value||outdated.value)return
  if(mode==='request'&&!destinations.value.length||mode==='resubmit'&&!inbox.canResubmit)return
  if((mode==='execute'||mode==='decide')&&!eligible(mode,item))return
  intent.value={mode,revision:inbox.projectRevision,fromStage:inbox.stage,
    toStage:mode==='resubmit'?'drafting':item?.toStage,requestId:item?.requestId}
  destination.value=destinations.value.length===1?destinations.value[0]??'':''
  reason.value='';decision.value='';holdUntil.value='';formError.value='';notice.value='';attempt=undefined;dialog.value=true
}
const needsHold=computed(()=>intent.value?.mode==='decide'&&intent.value.toStage==='on_hold'&&decision.value==='approve')
const awaitsInitiator=computed(()=>state.value?.items.some(row=>row.requestId===intent.value?.requestId&&row.awaitsInitiator)??false)
const command=computed(()=>{
  const inbox=state.value,action=intent.value,why=reason.value.trim()
  if(!inbox||!action||outdated.value||inbox.projectRevision!==action.revision||inbox.stage!==action.fromStage)return null
  const base=`projects/${props.projectId}`
  if(action.mode==='request')return destinations.value.includes(destination.value)&&why.length>=3&&why.length<=2000?
    {path:`${base}/transition-requests`,payload:{toStage:destination.value,reason:why}}:null
  if(action.mode==='resubmit')return inbox.canResubmit&&why.length>=4&&why.length<=2000?
    {path:`${base}/resubmit`,payload:{reason:why}}:null
  const item=inbox.items.find(row=>row.requestId===action.requestId)
  if(!item||!eligible(action.mode,item))return null
  if(action.mode==='execute')return {path:`${base}/transitions`,payload:{toStage:item.toStage,approvalRequestId:item.requestId}}
  if(!decision.value||why.length<4||why.length>2000||decision.value==='approve'&&item.awaitsInitiator)return null
  const timestamp=Date.parse(holdUntil.value)
  if(needsHold.value&&!Number.isFinite(timestamp))return null
  return {path:`approvals/${item.requestId}/decide`,payload:{decision:decision.value,rationale:why,
    ...(needsHold.value?{holdUntil:new Date(timestamp).toISOString()}:{})}}
})

async function save(){
  const action=intent.value,write=command.value
  if(busy.value||!action||!write)return
  const body=JSON.stringify(write.payload),signature=JSON.stringify([write.path,action.revision,body])
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  saving.value=true;formError.value='';error.value=''
  let saved=false
  try{
    const {data}:{data:{status:string}}=await props.api(write.path,{method:'POST',body,
      headers:{'If-Match':String(action.revision),'Idempotency-Key':attempt.key}})
    saved=true;dialog.value=false;attempt=undefined
    notice.value=action.mode==='request'?'阶段流转申请已登记。':data.status==='rejected'?'阶段流转申请已拒绝。':
      action.mode==='resubmit'?'已退回草稿。':'阶段流转已执行。'
    await read()
  }catch(e){
    if(saved){state.value=null;error.value='操作已提交，但未能重读项目阶段和流转记录。'}
    else formError.value=message(e,'未能确认提交结果。')
  }finally{saving.value=false}
}
</script>

<template>
  <section class="lifecycle" aria-label="项目生命周期" :aria-busy="busy">
    <div class="toolbar lifecycle-toolbar">
      <div v-if="state" class="stage-heading">
        <strong>当前阶段：{{stageName(state.stage)}}</strong>
        <span class="meta">项目版本 {{state.projectRevision}}</span>
        <el-tag v-if="state.policyPending" size="small" type="warning">审批策略待签核</el-tag>
      </div>
      <div class="controls">
        <el-button v-if="destinations.length" type="primary" :disabled="busy||outdated" @click="begin('request')"><Send :size="15"/>申请阶段流转</el-button>
        <el-button v-if="state?.canResubmit" :disabled="busy||outdated" @click="begin('resubmit')"><RotateCcw :size="15"/>退回草稿重提</el-button>
        <el-button aria-label="刷新阶段流转" title="刷新阶段流转" :disabled="busy" @click="refresh"><RefreshCw :size="15"/></el-button>
      </div>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
    <el-alert v-else-if="outdated" type="warning" title="项目版本已变化，阶段流转记录需要重新读取。" :closable="false" show-icon/>
    <p v-if="notice" role="status" class="notice">{{notice}}</p>
    <p v-if="loading" class="meta">正在读取阶段流转…</p>
    <h3>阶段流转记录</h3>
    <p v-if="state&&!state.items.length" class="meta">暂无阶段流转记录。</p>
    <ul v-if="state?.items.length" class="entries">
      <li v-for="item in state.items" :key="item.requestId">
        <div class="entry-heading">
          <strong class="edge">{{stageName(item.fromStage)}} <ArrowRight :size="14" aria-hidden="true"/> {{stageName(item.toStage)}}</strong>
          <el-tag size="small" :type="item.status==='applied'?'success':item.status==='rejected'?'danger':'info'">{{statuses[item.status]??item.status}}</el-tag>
          <el-tag v-if="item.stale" size="small" type="warning">依据已失效</el-tag>
          <el-tag v-else-if="item.awaitsInitiator" size="small" type="warning">待发起人确认</el-tag>
          <time class="meta" :datetime="item.requestedAt">{{time(item.requestedAt)}}</time>
        </div>
        <dl class="detail-lines"><dt>申请原因</dt><dd>{{item.reason}}</dd>
          <template v-if="item.holdUntil"><dt>暂停截至</dt><dd>{{time(item.holdUntil)}}</dd></template>
          <template v-if="item.initiatorConfirmedAt"><dt>发起人确认</dt><dd>{{time(item.initiatorConfirmedAt)}}</dd></template>
          <template v-if="item.decisionReason"><dt>裁决理由</dt><dd>{{item.decisionReason}}</dd></template>
        </dl>
        <details class="meta"><summary>记录来源</summary>
          <dl class="detail-lines"><dt>申请人</dt><dd>{{item.requestedBy}}</dd><dt>申请编号</dt><dd>{{item.requestId}}</dd>
            <dt>申请时项目版本</dt><dd>{{item.revision}}</dd>
            <template v-if="item.decisionId"><dt>裁决编号</dt><dd>{{item.decisionId}}</dd></template>
            <template v-if="item.authorityRef"><dt>裁决权限依据</dt><dd>{{item.authorityRef}}</dd></template>
            <template v-if="item.authoritySourceRef"><dt>组织授权记录</dt><dd>{{item.authoritySourceRef}}</dd></template>
          </dl>
        </details>
        <div v-if="eligible('execute',item)||eligible('decide',item)" class="entry-actions">
          <el-button v-if="eligible('execute',item)" :disabled="busy||outdated" @click="begin('execute',item)"><Play :size="15"/>执行阶段流转</el-button>
          <el-button v-if="eligible('decide',item)" :disabled="busy||outdated" @click="begin('decide',item)"><Gavel :size="15"/>裁决阶段流转</el-button>
        </div>
      </li>
    </ul>
  </section>

  <el-dialog v-model="dialog" :title="intent?modes[intent.mode].title:''" width="min(550px,94vw)" append-to-body destroy-on-close
    :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
    <form v-if="intent" class="lifecycle-form" @submit.prevent="save">
      <fieldset :disabled="busy||outdated">
        <legend>项目版本 {{intent.revision}}</legend>
        <p class="edge">{{stageName(intent.fromStage)}} <template v-if="intent.toStage"><ArrowRight :size="14" aria-hidden="true"/> {{stageName(intent.toStage)}}</template></p>
        <label v-if="intent.mode==='request'">目标阶段<select aria-label="目标阶段" v-model="destination" required>
          <option value="" disabled>请选择</option><option v-for="stage in destinations" :key="stage" :value="stage">{{stageName(stage)}}</option>
        </select></label>
        <label v-if="intent.mode==='decide'">流转裁决<select aria-label="流转裁决" v-model="decision" required>
          <option value="" disabled>请选择</option><option value="approve" :disabled="awaitsInitiator">{{awaitsInitiator?'通过（待发起人确认）':'通过'}}</option><option value="reject">拒绝</option>
        </select></label>
        <label v-if="intent.mode!=='execute'">{{intent.mode==='decide'?'裁决理由':intent.mode==='resubmit'?'重提理由':'申请原因'}}
          <textarea v-model="reason" rows="3" :minlength="intent.mode==='request'?3:4" maxlength="2000" required/>
        </label>
        <label v-if="needsHold">暂停至（本地时间）<input v-model="holdUntil" type="datetime-local" required/></label>
      </fieldset>
      <el-alert v-if="formError" type="error" :title="formError" :closable="false" show-icon/>
      <div class="actions">
        <el-button :disabled="saving" @click="dialog=false">取消</el-button>
        <el-button type="primary" native-type="submit" :disabled="busy||!command" :loading="saving"><component :is="modes[intent.mode].icon" :size="15"/>{{modes[intent.mode].submit}}</el-button>
      </div>
    </form>
  </el-dialog>
</template>

<style scoped>
.lifecycle{overflow-wrap:anywhere}
.lifecycle-toolbar,.stage-heading,.controls,.entry-heading,.entry-actions,.edge{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.lifecycle-toolbar{justify-content:space-between}
.controls,.entry-actions{margin-left:auto}
.meta,.lifecycle-form legend{color:var(--el-text-color-secondary);font-size:13px}
.notice{color:var(--el-color-primary)}
.lifecycle h3{margin:.9rem 0 .35rem}
.entries{padding:0;margin:.3rem 0;list-style:none}
.entries li{padding:.7rem 0;border-top:1px solid var(--el-border-color-lighter)}
.detail-lines{margin:.45rem 0;display:grid;grid-template-columns:auto minmax(0,1fr);gap:.3rem .65rem}
.detail-lines dd{margin:0;white-space:pre-wrap}
.entry-actions{margin-top:.45rem}
summary{cursor:pointer}
.lifecycle-form,.lifecycle-form fieldset{display:grid;gap:.7rem}
.lifecycle-form fieldset{border:0;padding:0;margin:0;min-width:0}
.lifecycle-form legend{padding:0;margin:0 0 .45rem}
.lifecycle-form p{margin:0;overflow-wrap:anywhere}
.controls :deep(.el-button),.entry-actions :deep(.el-button){margin-left:0}
</style>
