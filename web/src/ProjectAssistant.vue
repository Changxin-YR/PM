<script setup lang="ts">
import {computed,onBeforeUnmount,shallowRef,ref,watch} from 'vue'
import {ArrowRight,Bell,ChartNoAxesColumnIncreasing,CircleHelp,ClipboardList,Database,Download,Flag,Layers3,ListChecks,RefreshCw,Search,TriangleAlert} from 'lucide-vue-next'
import AssistantLines from './AssistantLines.vue'
import AssistantRequirementHistory from './AssistantRequirementHistory.vue'
import PreapprovalSpending from './PreapprovalSpending.vue'
import WeeklyHealthOverview from './WeeklyHealthOverview.vue'
import WeeklyPortfolioReports from './WeeklyPortfolioReports.vue'
import {interpretQuestion,type AssistantQuestion,type AssistantTopic,type QuestionTarget} from './assistantQuestions'

type Mode='charter'|'process'|'closing'|'ledger'
type Topic=AssistantTopic
type Cited={text:string;sourceRefs:string[];observedAt?:string|null}
type Preview={status:'ready'|'manual'|'empty';items:(Cited & {prompt?:string})[];errorCode?:string}
type Gap={code:string;message:string;sourceRefs:string[]}
type LessonRef=Cited & {lessonId:string;projectId:string;category:string;matchedTerm?:string|null}
type Score={total:string|null;means:Record<string,string>;sourceRefs:string[]}
type Charter={completeness:{eligible:boolean;missing:Gap[]};scoreSummary:null|Score & {decidedAt:string};
  pendingScore:null|Score & {observedAt:string};pendingWeaknesses:Cited[];charterFacts:Cited[];
  weaknesses:Cited[];lessonRefs:LessonRef[];reviewDraft:Cited[];previewEnabled:boolean}
type Process={reportId:string|null;modelEnabled:boolean;analysis:null|{status:string;errorCode:string|null};
  factCandidates:{subject:string;quote:string;sourceRef:string;asOf:string}[];conflicts:Cited[];
  health:{health:string;dataTrust:string;gaps:string[];timeLine:string|null;costLine:string|null;qualityLine:string|null};alerts:(Cited & {ruleId:string})[];
  weeklyDraft:{period:string;verifiedPersonDays:string|null;structuredFields:{milestone_progress:{summary:string}[];
    requirement_progress:{summary:string}[];risks_and_blockers:{summary:string}[]}}}
type Closing={gateEligible:boolean|null;gateGaps:Gap[];retrospectiveKind:'normal'|'terminated'|null;
  retrospectiveQuestions:string[];lessonDrafts:Cited[];canStartLessonDraft:boolean;previewEnabled:boolean}
type Ledger={answer:Cited[];asOf:string;sourceRefs:string[];unknowns:string[];previewEnabled:boolean}
type Data={charter:Charter;process:Process;closing:Closing;ledger:Ledger}
type Packet={ [K in Mode]:{mode:K;data:Data[K];meta:{asOf:string;sourceRefs:string[];ruleVersion:string}} }[Mode]
type Destination='reviews'|'reports'|'retrospective'|'health'

