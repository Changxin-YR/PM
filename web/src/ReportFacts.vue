<script setup lang="ts">
import {ref,watch} from 'vue'
import {Check,ChevronDown,ChevronUp,GitMerge,History,Plus,RefreshCw,X} from 'lucide-vue-next'
import ScopedEvidencePicker from './ScopedEvidencePicker.vue'
import ReportAnalysis from './ReportAnalysis.vue'

type Status='candidate'|'verified'|'disputed'|'rejected'
type Trust='low'|'medium'|'high'
type Fact={factId:string;revision:number;subject:string;predicate:string;typedValue:string|number|boolean;asOf:string;
  quote:string;sourceRef:string;quotePath:string;quoteOffset:number;validationStatus:Status;trust:Trust;
  verificationEvidenceRef?:string;verificationNote?:string}
type Page={items:Fact[];total:number;page:number;pageSize:number;reportRevision:number;canPropose:boolean;canVerify:boolean;canClarify:boolean}
type Entry={revision:number;action:string;actorId:string;occurredAt:string;validationStatus:Status;trust:Trust;reason:string;evidenceRef?:string}
type Log={items:Entry[];total:number;page:number;pageSize:number}
type Mode='propose'|'verify'|'clarify'

const props=defineProps<{reportId:string;projectId:string;revision:number;submittedAt:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const page=ref<Page>(), expanded=ref(false), reading=ref(false), working=ref(false)
const error=ref(''), notice=ref(''), mode=ref<Mode>(), selected=ref<Fact>(), trust=ref<Trust>('medium')
const subject=ref(''), predicate=ref(''), value=ref(''), kind=ref('text'), quote=ref(''), at=ref(''), reason=ref('')
const evidence=ref<string[]>([]), historyFor=ref(''), log=ref<Log>(), logLoading=ref(false)
let pickedAt:{local:string;iso:string}|undefined
const statusLabels:Record<Status,string>={candidate:'候选',verified:'已核验',disputed:'待澄清',rejected:'已驳回'}
const trustLabels:Record<Trust,string>={low:'低',medium:'中',high:'高'}
const actionLabels:Record<string,string>={proposed:'提报',verified:'核验',disputed:'发现冲突',resolved:'完成澄清',superseded:'驳回'}
const reasonLabels:Record<string,string>={SOURCE_LOCATED:'已定位报告原话',SOURCE_VERIFIED:'已复核来源',FACT_CONFLICT:'存在同一时点不同值',FACT_LOW_TRUST:'低可信，待补证'}
const failures:Record<string,string>={FACT_SOURCE_MISMATCH:'出处或原话与已提交报告不符。',FACT_VALUE_NOT_IN_QUOTE:'数值未出现在所引原话中。',
  FACT_REPORT_NOT_SUBMITTED:'只能从有提交回执的报告提报。',FACT_PM_SPECIFIC_SOURCE_REQUIRED:'中可信需要 PM 正式提交，且原话含所核验数字或日期。',
  FACT_OBJECTIVE_EVIDENCE_REQUIRED:'高可信需要核验本报告已通过附件核验的客观证据。',
  FACT_CLARIFICATION_REQUIRES_VERIFIED_SOURCE:'先核验明确可信的候选，再结束澄清。',VERSION_CONFLICT:'版本已经变化，请刷新后重试。'}
let epoch=0,seq=0,logSeq=0,attempt:{signature:string;key:string}|null=null

function shanghai(at:string){return new Date(at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function initialTime(){
  const date=new Date(new Date(props.submittedAt).getTime()+8*3600000)
  return date.toISOString().slice(0,16)
}
function message(e:unknown){
  const text=e instanceof Error?e.message:'请求失败。'
  return Object.entries(failures).find(([code])=>text.includes(code))?.[1]??text
}
async function read(n=page.value?.page??1){
  const id=++seq,scope=epoch
  reading.value=true
  try{
    const {data}:{data:Page}=await props.api(`reports/${props.reportId}/facts?page=${n}`)
    if(id===seq&&scope===epoch)page.value=data
  }catch(e){if(id===seq&&scope===epoch)error.value=message(e)}
  finally{if(id===seq&&scope===epoch)reading.value=false}
}
function toggle(){expanded.value=!expanded.value;if(expanded.value)void read(1)}
function start(next:Mode,item?:Fact){
  mode.value=next;selected.value=item;trust.value='medium';subject.value='';predicate.value='';value.value=''
  kind.value='text';quote.value='';at.value=initialTime();reason.value='';evidence.value=[];error.value='';notice.value='';attempt=null;pickedAt=undefined
}
function choose(item:Pick<Fact,'subject'|'predicate'|'typedValue'|'quote'|'asOf'>){
  start('propose')
  subject.value=item.subject;predicate.value=item.predicate;value.value=String(item.typedValue);quote.value=item.quote
  at.value=new Date(new Date(item.asOf).getTime()+8*3600000).toISOString().slice(0,16)
  pickedAt={local:at.value,iso:item.asOf}
}
async function showLog(factId:string,n=1){
  const id=++logSeq,scope=epoch
  historyFor.value=factId;logLoading.value=true
  try{
    const response=await props.api(`facts/${factId}/history?page=${n}`)
    if(id===logSeq&&scope===epoch)log.value=response.data
  }catch(e){if(id===logSeq&&scope===epoch)error.value=message(e)}
  finally{if(id===logSeq&&scope===epoch)logLoading.value=false}
}
function toggleLog(factId:string){if(historyFor.value===factId){++logSeq;historyFor.value='';log.value=undefined;logLoading.value=false}else{log.value=undefined;void showLog(factId)}}
function cancel(){mode.value=undefined;attempt=null}
async function submit(){
  if(!page.value||working.value||!mode.value)return
  const target=selected.value,operation=mode.value,scope=epoch
  let path=`reports/${props.reportId}/facts/proposals`,rev=page.value.reportRevision,payload:object
  if(operation==='propose'){
    payload={subject:subject.value,predicate:predicate.value,typedValue:kind.value==='bool'?value.value==='true':value.value,
      asOf:pickedAt?.local===at.value?pickedAt.iso:new Date(`${at.value}+08:00`).toISOString(),quote:quote.value,sourceRef:`report:${props.reportId}@${rev}`}
  }else{
    if(!target)return
    path=`facts/${target.factId}/${operation}`;rev=target.revision
    if(operation==='verify'){
      if(trust.value==='high'&&!evidence.value.length){error.value='请选择一份已核验客观证据。';return}
      payload={trust:trust.value,evidenceRef:trust.value==='high'?evidence.value[0]:target.sourceRef,...(reason.value?{reason:reason.value}:{})}
    }else payload={correctedSourceRef:target.verificationEvidenceRef,reason:reason.value}
  }
  const body=JSON.stringify(payload),signature=JSON.stringify([path,rev,body])
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  working.value=true;error.value='';notice.value=''
  try{
    await props.api(path,{method:'POST',headers:{'Idempotency-Key':attempt.key,'If-Match':String(rev)},body})
    if(scope!==epoch)return
    cancel();notice.value=operation==='propose'?'候选已记录。':operation==='verify'?'核验已记录。':'澄清已完成。'
    await read(operation==='propose'?1:undefined)
    if(historyFor.value)await showLog(historyFor.value)
  }catch(e){if(scope===epoch)error.value=message(e)}
  finally{if(scope===epoch)working.value=false}
}
watch(()=>[props.reportId,props.revision],()=>{
  ++epoch;page.value=undefined;working.value=false;reading.value=false;historyFor.value='';log.value=undefined;logLoading.value=false
  error.value='';notice.value='';cancel();if(expanded.value)void read(1)
})
</script>

<template>
  <section class="facts" aria-label="Fact 证据与澄清">
    <header class="fact-bar">
      <h4><el-button text :aria-expanded="expanded" @click="toggle"><ChevronDown v-if="expanded" :size="16"/><ChevronUp v-else :size="16"/>Fact 证据与澄清</el-button></h4>
      <div v-if="expanded" class="fact-actions">
        <el-button v-if="page?.canPropose" size="small" :disabled="working||reading" @click="start('propose')"><Plus :size="15"/>提报候选</el-button>
        <el-button size="small" title="刷新 Fact" aria-label="刷新 Fact" :disabled="working||reading" @click="error='';read()"><RefreshCw :size="15"/></el-button>
      </div>
    </header>
    <div v-if="expanded" v-loading="reading" class="fact-content">
      <el-alert v-if="error" :title="error" type="error" :closable="false"/>
      <p v-if="notice" role="status" class="fact-notice">{{notice}}</p>
      <ReportAnalysis v-if="page?.canPropose" :report-id="reportId" :revision="page.reportRevision" :api="api"
        :can-select="!working&&!reading&&!mode" @choose="choose"/>
      <form v-if="mode" class="fact-form" :aria-label="mode==='propose'?'Fact 候选':mode==='verify'?'Fact 核验':'Fact 澄清'" @submit.prevent="submit">
        <div v-if="mode==='propose'" class="fact-inputs">
          <label>主题<input v-model="subject" required maxlength="256" :disabled="working"></label>
          <label>属性<input v-model="predicate" required maxlength="120" :disabled="working"></label>
          <label>业务时点（上海）<input v-model="at" required type="datetime-local" :disabled="working"></label>
          <label>值类型<select v-model="kind" :disabled="working" @change="value=''">
            <option value="text">文本或十进制值</option><option value="bool">是/否</option>
          </select></label>
          <label>值<select v-if="kind==='bool'" v-model="value" required :disabled="working"><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>
            <input v-else v-model="value" required maxlength="3000" :disabled="working"></label>
          <label class="fact-wide">原话<textarea v-model="quote" required rows="2" maxlength="3000" :disabled="working"/></label>
        </div>
        <template v-else-if="selected">
          <strong>{{selected.subject}} / {{selected.predicate}} · {{String(selected.typedValue)}}</strong>
          <label v-if="mode==='verify'">可信等级<select v-model="trust" :disabled="working"><option value="medium">中 · PM 正式填报</option>
            <option value="high">高 · 客观凭证</option><option value="low">低 · 待补证</option></select></label>
          <ScopedEvidencePicker v-if="mode==='verify'&&trust==='high'" v-model="evidence" :api="api" :project-id="projectId"
            resource-type="report" :resource-id="reportId" label="报告客观凭证" :disabled="working"/>
          <label>{{trust==='high'&&mode==='verify'?'核对位置与依据':'说明'}}<textarea v-model.trim="reason" :required="mode==='clarify'||trust==='high'"
            minlength="5" maxlength="2000" rows="2" :disabled="working"/></label>
        </template>
        <div class="fact-actions"><el-button type="primary" native-type="submit" :loading="working"><Check :size="15"/>{{mode==='propose'?'保存候选':mode==='verify'?'记录核验':'确认澄清'}}</el-button>
          <el-button :disabled="working" @click="cancel"><X :size="15"/>取消</el-button></div>
      </form>
      <p v-if="page&&!page.total" class="fact-muted">暂无候选事实</p>
      <article v-for="item in page?.items" :key="item.factId" class="fact-row" :aria-label="`${item.subject} ${item.predicate}`">
        <div class="fact-bar"><strong>{{item.subject}} / {{item.predicate}}</strong>
          <el-tag size="small" :type="item.validationStatus==='verified'?'success':item.validationStatus==='disputed'?'warning':'info'">{{statusLabels[item.validationStatus]}} · {{trustLabels[item.trust]}}可信</el-tag></div>
        <div>{{String(item.typedValue)}} <time class="fact-muted" :datetime="item.asOf">· {{shanghai(item.asOf)}}</time></div>
        <blockquote>{{item.quote}}</blockquote>
        <code>{{item.sourceRef}} · {{item.quotePath}}:{{item.quoteOffset}}</code>
        <span v-if="item.verificationNote" class="fact-muted">{{reasonLabels[item.verificationNote]??item.verificationNote}}</span>
        <div class="fact-actions">
          <el-button v-if="page?.canVerify&&item.validationStatus!=='rejected'" size="small" :disabled="working||reading" @click="start('verify',item)"><Check :size="15"/>核验</el-button>
          <el-button v-if="page?.canClarify&&item.validationStatus==='disputed'&&item.trust!=='low'" size="small" :disabled="working||reading" @click="start('clarify',item)"><GitMerge :size="15"/>澄清</el-button>
          <el-button size="small" :aria-expanded="historyFor===item.factId" @click="toggleLog(item.factId)"><History :size="15"/>核验历史</el-button>
        </div>
        <div v-if="historyFor===item.factId" v-loading="logLoading" class="fact-history">
          <ol><li v-for="entry in log?.items" :key="entry.revision">r{{entry.revision}} · {{actionLabels[entry.action]??entry.action}} · {{shanghai(entry.occurredAt)}}
            <div>{{reasonLabels[entry.reason]??entry.reason}}</div><code v-if="entry.evidenceRef">{{entry.evidenceRef}}</code></li></ol>
          <el-pagination v-if="log&&log.total>log.pageSize" :current-page="log.page" :page-size="log.pageSize" :total="log.total"
            layout="prev,pager,next" :disabled="logLoading" @current-change="(n:number)=>showLog(item.factId,n)"/>
        </div>
      </article>
      <el-pagination v-if="page&&page.total>page.pageSize" :current-page="page.page" :page-size="page.pageSize" :total="page.total"
        layout="prev,pager,next" :disabled="reading||working" @current-change="read"/>
    </div>
  </section>
</template>

<style scoped>
.facts{border-top:1px solid var(--el-border-color-light);padding-top:8px}
.fact-bar,.fact-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.fact-bar{justify-content:space-between}
.fact-actions :deep(.el-button){margin-left:0}.facts svg{margin-right:4px}.fact-content,.fact-row,.fact-form{display:grid;gap:8px;min-width:0}
.fact-row,.fact-form{border-top:1px solid var(--el-border-color-lighter);padding:10px 0;overflow-wrap:anywhere}
.fact-form label{display:grid;gap:4px;min-width:0}.fact-inputs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:8px}
.fact-wide{grid-column:1/-1}.fact-row blockquote{margin:0;border-left:2px solid var(--el-border-color);padding-left:8px;white-space:pre-wrap}
.fact-row code{overflow-wrap:anywhere}.fact-muted{color:var(--el-text-color-secondary);margin:0}.fact-notice{color:var(--el-color-success-dark-2);margin:0}
.fact-history ol{padding-left:18px;margin:0}.fact-history li{padding:3px 0}
</style>
