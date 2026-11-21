<script setup lang="ts">
import {computed,onBeforeUnmount,shallowRef,ref,watch} from 'vue'
import {ArrowRight,ClipboardList,RefreshCw,Search} from 'lucide-vue-next'
import AssistantLines from './AssistantLines.vue'

type Mode='charter'|'process'|'closing'|'ledger'
type Topic='health'|'cost'|'requirements'|'fact'|'lesson'
type Cited={text:string;sourceRefs:string[];observedAt?:string|null}
type Preview={status:'ready'|'manual'|'empty';items:(Cited & {prompt?:string})[];errorCode?:string}
type Gap={code:string;message:string;sourceRefs:string[]}
type LessonRef=Cited & {lessonId:string;projectId:string;category:string}
type Charter={completeness:{eligible:boolean;missing:Gap[]};scoreSummary:null|{total:string|null;means:Record<string,string>;sourceRefs:string[];decidedAt:string};
  weaknesses:Cited[];lessonRefs:LessonRef[];reviewDraft:Cited[];previewEnabled:boolean}
type Process={reportId:string|null;modelEnabled:boolean;analysis:null|{status:string;errorCode:string|null};
  factCandidates:{subject:string;quote:string;sourceRef:string;asOf:string}[];conflicts:Cited[];
  health:{health:string;dataTrust:string;gaps:string[];timeLine:string|null;costLine:string|null;qualityLine:string|null};alerts:(Cited & {ruleId:string})[];
  weeklyDraft:{period:string;verifiedPersonDays:string|null;structuredFields:{milestone_progress:{summary:string}[];
    requirement_progress:{summary:string}[];risks_and_blockers:{summary:string}[]}}}
type Closing={gateEligible:boolean|null;gateGaps:Gap[];retrospectiveKind:'normal'|'terminated'|null;
  retrospectiveQuestions:string[];lessonDrafts:Cited[];previewEnabled:boolean}
type Ledger={answer:Cited[];asOf:string;sourceRefs:string[];unknowns:string[];previewEnabled:boolean}
type Data={charter:Charter;process:Process;closing:Closing;ledger:Ledger}
type Packet={ [K in Mode]:{mode:K;data:Data[K];meta:{asOf:string;sourceRefs:string[];ruleVersion:string}} }[Mode]
type Destination='reviews'|'reports'|'retrospective'|'health'