const props=defineProps<{projectId:string;projectLabel?:string;projectCode?:string;initialQuestion?:AssistantQuestion;
  revision:number;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{navigate:[destination:Destination];draftLesson:[];openReport:[projectId:string,reportId:string];
  openProjectQuestion:[projectId:string,question:AssistantQuestion]}>()
const modes:{label:string;value:Mode}[]=[{value:'charter',label:'立项评审'},{value:'process',label:'过程管家'},
  {value:'closing',label:'结项回顾'},{value:'ledger',label:'台账问答'}]
const topics:{value:Topic;label:string}[]=[{value:'health',label:'当前三线'},{value:'cost',label:'成本台账'},
  {value:'requirements',label:'需求满足率'},{value:'requirement_history',label:'需求变更历史'},
  {value:'forecast',label:'里程碑延期'},{value:'spending',label:'立项前支出'},{value:'weekly_health',label:'本周黄红灯'},
  {value:'fact',label:'核验事实'},{value:'lesson',label:'已确认教训'}]
const healthNames:Record<string,string>={green:'绿',yellow:'黄',red:'红',untrusted:'不可信',unavailable:'不可用'}
const analysisNames:Record<string,string>={queued:'正在排队',running:'分析中',ready:'候选已生成',manual:'待人工处理',stale:'来源已变更'}
const previewReasons:Record<string,string>={MODEL_DISABLED:'模型已关闭',MODEL_UNAVAILABLE:'模型网关不可用',MODEL_TIMEOUT:'模型响应超时',
  MODEL_SCHEMA_INVALID:'模型输出格式不合规',MODEL_SOURCE_INVALID:'模型输出无法追溯到本次来源',MODEL_INTERRUPTED:'预览请求已中断',
  MODEL_SCOPE_BLOCKED:'当前项目不在模型外发范围内',MODEL_NO_SELECTION:'模型未选出可用条目'}
const scores:Record<string,string>={strategicAlignment:'战略对齐',customerAttractiveness:'客户／市场吸引力',
  financialResourceReturn:'财务／资源回报',riskControllability:'风险可控度'}
const initialTarget=props.initialQuestion?.target
const mode=ref<Mode>(initialTarget?.mode??'charter'),topic=ref<Topic>(initialTarget?.mode==='ledger'?initialTarget.topic:'health')
const keyword=ref(''),category=ref('')
const question=ref(props.initialQuestion?.text??''),questionError=ref(''),asking=ref(false)
const historyKeyword=ref(topic.value==='requirement_history'?initialTarget?.keyword??'':'')
const showPortfolio=ref(false)
const packet=shallowRef<Packet|null>(null),loading=ref(false),error=ref('')
const suggestion=shallowRef<Preview|null>(null),suggesting=ref(false),suggestionError=ref(''),loadedQuery=ref('')
const charter=computed(()=>packet.value?.mode==='charter'?packet.value.data:null)
const process=computed(()=>packet.value?.mode==='process'?packet.value.data:null)
const closing=computed(()=>packet.value?.mode==='closing'?packet.value.data:null)
const ledger=computed(()=>packet.value?.mode==='ledger'?packet.value.data:null)
const historyTopic=computed(()=>mode.value==='ledger'&&topic.value==='requirement_history')
const globalTopic=computed(()=>mode.value==='ledger'&&(topic.value==='spending'||topic.value==='weekly_health'))
const visibleScore=computed(()=>charter.value?.pendingScore??charter.value?.scoreSummary)
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
let request:AbortController|undefined,previewRequest:AbortController|undefined,scopeRequest:AbortController|undefined
let attempt:{body:string;key:string}|undefined
let nextKeyword=initialTarget?.keyword

function clearSuggestion(){
  previewRequest?.abort();previewRequest=undefined;attempt=undefined
  suggesting.value=false;suggestion.value=null;suggestionError.value=''
}

async function load(){
  request?.abort();clearSuggestion()
  if(historyTopic.value||globalTopic.value){request=undefined;loading.value=false;error.value='';packet.value=null;return}
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
function changeContext(){keyword.value=nextKeyword??'';nextKeyword=undefined;category.value='';showPortfolio.value=false;void load()}
watch([mode,topic,()=>props.projectId],changeContext,{immediate:true})
watch(()=>props.projectId,()=>{question.value='';questionError.value='';historyKeyword.value=''})
watch([question,mode,topic,()=>props.projectId],()=>scopeRequest?.abort(),{flush:'sync'})
watch([keyword,category],clearSuggestion)
watch(()=>props.revision,()=>void load())
onBeforeUnmount(()=>{request?.abort();previewRequest?.abort();scopeRequest?.abort()})
function time(value:string){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}

function showAnswer(target:QuestionTarget){
  const nextTopic=target.mode==='ledger'?target.topic:topic.value
  historyKeyword.value=nextTopic==='requirement_history'?(target.keyword??''):''
  nextKeyword=target.keyword
  if(target.mode===mode.value&&nextTopic===topic.value){changeContext();return}
  mode.value=target.mode
  topic.value=nextTopic
}

const identityText=(value?:string)=>value?.trim().normalize('NFKC').toLowerCase()
function matchesProject(qualifier:string,project:{code?:string;name?:string}){
  const reference=identityText(qualifier)
  return !!reference&&(reference===identityText(project.code)||reference===identityText(project.name))
}

function refuseScope(message:string){
  request?.abort();request=undefined
  packet.value=null;loading.value=false;clearSuggestion()
  questionError.value=message
}

async function ask(){
  const text=question.value.trim()
  if(!text||asking.value)return
  const target=interpretQuestion(text)
  if(!target){questionError.value='暂时无法识别这个问题，请选择查询主题。核验事实可在问题中用引号标出关键词。';return}
  questionError.value=''
  const projects=target.projects??[]
  if(projects.length>1){refuseScope('请一次指定一个项目，再查询它的台账。');return}
  const [qualifier]=projects
  if(!qualifier||qualifier.normalize('NFKC')===props.projectCode?.normalize('NFKC')){showAnswer(target);return}
  const controller=new AbortController()
  scopeRequest=controller;asking.value=true
  const params=new URLSearchParams({page:'1',pageSize:'100','filters[keyword]':qualifier})
  const unavailable='无法确定唯一可读的项目。请核对项目编号，或从项目台账进入对应项目。'
  try{
    const {data}:{data:{items:{projectId:string;code:string;name:string;materialsVisible:boolean}[];total:number}}=
      await props.api('project-overview?'+params,{signal:controller.signal})
    if(controller.signal.aborted)return
    const matched=data.items.filter(project=>matchesProject(qualifier,project))
    if(data.total>data.items.length||matched.length!==1||!matched[0].materialsVisible){
      refuseScope(unavailable);return
    }
    if(matched[0].projectId===props.projectId){showAnswer(target);return}
    emit('openProjectQuestion',matched[0].projectId,{text,target})
  }catch(e){
    if(!controller.signal.aborted)refuseScope(unavailable)
  }finally{
    if(scopeRequest===controller){scopeRequest=undefined;asking.value=false}
  }
}

function downloadReview(){
  const data=charter.value,meta=packet.value?.meta
  if(!data||!meta)return
  const score=visibleScore.value
  const lines=(items:Cited[])=>items.length
    ?items.map(item=>`- ${item.text}\n  依据 ${item.sourceRefs.join('、')}`).join('\n'):'暂无可见记录'
  const sections=[`# 立项评审材料\n\n项目 ${props.projectLabel??props.projectId}\n数据时点 ${time(meta.asOf)}（北京时间）\n规则版本 ${meta.ruleVersion}`,
    `## 立项卡\n${lines(data.charterFacts)}`,
    `## 材料预检\n${data.completeness.eligible?'材料齐备，待有权人员处理。':'存在待补材料。'}\n${lines(data.reviewDraft)}`,
    `## ${data.pendingScore?'当前评审评分汇总（待裁决）':'正式评分'}\n${score?[
      `总分 ${score.total??'不可用'}`,...Object.entries(score.means).map(([key,value])=>`${scores[key]??key} ${value}`),
      `依据 ${score.sourceRefs.join('、')}`].join('\n'):'暂无可见评分'}`,
    `## 补强建议\n${lines(data.pendingScore?data.pendingWeaknesses:data.weaknesses)}`,
    `## 历史教训\n${lines(data.lessonRefs)}`]
  const href=URL.createObjectURL(new Blob([sections.join('\n\n')+'\n'],{type:'text/markdown;charset=utf-8'}))
  const link=document.createElement('a')
  link.href=href;link.download=`PMO-立项评审材料-${props.projectId.slice(0,8)}.md`
  link.click()
  window.setTimeout(()=>URL.revokeObjectURL(href),1000)
}
</script>

<template>
  <section class="assistant" aria-label="项目助理" :aria-busy="loading">
    <div class="panel assistant-toolbar">
      <el-segmented v-model="mode" :options="modes" aria-label="助理模式"/>
      <form class="filters" @submit.prevent="load">
      <label v-if="mode==='ledger'">查询主题<select v-model="topic">
        <option v-for="item in topics" :key="item.value" :value="item.value">{{item.label}}</option>
      </select></label>
      <label v-if="useKeyword">{{topic==='fact'&&mode==='ledger'?'事实关键词':'教训关键词'}}
        <input v-model="keyword" maxlength="160" autocomplete="off"></label>
      <label v-if="useCategory">教训分类<input v-model="category" maxlength="80" autocomplete="off"></label>
      <el-button v-if="useKeyword" native-type="submit" :loading="loading"><Search :size="15"/>查询</el-button>
      <el-tooltip v-else-if="!historyTopic&&!globalTopic" content="重新读取"><el-button aria-label="重新读取助理结果" native-type="submit" :loading="loading" :icon="RefreshCw"/></el-tooltip>
      </form>
    </div>
    <form v-if="mode==='ledger'" class="panel question-form" aria-label="台账自然语言查询" @submit.prevent="ask">
      <label>用一句话查询台账<input v-model="question" maxlength="180" placeholder="例如 本周哪些项目亮了黄灯？" @input="questionError=''"/></label>
      <el-button native-type="submit" :disabled="!question.trim()" :loading="asking"><Search :size="15"/>提问</el-button>
      <p v-if="questionError" class="meta" role="status">{{questionError}}</p>
      <p v-else class="meta">{{globalTopic?'查询范围为你有权查看的项目。':`当前项目 ${projectLabel??''}`}}</p>
    </form>
    <p v-if="loading" class="meta" role="status">正在读取…</p>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <AssistantRequirementHistory v-if="historyTopic" :key="`${projectId}:${historyKeyword}`" :project-id="projectId" :initial-query="historyKeyword" :api="api"/>
    <PreapprovalSpending v-if="mode==='ledger'&&topic==='spending'" :api="api"/>
    <section v-if="mode==='ledger'&&topic==='weekly_health'" class="panel assistant-panel"><WeeklyHealthOverview :api="api"/></section>

    <template v-if="charter">
    <section class="panel assistant-panel">
      <div class="assistant-row"><h3>立项材料与评分</h3><div class="assistant-actions">
        <el-button :icon="Download" @click="downloadReview">下载评审材料</el-button>
        <el-button text @click="emit('navigate','reviews')">关口工作台<ArrowRight :size="14"/></el-button>
      </div></div>
      <p v-if="charter.completeness.eligible" class="meta">材料预检通过 · 未代替人工裁决</p>
      <dl v-if="visibleScore" class="summary-grid">
        <div><dt>{{charter.pendingScore?'已录评分总分 · 待裁决':'正式评分总分'}}</dt><dd>{{visibleScore.total??'不可用'}}</dd></div>
        <div v-for="(mean,key) in visibleScore.means" :key="key"><dt>{{scores[key]??key}}</dt><dd>{{mean}}</dd></div>
      </dl>
      <p v-else class="meta">暂无正式评分摘要。</p>
      <p v-if="charter.pendingScore" class="meta">已录评分读取时点 · {{time(charter.pendingScore.observedAt)}}</p>
      <p v-else-if="charter.scoreSummary" class="meta">最近一次正式裁决 · {{time(charter.scoreSummary.decidedAt)}}</p>
      <AssistantLines v-if="visibleScore" :items="charter.pendingScore?charter.pendingWeaknesses:charter.weaknesses"/>
    </section>
    <section class="panel assistant-panel">
      <h3>评审建议草稿</h3>
      <AssistantLines :items="charter.reviewDraft"/>
    </section>
    <section class="panel assistant-panel">
      <h3>历史教训</h3>
      <AssistantLines v-if="charter.lessonRefs.length" :items="charter.lessonRefs.map(item=>({...item,
        prompt:item.matchedTerm?`立项卡文字匹配 · ${item.matchedTerm}`:undefined}))"/>
      <p v-else class="meta">没有匹配的已确认教训。</p>
    </section>
    </template>

    <template v-if="process">
      <section class="panel assistant-panel">
      <div class="assistant-row"><h3>过程核对</h3><el-button text @click="emit('navigate','reports')">日报周报<ArrowRight :size="14"/></el-button></div>
      <dl class="summary-grid process-grid">
        <div><dt><Layers3 :size="19" aria-hidden="true"/>三线总体</dt><dd><span class="health-swatch" :data-state="process.health.health" aria-hidden="true"/>{{healthNames[process.health.health]??process.health.health}}</dd></div>
        <div><dt><Bell :size="19" aria-hidden="true"/>规则提醒</dt><dd>{{process.alerts.length}}</dd></div>
        <div><dt><CircleHelp :size="19" aria-hidden="true"/>待澄清</dt><dd>{{process.conflicts.length}}</dd></div>
        <div><dt><ChartNoAxesColumnIncreasing :size="19" aria-hidden="true"/>报告分析</dt><dd>{{process.analysis?(analysisNames[process.analysis.status]??process.analysis.status):process.modelEnabled?'尚无分析':'模型关闭'}}</dd></div>
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
      </section>
      <section class="panel assistant-panel">
      <div class="assistant-row"><h3>周报预览 · {{process.weeklyDraft.period}}</h3>
        <el-button :aria-expanded="showPortfolio" @click="showPortfolio=!showPortfolio">{{showPortfolio?'收起组合周报':'查看组合周报'}}</el-button></div>
      <div v-if="showPortfolio" class="portfolio-preview">
        <WeeklyPortfolioReports :api="api" @open-report="(projectId,reportId)=>emit('openReport',projectId,reportId)"/>
      </div>
      <dl class="sections">
        <div><dt><Flag :size="17" aria-hidden="true"/>里程碑</dt><dd v-for="(row,index) in process.weeklyDraft.structuredFields.milestone_progress" :key="index">{{row.summary}}</dd>
          <dd v-if="!process.weeklyDraft.structuredFields.milestone_progress.length">暂无记录</dd></div>
        <div><dt><Database :size="17" aria-hidden="true"/>已核验人天</dt><dd>{{process.weeklyDraft.verifiedPersonDays??'暂无已核验流水'}}</dd></div>
        <div><dt><ListChecks :size="17" aria-hidden="true"/>需求进展</dt><dd v-for="(row,index) in process.weeklyDraft.structuredFields.requirement_progress" :key="index">{{row.summary}}</dd>
          <dd v-if="!process.weeklyDraft.structuredFields.requirement_progress.length">暂无记录</dd></div>
        <div><dt><TriangleAlert :size="17" aria-hidden="true"/>风险阻塞</dt><dd v-for="(row,index) in process.weeklyDraft.structuredFields.risks_and_blockers" :key="index">{{row.summary}}</dd>
          <dd v-if="!process.weeklyDraft.structuredFields.risks_and_blockers.length">暂无记录</dd></div>
      </dl>
      </section>
    </template>

    <template v-if="closing">
    <section class="panel assistant-panel">
      <div class="assistant-row"><h3>结项门槛</h3><el-button text @click="emit('navigate','reviews')">关口工作台<ArrowRight :size="14"/></el-button></div>
      <p v-if="closing.gateEligible===null" class="meta">当前未处于结项检查阶段。</p>
      <p v-else-if="closing.gateEligible" class="meta">未发现结项缺口 · 待人工裁决</p>
      <AssistantLines :items="closing.gateGaps.map(gap=>({text:gap.message,sourceRefs:gap.sourceRefs}))"/>
    </section>
    <section class="panel assistant-panel">
      <div class="assistant-row"><h3>{{closing.retrospectiveKind==='terminated'?'终止复盘':'复盘四问'}}</h3>
        <el-button text @click="emit('navigate','retrospective')">复盘与教训<ArrowRight :size="14"/></el-button></div>
      <ol><li v-for="question in closing.retrospectiveQuestions" :key="question">{{question}}</li></ol>
      <h3>待确认教训摘录</h3>
      <AssistantLines v-if="closing.lessonDrafts.length" :items="closing.lessonDrafts"/>
      <p v-else class="meta">暂无可引用的复盘回答。</p>
      <el-button v-if="closing.canStartLessonDraft" @click="emit('draftLesson')">从复盘起草教训<ArrowRight :size="14"/></el-button>
    </section>
    </template>

    <section v-if="ledger" class="panel assistant-panel" aria-label="台账原始结果">
      <div class="assistant-row"><h3>台账结果</h3><el-button v-if="topic==='health'" text @click="emit('navigate','health')">三线明细<ArrowRight :size="14"/></el-button></div>
      <AssistantLines :items="ledger.answer"/>
      <p v-for="unknown in ledger.unknowns" :key="unknown" class="meta">{{unknown}}</p>
    </section>

    <section v-if="enabled" class="panel assistant-panel" aria-label="模型建议预览" :aria-busy="suggesting">
      <div class="assistant-row">
        <h3>{{ledger?'模型整理 · 来源摘录':'模型建议 · 待人工核对'}}</h3>
        <el-button size="small" :loading="suggesting" :disabled="loading||loadedQuery!==query" @click="suggest">
          <ClipboardList :size="15"/>生成预览
        </el-button>
      </div>
      <el-alert v-if="suggestionError" type="warning" :title="suggestionError" :closable="false"/>
      <template v-if="suggestion">
        <template v-if="suggestion.status==='manual'">
          <p role="status" class="meta">{{previewReasons[suggestion.errorCode??'']??'模型建议不可用'}}。{{suggestion.items.length?'以下为台账原文。':'仍可使用以上台账和人工流程。'}}</p>
          <AssistantLines v-if="suggestion.items.length" :items="suggestion.items"/>
        </template>
        <p v-else-if="suggestion.status==='empty'" role="status" class="meta">没有可引用的建议。</p>
        <AssistantLines v-else :items="suggestion.items"/>
      </template>
    </section>

    <details v-if="packet" class="panel packet-sources">
      <summary>截至 {{time(packet.meta.asOf)}} · 依据</summary>
      <small>{{packet.meta.ruleVersion}}</small>
      <code v-for="source in packet.meta.sourceRefs" :key="source">{{source}}</code>
    </details>
  </section>
