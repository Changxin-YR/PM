<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {CalendarDays,Check,ChevronDown,ChevronUp,GitMerge,History,Plus,RefreshCw,X} from 'lucide-vue-next'
import ScopedEvidencePicker from './ScopedEvidencePicker.vue'
import ReportAnalysis from './ReportAnalysis.vue'
import WeeklyFactReview from './WeeklyFactReview.vue'

type Status='candidate'|'verified'|'disputed'|'rejected'
type Trust='low'|'medium'|'high'
type Fact={factId:string;revision:number;subject:string;predicate:string;typedValue:string|number|boolean;asOf:string;
  quote:string;sourceRef:string;quotePath:string;quoteOffset:number;validationStatus:Status;trust:Trust;
  verificationEvidenceRef?:string;verificationNote?:string}
type Page={items:Fact[];total:number;page:number;pageSize:number;reportRevision:number;canReadAnalysis:boolean;canPropose:boolean;canVerify:boolean;canClarify:boolean}
type Entry={revision:number;action:string;actorId:string;occurredAt:string;validationStatus:Status;trust:Trust;reason:string;evidenceRef?:string}
type Log={items:Entry[];total:number;page:number;pageSize:number}
type Mode='propose'|'verify'|'clarify'
type ComparisonStatus='same'|'changed'|'current_unverified'|'no_previous_fact'|'scan_limited'|'outside_reporting_week'
type WeeklyComparison={status:ComparisonStatus;previousStart:string;previousEnd:string;
  previous?:{asOf:string;typedValue:string|number|boolean;quote:string;factRef:string;reportRef:string;evidenceRef?:string}}
type EffortCategory='development'|'implementation'|'management'|'unclassified'
type EffortLine={category:EffortCategory;personDays:string;basis:string}
const effortLabel:Record<EffortCategory,string>={development:'研发',implementation:'实施',management:'管理',unclassified:'未分类'}

const props=defineProps<{reportId:string;projectId:string;revision:number;submittedAt:string;initiallyExpanded?:boolean;currentWeekly?:boolean;
  periodEnd?:string;effortBreakdown?:EffortLine[];api:(path:string,options?:RequestInit)=>Promise<any>}>()
