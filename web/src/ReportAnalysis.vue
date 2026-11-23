<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {ClipboardCheck,RefreshCw,ScanText} from 'lucide-vue-next'

type Status='queued'|'running'|'ready'|'manual'|'stale'
type Suggestion={subject:string;predicate:string;typedValue:string;quote:string;asOf:string;sourceRef:string;quotePath:string;quoteOffset:number}
type Run={runId:string;status:Status;attempts:number;reportRevision:number;requestedAt:string;errorCode?:string;fallbackTaskId?:string;candidates:Suggestion[]}
type Page={items:Run[];total:number;page:number;pageSize:number;reportRevision:number;canAnalyze:boolean;modelEnabled:boolean;activeRunId?:string}
const props=defineProps<{reportId:string;revision:number;api:(path:string,options?:RequestInit)=>Promise<any>;canSelect:boolean}>()
const emit=defineEmits<{choose:[Suggestion]}>()
const page=ref<Page>(), error=ref(''), reading=ref(false), sending=ref(false)
const status:Record<Status,string>={queued:'排队中',running:'分析中',ready:'建议已就绪',manual:'待人工处理',stale:'分析已失效'}
const reasons:Record<string,string>={MODEL_DISABLED:'模型已关闭',MODEL_TIMEOUT:'分析超时',MODEL_UNAVAILABLE:'模型网关不可用',MODEL_SCOPE_BLOCKED:'当前项目不在模型外发范围内',
  MODEL_SCHEMA_INVALID:'输出格式不合规',MODEL_SOURCE_INVALID:'无法核对输出出处',MODEL_LEASE_EXHAUSTED:'分析进程重试已用尽',
  ANALYSIS_AUTHORITY_CHANGED:'请求人的会话或项目授权已变化',ANALYSIS_SOURCE_CHANGED:'报告版本已变化'}
const retry=computed(()=>page.value?.items.some(item=>item.reportRevision===props.revision&&item.status!=='stale')??false)
let timer:ReturnType<typeof setTimeout>|undefined, epoch=0, seq=0, attempt:{signature:string;key:string}|undefined
function showTime(at:string){return new Date(at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function cancelTimer(){if(timer!==undefined)clearTimeout(timer);timer=undefined}
function problem(e:unknown){return e instanceof Error?e.message:'请求失败。'}
async function read(n=page.value?.page??1){
  const ticket=++seq,scope=epoch
  cancelTimer();reading.value=true
  try{
    const {data}:{data:Page}=await props.api(`reports/${props.reportId}/analysis-runs?page=${n}`)
    if(ticket!==seq||scope!==epoch)return
    page.value=data;error.value=''
    if(data.activeRunId)timer=setTimeout(()=>void read(),2500)
  }catch(e){if(ticket===seq&&scope===epoch)error.value=problem(e)}
  finally{if(ticket===seq&&scope===epoch)reading.value=false}
}
async function request(){
  if(!page.value?.canAnalyze||page.value.activeRunId||sending.value||props.revision!==page.value.reportRevision)return
  const scope=epoch, body=JSON.stringify({modelMode:'process_manager',forceNewRun:retry.value})
  const signature=JSON.stringify([props.reportId,props.revision,body])
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  sending.value=true;error.value=''
  try{
    await props.api(`reports/${props.reportId}/analyze`,{method:'POST',body,
      headers:{'If-Match':String(props.revision),'Idempotency-Key':attempt.key}})
    if(scope!==epoch)return
    attempt=undefined;await read(1)
  }catch(e){if(scope===epoch)error.value=problem(e)}
  finally{if(scope===epoch)sending.value=false}
}
watch(()=>[props.reportId,props.revision],()=>{++epoch;++seq;attempt=undefined;page.value=undefined;sending.value=false;void read(1)},{immediate:true})
onBeforeUnmount(()=>{++epoch;cancelTimer()})
</script>

<template>
  <section class="analysis" aria-label="报告分析">
    <header class="analysis-toolbar">
      <strong>报告分析</strong>
      <div class="analysis-actions">
        <el-button v-if="page?.canAnalyze" size="small" :disabled="Boolean(page.activeRunId)||page.reportRevision!==revision||reading"
          :loading="sending" @click="request"><ScanText :size="15"/>{{page.modelEnabled?(retry?'重新分析':'开始分析'):'转人工处理'}}</el-button>
        <el-button size="small" :disabled="reading||sending" aria-label="刷新报告分析" title="刷新报告分析" @click="read()"><RefreshCw :size="15"/></el-button>
      </div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <p v-if="page && !page.total" class="analysis-muted">{{page.modelEnabled?'暂无分析记录':'模型已关闭，暂无分析记录'}}</p>
    <article v-for="item in page?.items" :key="item.runId" class="analysis-run" :aria-label="`报告分析 r${item.reportRevision}`">
      <div class="analysis-toolbar">
        <span class="analysis-muted">报告 r{{item.reportRevision}} · {{showTime(item.requestedAt)}}</span>
        <el-tag size="small" :type="item.status==='manual'||item.status==='stale'?'warning':'info'">{{status[item.status]}}</el-tag>
      </div>
      <p v-if="item.errorCode" class="analysis-muted">{{reasons[item.errorCode]??item.errorCode}}{{item.fallbackTaskId?' · 已关联人工任务':''}}</p>
      <p v-if="item.status==='ready'&&!item.candidates.length" class="analysis-muted">无可定位的建议</p>
      <div v-for="(suggestion,index) in item.candidates" :key="index" class="analysis-proposal">
        <div class="analysis-toolbar">
          <strong>{{suggestion.subject}} / {{suggestion.predicate}} · {{suggestion.typedValue}}</strong>
          <el-button v-if="page?.canAnalyze&&item.reportRevision===revision" size="small" :disabled="!canSelect"
            @click="emit('choose',suggestion)"><ClipboardCheck :size="15"/>选用为候选</el-button>
        </div>
        <blockquote>{{suggestion.quote}}</blockquote>
        <code>{{suggestion.sourceRef}} · {{suggestion.quotePath}}:{{suggestion.quoteOffset}}</code>
      </div>
    </article>
    <el-pagination v-if="page&&page.total>page.pageSize" layout="prev,pager,next" :current-page="page.page"
      :page-size="page.pageSize" :total="page.total" :disabled="reading||sending" @current-change="read"/>
  </section>
</template>

<style scoped>
.analysis{display:grid;gap:8px;min-width:0;padding:12px;border:1px solid var(--pmo-line);border-radius:8px;background:var(--pmo-bg)}
.analysis-toolbar,.analysis-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.analysis-toolbar{justify-content:space-between}
.analysis-actions :deep(.el-button){margin-left:0}.analysis svg{margin-right:4px}.analysis-run{border-top:1px solid var(--el-border-color-lighter);padding:6px 0}
.analysis-muted{color:var(--el-text-color-secondary);margin:0}.analysis-proposal{padding:8px 0;overflow-wrap:anywhere}
.analysis-proposal blockquote{margin:4px 0;padding-left:12px;border-left:2px solid var(--el-border-color);white-space:pre-wrap}
</style>