</template>

<style scoped>
.assistant {display:grid;gap:12px;min-width:0;overflow-wrap:anywhere}
.assistant > .panel + .panel {margin-top:0}
.filters,.question-form,.assistant-actions {display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}
.assistant-toolbar{display:grid;gap:12px;justify-items:start;padding-block-start:0}
.assistant-toolbar .filters{width:100%}
.question-form label {flex:1 1 240px;min-width:0;margin:0}
.question-form input {width:100%;min-width:0}
.question-form p {flex:1 1 100%;margin:0}
.portfolio-preview {margin:16px 0;padding-bottom:16px;border-bottom:1px solid var(--pmo-line)}
.assistant-toolbar :deep(.el-segmented) {width:100%;max-width:100%;padding:0;border-radius:0;background:var(--pmo-surface);border-bottom:1px solid var(--pmo-line);--el-segmented-item-selected-color:var(--pmo-primary-hover);--el-segmented-item-selected-bg-color:transparent}
.assistant-toolbar :deep(.el-segmented__group){flex-wrap:wrap;justify-content:flex-start}
.assistant-toolbar :deep(.el-segmented__item){flex:0 0 auto;border-radius:0;border-bottom:2px solid transparent;padding-inline:12px}
.assistant-toolbar :deep(.el-segmented__item.is-selected){border-color:var(--pmo-primary);font-weight:600}
.assistant-toolbar :deep(.el-segmented__item-label) {line-height:38px}
.filters label {flex:1 1 140px;min-width:0;margin:0}
.filters input,.filters select {width:100%;min-width:0}
.assistant-row {display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px}
h3 {font-size:16px;font-weight:600;margin:16px 0 8px}
.assistant-row h3,.assistant-panel > h3:first-child {margin:0}
.summary-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,145px),1fr));gap:8px;margin:12px 0 8px}
.summary-grid div {border-inline-start:1px solid var(--pmo-line);padding:0 12px;min-width:0}
dt {color:var(--pmo-muted);font-size:13px}
dd {margin:4px 0 0}
.summary-grid dd {font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.process-grid dt {display:flex;align-items:center;gap:4px}
.process-grid dt > svg {width:15px;height:15px;flex:none;color:var(--pmo-primary-hover)}
.health-swatch {display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--el-color-info)}
.health-swatch[data-state=green] {background:var(--el-color-success)}
.health-swatch[data-state=yellow] {background:var(--el-color-warning)}
.health-swatch[data-state=red] {background:var(--el-color-danger)}
.sections {margin:12px 0 0}
.sections div {display:grid;grid-template-columns:minmax(100px,155px) minmax(0,1fr);align-items:baseline;gap:4px 12px;padding:10px 0;border-bottom:1px solid var(--pmo-line)}
.sections div:last-child {border-bottom:0;padding-bottom:0}
.sections dt {display:flex;align-items:center;gap:8px;color:var(--pmo-text);font-weight:600}
.sections dd {grid-column:2;margin:0;color:var(--pmo-secondary);white-space:pre-wrap}
.packet-sources,.meta {color:var(--pmo-muted)}
.packet-sources summary {cursor:pointer;font-weight:500}
code {display:block;font-size:12px;margin:4px 0}
@media (max-width:560px) {
  .assistant-toolbar :deep(.el-segmented) {width:100%;min-width:0}
  .assistant-toolbar :deep(.el-segmented__item) {flex:1 0 auto;min-width:0;padding-inline:6px}
  .assistant-toolbar :deep(.el-segmented__item-label) {font-size:13px}
  .filters {width:100%}
  .sections div {grid-template-columns:1fr}
  .sections dd {grid-column:1}
}
</style>