const props=defineProps<{projectId:string;revision:number;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{navigate:[destination:Destination]}>()
const modes:{label:string;value:Mode}[]=[{value:'charter',label:'立项评审'},{value:'process',label:'过程管家'},
  {value:'closing',label:'结项回顾'},{value:'ledger',label:'台账问答'}]
const topics:{value:Topic;label:string}[]=[{value:'health',label:'当前三线'},{value:'cost',label:'成本台账'},
  {value:'requirements',label:'需求满足率'},{value:'fact',label:'核验事实'},{value:'lesson',label:'已确认教训'}]
const healthNames:Record<string,string>={green:'绿',yellow:'黄',red:'红',untrusted:'不可信',unavailable:'不可用'}
const analysisNames:Record<string,string>={queued:'正在排队',running:'分析中',ready:'候选已生成',manual:'待人工处理',stale:'来源已变更'}
const previewReasons:Record<string,string>={MODEL_DISABLED:'模型已关闭',MODEL_UNAVAILABLE:'模型网关不可用',MODEL_TIMEOUT:'模型响应超时',
  MODEL_SCHEMA_INVALID:'模型输出格式不合规',MODEL_SOURCE_INVALID:'模型输出无法追溯到本次来源',MODEL_INTERRUPTED:'预览请求已中断'}
const scores:Record<string,string>={strategicAlignment:'战略对齐',customerAttractiveness:'客户／市场吸引力',
  financialResourceReturn:'财务／资源回报',riskControllability:'风险可控度'}
const mode=ref<Mode>('charter'),topic=ref<Topic>('health'),keyword=ref(''),category=ref('')
const packet=shallowRef<Packet|null>(null),loading=ref(false),error=ref('')
const suggestion=shallowRef<Preview|null>(null),suggesting=ref(false),suggestionError=ref(''),loadedQuery=ref('')
const charter=computed(()=>packet.value?.mode==='charter'?packet.value.data:null)
const process=computed(()=>packet.value?.mode==='process'?packet.value.data:null)
const closing=computed(()=>packet.value?.mode==='closing'?packet.value.data:null)
const ledger=computed(()=>packet.value?.mode==='ledger'?packet.value.data:null)
const useKeyword=computed(()=>mode.value==='charter'||(mode.value==='ledger'&&['fact','lesson'].includes(topic.value)))
const useCategory=computed(()=>mode.value==='charter'||(mode.value==='ledger'&&topic.value==='lesson'))
const enabled=computed(()=>charter.value?.previewEnabled||closing.value?.previewEnabled||ledger.value?.previewEnabled)
const query=computed(()=>{
  const params=new URLSearchParams()
  if(mode.value==='ledger')params.set('topic',topic.value)
  if(useKeyword.value&&keyword.value.trim())params.set('keyword',keyword.value.trim())
  if(useCategory.value&&category.value.trim())params.set('category',category.value.trim())
  return params.toString()
})
let request:AbortController|undefined,previewRequest:AbortController|undefined
let attempt:{body:string;key:string}|undefined

function clearSuggestion(){
  previewRequest?.abort();previewRequest=undefined;attempt=undefined
  suggesting.value=false;suggestion.value=null;suggestionError.value=''
}

async function load(){
  request?.abort();clearSuggestion()
  const next=new AbortController(),current=mode.value
  request=next;loading.value=true;error.value='';packet.value=null
  const params=query.value
  try{
    const response=await props.api(`projects/${props.projectId}/assistant/${current}${params?`?${params}`:''}`,{signal:next.signal})
    if(request===next){packet.value={mode:current,...response};loadedQuery.value=params}
  }catch(e){
    if(request===next&&!next.signal.aborted)error.value=e instanceof Error?e.message:'暂时无法读取助理结果。'
  }finally{
    if(request===next)loading.value=false
  }
}

async function suggest(){
  if(!enabled.value||loading.value||suggesting.value||loadedQuery.value!==query.value)return
  const filters:Record<string,string>={}
  new URLSearchParams(loadedQuery.value).forEach((value,key)=>{filters[key]=value})
  const body=JSON.stringify({mode:mode.value,...filters})
  if(attempt?.body!==body)attempt={body,key:crypto.randomUUID()}
  const next=new AbortController()
  previewRequest=next;suggesting.value=true;suggestion.value=null;suggestionError.value=''
  try{
    const {data}:{data:Preview}=await props.api(`projects/${props.projectId}/assistant/suggestions`,{
      method:'POST',signal:next.signal,body,headers:{'If-Match':String(props.revision),'Idempotency-Key':attempt.key}})
    if(previewRequest===next){suggestion.value=data;attempt=undefined}
  }catch(e){
    if(previewRequest===next&&!next.signal.aborted)suggestionError.value=e instanceof Error?e.message:'暂时无法生成建议。'
  }finally{if(previewRequest===next)suggesting.value=false}
}
function changeContext(){keyword.value='';category.value='';void load()}
watch([mode,topic,()=>props.projectId],changeContext,{immediate:true})
watch([keyword,category],clearSuggestion)
watch(()=>props.revision,()=>void load())
onBeforeUnmount(()=>{request?.abort();previewRequest?.abort()})
function time(value:string){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
</script>

<template>
  <section class="assistant" aria-label="项目助理" :aria-busy="loading">
    <el-segmented v-model="mode" :options="modes" block aria-label="助理模式"/>
    <form class="filters" @submit.prevent="load">
      <label v-if="mode==='ledger'">查询主题<select v-model="topic">
        <option v-for="item in topics" :key="item.value" :value="item.value">{{item.label}}</option>
      </select></label>
      <label v-if="useKeyword">{{topic==='fact'&&mode==='ledger'?'事实关键词':'教训关键词'}}
        <input v-model="keyword" maxlength="160" autocomplete="off"></label>
      <label v-if="useCategory">教训分类<input v-model="category" maxlength="80" autocomplete="off"></label>
      <el-button v-if="useKeyword" native-type="submit" :loading="loading"><Search :size="15"/>查询</el-button>
      <el-tooltip v-else content="重新读取"><el-button aria-label="重新读取助理结果" native-type="submit" :loading="loading" :icon="RefreshCw"/></el-tooltip>
    </form>
    <p v-if="loading" class="meta" role="status">正在读取…</p>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>

    <template v-if="charter">
      <div class="assistant-row"><h3>立项材料与评分</h3><el-button text @click="emit('navigate','reviews')">关口工作台<ArrowRight :size="14"/></el-button></div>
      <p v-if="charter.completeness.eligible" class="meta">材料预检通过 · 未代替人工裁决</p>
      <dl v-if="charter.scoreSummary" class="summary-grid">
        <div><dt>正式评分总分</dt><dd>{{charter.scoreSummary.total??'不可用'}}</dd></div>
        <div v-for="(mean,key) in charter.scoreSummary.means" :key="key"><dt>{{scores[key]??key}}</dt><dd>{{mean}}</dd></div>
      </dl>
      <p v-else class="meta">暂无正式评分摘要。</p>
      <p v-if="charter.scoreSummary" class="meta">最近一次正式裁决 · {{time(charter.scoreSummary.decidedAt)}}</p>
      <AssistantLines v-if="charter.weaknesses.length" :items="charter.weaknesses"/>
      <h3>评审建议草稿</h3>
      <AssistantLines :items="charter.reviewDraft"/>
      <h3>历史教训</h3>
      <AssistantLines v-if="charter.lessonRefs.length" :items="charter.lessonRefs"/>
      <p v-else class="meta">没有匹配的已确认教训。</p>
    </template>

    <template v-if="process">
      <div class="assistant-row"><h3>过程核对</h3><el-button text @click="emit('navigate','reports')">日报周报<ArrowRight :size="14"/></el-button></div>
      <dl class="summary-grid">
        <div><dt>三线总体</dt><dd><span class="health-swatch" :data-state="process.health.health" aria-hidden="true"/>{{healthNames[process.health.health]??process.health.health}}</dd></div>
        <div><dt>规则提醒</dt><dd>{{process.alerts.length}}</dd></div>
        <div><dt>待澄清</dt><dd>{{process.conflicts.length}}</dd></div>
        <div><dt>报告分析</dt><dd>{{process.analysis?(analysisNames[process.analysis.status]??process.analysis.status):process.modelEnabled?'尚无分析':'模型关闭'}}</dd></div>
      </dl>
      <dl v-if="process.health.dataTrust==='trusted'" class="summary-grid">
        <div v-for="[label,line] in [['时间',process.health.timeLine],['成本',process.health.costLine],['质量',process.health.qualityLine]]" :key="label??''">
          <dt>{{label}}</dt><dd><span class="health-swatch" :data-state="line" aria-hidden="true"/>{{healthNames[line??'unavailable']??line}}</dd>
        </div>
      </dl>
      <AssistantLines :items="process.alerts.map(item=>({...item,text:`${item.ruleId} · ${item.text}`}))"/>
      <AssistantLines :items="process.conflicts"/>
      <template v-if="process.factCandidates.length">
        <h3>待核验候选</h3>
        <AssistantLines :items="process.factCandidates.map(item=>({text:`${item.subject}：${item.quote}`,observedAt:item.asOf,sourceRefs:[item.sourceRef]}))"/>
      </template>
      <h3>周报预览 · {{process.weeklyDraft.period}}</h3>
      <dl class="sections">
        <div><dt>里程碑</dt><dd v-for="(row,index) in process.weeklyDraft.structuredFields.milestone_progress" :key="index">{{row.summary}}</dd>
          <dd v-if="!process.weeklyDraft.structuredFields.milestone_progress.length">暂无记录</dd></div>
        <div><dt>已核验人天</dt><dd>{{process.weeklyDraft.verifiedPersonDays??'暂无已核验流水'}}</dd></div>
        <div><dt>需求进展</dt><dd v-for="(row,index) in process.weeklyDraft.structuredFields.requirement_progress" :key="index">{{row.summary}}</dd>
          <dd v-if="!process.weeklyDraft.structuredFields.requirement_progress.length">暂无记录</dd></div>
        <div><dt>风险阻塞</dt><dd v-for="(row,index) in process.weeklyDraft.structuredFields.risks_and_blockers" :key="index">{{row.summary}}</dd>
          <dd v-if="!process.weeklyDraft.structuredFields.risks_and_blockers.length">暂无记录</dd></div>
      </dl>
    </template>

    <template v-if="closing">
      <div class="assistant-row"><h3>结项门槛</h3><el-button text @click="emit('navigate','reviews')">关口工作台<ArrowRight :size="14"/></el-button></div>
      <p v-if="closing.gateEligible===null" class="meta">当前未处于结项检查阶段。</p>
      <p v-else-if="closing.gateEligible" class="meta">未发现结项缺口 · 待人工裁决</p>
      <AssistantLines :items="closing.gateGaps.map(gap=>({text:gap.message,sourceRefs:gap.sourceRefs}))"/>
      <div class="assistant-row"><h3>{{closing.retrospectiveKind==='terminated'?'终止复盘':'复盘四问'}}</h3>
        <el-button text @click="emit('navigate','retrospective')">复盘与教训<ArrowRight :size="14"/></el-button></div>
      <ol><li v-for="question in closing.retrospectiveQuestions" :key="question">{{question}}</li></ol>
      <h3>待确认教训摘录</h3>
      <AssistantLines v-if="closing.lessonDrafts.length" :items="closing.lessonDrafts"/>
      <p v-else class="meta">暂无可引用的复盘回答。</p>
    </template>

    <template v-if="ledger">
      <div class="assistant-row"><h3>台账结果</h3><el-button v-if="topic==='health'" text @click="emit('navigate','health')">三线明细<ArrowRight :size="14"/></el-button></div>
      <AssistantLines :items="ledger.answer"/>
      <p v-for="unknown in ledger.unknowns" :key="unknown" class="meta">{{unknown}}</p>
    </template>

    <section v-if="enabled" class="suggestions" aria-label="模型建议预览" :aria-busy="suggesting">
      <div class="assistant-row">
        <h3>{{ledger?'模型整理 · 来源摘录':'模型建议 · 待人工核对'}}</h3>
        <el-button size="small" :loading="suggesting" :disabled="loading||loadedQuery!==query" @click="suggest">
          <ClipboardList :size="15"/>生成预览
        </el-button>
      </div>
      <el-alert v-if="suggestionError" type="warning" :title="suggestionError" :closable="false"/>
      <template v-if="suggestion">
        <p v-if="suggestion.status==='manual'" role="status" class="meta">
          {{previewReasons[suggestion.errorCode??'']??'模型建议不可用'}}，仍可使用以上台账和人工流程。
        </p>
        <p v-else-if="suggestion.status==='empty'" role="status" class="meta">没有可引用的建议。</p>
        <AssistantLines v-else :items="suggestion.items"/>
      </template>
    </section>

    <details v-if="packet" class="packet-sources">
      <summary>截至 {{time(packet.meta.asOf)}} · 依据</summary>
      <small>{{packet.meta.ruleVersion}}</small>
      <code v-for="source in packet.meta.sourceRefs" :key="source">{{source}}</code>
    </details>
  </section>
</template>

<style scoped>
.assistant{min-width:0;overflow-wrap:anywhere}
.filters{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap;margin:12px 0}
.filters label{flex:1 1 140px;min-width:0;margin:0}
.filters input,.filters select{width:100%;min-width:0}
.filters :deep(.el-button){min-height:38px}
.assistant-row{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:4px;margin-top:12px}
h3{font-size:14px;margin:12px 0 4px}
.assistant-row h3{margin:0}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,113px),1fr));gap:8px;margin:8px 0}
.summary-grid div,.sections div{border-bottom:1px solid var(--el-border-color-light);padding:6px 0;min-width:0}
dt{color:var(--el-text-color-secondary);font-size:12px}dd{margin:3px 0}
.health-swatch{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--el-color-info)}
.health-swatch[data-state=green]{background:var(--el-color-success)}.health-swatch[data-state=yellow]{background:var(--el-color-warning)}
.health-swatch[data-state=red]{background:var(--el-color-danger)}
.sections{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,205px),1fr));gap:8px 16px}
.packet-sources{margin-top:12px;color:var(--el-text-color-secondary);border-top:1px solid var(--el-border-color-light);padding:8px 0}
.suggestions{border-top:1px solid var(--el-border-color-light);margin-top:10px}
summary{cursor:pointer}code{display:block;font-size:12px;margin:3px 0}
</style>