const page=ref<Page>(), expanded=ref(props.initiallyExpanded??false), reading=ref(false), working=ref(false)
const error=ref(''), notice=ref(''), mode=ref<Mode>(), selected=ref<Fact>(), trust=ref<Trust>('medium')
const subject=ref(''), predicate=ref(''), value=ref(''), kind=ref('text'), quote=ref(''), at=ref(''), reason=ref('')
const selectedEffort=ref<EffortLine>(), classified=computed(()=>props.effortBreakdown?.filter(row=>row.category!=='unclassified')??[])
const decimalValue=/^-?[0-9]{1,35}(?:\.[0-9]{1,18})?$/
const unitValue=/^(-?[0-9]{1,35}(?:\.[0-9]{1,18})?)\s*(人天|工作日|天|小时|元|条|项|%|person-days?|days?|hours?)$/i
const quotedAmount=computed(()=>{
  if(kind.value!=='text')return undefined
  const match=unitValue.exec(value.value.trim())
  if(!match)return undefined
  const [,amount,unit]=match
  const mentions=quote.value.matchAll(/(?<![0-9.\-])(-?[0-9]+(?:\.[0-9]+)?)\s*(人天|工作日|天|小时|元|条|项|%|person-days?|days?|hours?)(?![a-z0-9])/gi)
  return [...mentions].some(([,number,shownUnit])=>number===amount&&shownUnit.toLowerCase()===unit.toLowerCase())?amount:undefined
})
const evidence=ref<string[]>([]), historyFor=ref(''), log=ref<Log>(), logLoading=ref(false)
const compareFor=ref(''),comparison=ref<WeeklyComparison>(),compareLoading=ref(false),compareError=ref('')
const comparisonRefresh=ref(0)
let pickedAt:{local:string;iso:string}|undefined
let compareSeq=0
const comparisonLabels:Record<ComparisonStatus,string>={
  same:'与上周同类事实一致',changed:'与上周同类事实相比有变化',current_unverified:'当前事实的原始来源未通过复核',
  no_previous_fact:'上周没有可读且可核验的同类事实',scan_limited:'上周记录超过查询上限，本次未确定可对照的同类事实',
  outside_reporting_week:'事实的业务时点不在这份周报的报告周期内。',
}
const statusLabels:Record<Status,string>={candidate:'候选',verified:'已核验',disputed:'待澄清',rejected:'已驳回'}
const trustLabels:Record<Trust,string>={low:'低',medium:'中',high:'高'}
const actionLabels:Record<string,string>={proposed:'提报',verified:'核验',disputed:'发现冲突',resolved:'完成澄清',superseded:'驳回'}
const reasonLabels:Record<string,string>={SOURCE_LOCATED:'已定位报告原话',SOURCE_VERIFIED:'已复核来源',FACT_CONFLICT:'存在同一时点不同值',FACT_LOW_TRUST:'低可信，待补证'}
const failures:Record<string,string>={FACT_SOURCE_MISMATCH:'出处或原话与已提交报告不符。',FACT_VALUE_NOT_IN_QUOTE:'数值未出现在所引原话中。',
  FACT_EFFORT_SOURCE_MISMATCH:'分类、数值或业务时点与当前周报申报不符。',FACT_SOURCE_STALE:'该事实的报告来源已被新版替代，请打开当前周报。',
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
  clearComparison();reading.value=true
  try{
    const {data}:{data:Page}=await props.api(`reports/${props.reportId}/facts?page=${n}`)
    if(id===seq&&scope===epoch){page.value=data;++comparisonRefresh.value}
  }catch(e){if(id===seq&&scope===epoch)error.value=message(e)}
  finally{if(id===seq&&scope===epoch)reading.value=false}
}
function toggle(){expanded.value=!expanded.value;if(expanded.value)void read(1)}
function start(next:Mode,item?:Fact){
  mode.value=next;selected.value=item;trust.value='medium';subject.value='';predicate.value='';value.value=''
  kind.value='text';quote.value='';at.value=initialTime();reason.value='';evidence.value=[];error.value='';notice.value='';attempt=null;pickedAt=undefined
  selectedEffort.value=undefined
}
function choose(item:Pick<Fact,'subject'|'predicate'|'typedValue'|'quote'|'asOf'>){
  start('propose')
  subject.value=item.subject;predicate.value=item.predicate;value.value=String(item.typedValue);quote.value=item.quote
  at.value=new Date(new Date(item.asOf).getTime()+8*3600000).toISOString().slice(0,16)
  pickedAt={local:at.value,iso:item.asOf}
}
function chooseEffort(line:EffortLine){
  if(!props.periodEnd)return
  const asOf=new Date(Math.min(Date.parse(props.submittedAt),Date.parse(`${props.periodEnd}T00:00:00+08:00`)-60000)).toISOString()
  choose({subject:'周报投入',predicate:`本期${effortLabel[line.category]}人天`,typedValue:line.personDays,quote:line.personDays,asOf})
  selectedEffort.value=line
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
function clearComparison(){
  ++compareSeq;compareFor.value='';comparison.value=undefined;compareError.value='';compareLoading.value=false
}
async function toggleComparison(factId:string){
  const wasOpen=compareFor.value===factId
  clearComparison()
  if(wasOpen)return
  const id=compareSeq,scope=epoch
  compareFor.value=factId;compareLoading.value=true
  try{
    const {data}:{data:WeeklyComparison}=await props.api(`facts/${factId}/weekly-comparison`)
    if(id===compareSeq&&scope===epoch)comparison.value=data
  }catch(e){if(id===compareSeq&&scope===epoch)compareError.value=message(e)}
  finally{if(id===compareSeq&&scope===epoch)compareLoading.value=false}
}
function cancel(){mode.value=undefined;attempt=null}
async function submit(){
  if(!page.value||working.value||!mode.value)return
  const target=selected.value,operation=mode.value,scope=epoch
  let path=`reports/${props.reportId}/facts/proposals`,rev=page.value.reportRevision,payload:object
  if(operation==='propose'){
    const trimmedValue=value.value.trim()
    const typedValue=kind.value==='bool'?value.value==='true':decimalValue.test(trimmedValue)?trimmedValue:value.value
    payload={subject:subject.value,predicate:predicate.value,typedValue,effortCategory:selectedEffort.value?.category,
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
  error.value='';notice.value='';cancel();clearComparison();if(expanded.value)void read(1)
},{immediate:true})
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
      <ReportAnalysis v-if="page?.canReadAnalysis" :report-id="reportId" :revision="page.reportRevision" :api="api"
        :can-select="page.canPropose&&!working&&!reading&&!mode" @choose="choose"/>
      <div v-if="page?.canPropose&&currentWeekly&&classified.length" class="fact-actions" aria-label="周报人天原文">
        <el-button v-for="line in classified" :key="line.category" size="small" :disabled="working||reading||!!mode"
          @click="chooseEffort(line)">引用{{effortLabel[line.category]}} {{line.personDays}} 人天</el-button>
      </div>
      <WeeklyFactReview v-if="currentWeekly" :report-id="reportId" :refresh-version="comparisonRefresh" :api="api" @requested="read()"/>
      <form v-if="mode" class="fact-form" :aria-label="mode==='propose'?'Fact 候选':mode==='verify'?'Fact 核验':'Fact 澄清'" @submit.prevent="submit">
        <div v-if="mode==='propose'" class="fact-inputs">
          <label>主题<input v-model="subject" required maxlength="256" :disabled="working"></label>
          <label>属性<input v-model="predicate" required maxlength="120" :disabled="working" :readonly="!!selectedEffort"></label>
          <label>业务时点（上海）<input v-model="at" required type="datetime-local" :disabled="working"></label>
          <label>值类型<select v-model="kind" :disabled="working||!!selectedEffort" @change="value=''">
            <option value="text">文本或十进制值</option><option value="bool">是/否</option>
          </select></label>
          <label>值<select v-if="kind==='bool'" v-model="value" required :disabled="working"><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>
            <input v-else v-model="value" required maxlength="3000" :disabled="working" :readonly="!!selectedEffort"></label>
          <div v-if="quotedAmount!==undefined" class="fact-wide">
            <el-button native-type="button" size="small" :disabled="working" @click="value=quotedAmount">采用数值 {{quotedAmount}}</el-button>
            <span class="fact-muted"> 数值用于核验，单位和上下文保留在原话中。</span>
          </div>
          <label class="fact-wide">原话<textarea v-model="quote" required rows="2" maxlength="3000" :disabled="working" :readonly="!!selectedEffort"/></label>
          <p v-if="selectedEffort" class="fact-wide fact-muted">来源为当前周报的本期人天分类。投入依据：{{selectedEffort.basis}}</p>
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
          <el-button v-if="item.validationStatus==='verified'&&item.trust!=='low'" size="small"
            :aria-expanded="compareFor===item.factId" @click="toggleComparison(item.factId)"><CalendarDays :size="15"/>对照上周</el-button>
        </div>
        <section v-if="compareFor===item.factId" class="fact-comparison" aria-label="上周事实对照" :aria-busy="compareLoading">
          <p v-if="compareLoading" class="fact-muted" role="status">正在核对上周事实…</p>
          <el-alert v-if="compareError" :title="compareError" type="error" :closable="false"/>
          <template v-if="comparison">
            <strong>{{comparisonLabels[comparison.status]}}</strong>
            <p class="fact-muted">{{comparison.previousStart}} – {{comparison.previousEnd}} · 北京时间</p>
            <dl v-if="comparison.previous">
              <dt>本周</dt><dd>{{item.typedValue}}</dd>
              <dt>上周</dt><dd>{{comparison.previous.typedValue}} · {{shanghai(comparison.previous.asOf)}}</dd>
              <dt>原话</dt><dd>{{comparison.previous.quote}}</dd>
              <dt>来源</dt><dd><code>{{comparison.previous.factRef}}</code> · <code>{{comparison.previous.reportRef}}</code></dd>
              <template v-if="comparison.previous.evidenceRef"><dt>核验原件</dt><dd><code>{{comparison.previous.evidenceRef}}</code></dd></template>
            </dl>
          </template>
        </section>
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
.facts{border:1px solid var(--pmo-line);border-radius:8px;padding:16px;background:var(--pmo-surface)}
.fact-bar,.fact-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.fact-bar{justify-content:space-between}
.fact-actions :deep(.el-button){margin-left:0}.facts svg{margin-right:4px}.fact-content,.fact-row,.fact-form{display:grid;gap:8px;min-width:0}
.fact-row,.fact-form{border:1px solid var(--pmo-line);border-radius:8px;padding:12px;background:var(--pmo-bg);overflow-wrap:anywhere}
.fact-form label{display:grid;gap:4px;min-width:0}.fact-inputs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:8px}
.fact-wide{grid-column:1/-1}.fact-row blockquote{margin:0;border-left:2px solid var(--el-border-color);padding-left:8px;white-space:pre-wrap}
.fact-row code{overflow-wrap:anywhere}.fact-muted{color:var(--el-text-color-secondary);margin:0}.fact-notice{color:var(--el-color-success-dark-2);margin:0}
.fact-history ol{padding-left:18px;margin:0}.fact-history li{padding:3px 0}
.fact-comparison{padding:12px;border:1px solid var(--pmo-line);border-radius:8px;background:var(--pmo-surface)}
.fact-comparison dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px 12px;margin:8px 0 0}
.fact-comparison dd{margin:0;white-space:pre-wrap}
</style>
