<script setup lang="ts">
import {ref,watch} from 'vue'
import {Check,FilePlus,FileText,Pencil,Plus,RefreshCw,Save,Send,Trash2,X} from 'lucide-vue-next'
import ReportFacts from './ReportFacts.vue'
import WeeklyEffortPosting from './WeeklyEffortPosting.vue'

type Kind='weekly'|'daily'
type Status='draft'|'submitted'|'confirmed'
type Section='milestone_progress'|'requirement_progress'|'risks_and_blockers'
type Entry={summary?:string;name?:string;description?:string;milestoneId?:string;requirementId?:string;riskId?:string;
  claim?:'progress'|'completion';evidenceRef?:string;evidenceRefs?:string[]}
type Fields=Record<Section,Entry[]>&{effort_person_days?:string}
type SourcedFact={sourceRef:string;reportRef:string;reportHash:string;quotePath:string;quoteOffset:number;quote:string;
  subject:string;predicate:string;trust:'high'|'medium';asOf:string;evidenceRef?:string;evidenceHash?:string}
type Basis={asOf:string;ruleVersion:string;projectRevision:number;sourceHash:string;sourceRefs:string[];verifiedPeriodPersonDays:string;
  verifiedPeriodPostingCount:number;omittedRequirements:number;omittedRisks:number;omittedVerifiedFacts:number;verifiedFacts:SourcedFact[];
  health:{health:string;dataTrust:string;timeLine?:string;costLine?:string;qualityLine?:string;requirementSatisfactionRate?:string;gaps:string[]}}
type Report={reportId:string;projectId:string;type:Kind;period:string;periodStart:string;periodEnd:string;versionNo:number;revision:number;
  status:Status;authorId:string;submittedAt?:string;confirmedAt?:string;structuredFields?:Fields;narrative?:string;
  isCurrent:boolean;isCurrentConfirmed:boolean;canEdit:boolean;canSubmit:boolean;canConfirm:boolean;contentHash?:string;
  legacyStructuredFields?:boolean;weeklyBasis?:Basis}
type Editor={item?:Report;type:Kind;day:string;effort:string;fields:Record<Section,Entry[]>;narrative:string}
type Milestone={milestoneId:string;name:string;isKey:boolean}
type Evidence={evidenceRef:string;linkedResourceType:string;linkedResourceId:string;originalName:string}
type Attempt={signature:string;key:string}

const props=defineProps<{projectId:string;focusReportId?:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const sections:{key:Section;label:string;empty:string;add:string}[]=[
  {key:'milestone_progress',label:'里程碑进展',empty:'本期无里程碑申报',add:'添加里程碑申报'},
  {key:'requirement_progress',label:'需求进展',empty:'本期无需求进展记录',add:'添加需求进展'},
  {key:'risks_and_blockers',label:'风险与阻塞',empty:'本期无风险或阻塞记录',add:'添加风险或阻塞'},
]
const labels:Record<Status,string>={draft:'草稿',submitted:'待确认',confirmed:'已确认'}
const rows=ref<Report[]>([]), detail=ref<Report|null>(null), editor=ref<Editor|null>(null)
const filter=ref<Kind|''>(''), page=ref(1), total=ref(0), canCreate=ref(false)
const generationRevision=ref<number|null>(null), generating=ref(false), generationDay=ref('')
const loading=ref(false), reading=ref(false), busy=ref(false), error=ref(''), sourceError=ref(''), notice=ref('')
const milestones=ref<Milestone[]>([]), evidence=ref<Evidence[]>([])
const size=12
let epoch=0,listRead=0,detailRead=0,sourceRead=0,attempt:Attempt|null=null

function today(){
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date())
  return ['year','month','day'].map(k=>parts.find(p=>p.type===k)?.value).join('-')
}
function isoWeek(day:string){
  const date=new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate()+4-(date.getUTCDay()||7))
  const year=date.getUTCFullYear(),first=Date.UTC(year,0,1)
  return `${year}-W${String(Math.floor((date.valueOf()-first)/604800000)+1).padStart(2,'0')}`
}
function time(at:string){return new Date(at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function reportStatus(row:Report){
  if(row.isCurrentConfirmed)return '已发布'
  return row.status==='confirmed'&&!row.confirmedAt?'历史标记（无回执）':labels[row.status]
}
function lamp(color:string){return ({green:'绿',yellow:'黄',red:'红',untrusted:'不可信',unavailable:'不可用'} as Record<string,string>)[color]??color}
function base(){return `projects/${props.projectId}/reports`}
function target(row:Report){return `reports/${row.reportId}`}
function sourceLabel(id?:string){return milestones.value.find(item=>item.milestoneId===id)?.name??id}
function entryText(item:Entry){return typeof item.summary==='string'?item.summary:typeof item.name==='string'?item.name:typeof item.description==='string'?item.description:''}
function evidenceRefs(item:Entry){return [...new Set([...(item.evidenceRefs??[]),...(item.evidenceRef?[item.evidenceRef]:[])])]}
function setRefs(item:Entry,refs:string[]){item.evidenceRefs=refs;delete item.evidenceRef}
function evidenceOptions(item:Entry){
  const options=evidence.value.filter(file=>file.linkedResourceType==='milestone'&&file.linkedResourceId===item.milestoneId)
    .map(file=>({value:file.evidenceRef,label:file.originalName}))
  for(const value of evidenceRefs(item))if(!options.some(x=>x.value===value))options.push({value,label:value})
  return options
}
function problem(e:unknown){
  if(e instanceof TypeError)return '无法连接服务，请重试。'
  const message=e instanceof Error?e.message:'操作失败。'
  const known:Record<string,string>={REPORT_INCOMPLETE:'请先填写人天并保存草稿。',REPORT_PERIOD_IN_FUTURE:'不能提交未来周期的报告。',
    REPORT_SOURCE_SCOPE:'引用与项目或填报事项不一致。',REPORT_VERSION_SUPERSEDED:'已有更新版本发布，此版本不能再发布。',
    WEEKLY_DRAFT_ALREADY_CURRENT:'本周已有同一来源的草稿或待确认报告。',WEEKLY_DRAFT_SOURCE_CHANGED:'台账或核验来源已变化，请重新生成该周的草稿。',
    REPORT_NOT_DRAFT:'该报告已提交，不能再编辑。',PROJECT_ARCHIVED:'项目已归档，报告只能查看。'}
  return Object.entries(known).find(([code])=>message.includes(code))?.[1]??message
}

async function list(next=page.value){
  const ticket=++listRead,scope=epoch
  loading.value=true
  try{
    const query=new URLSearchParams({page:String(next),pageSize:String(size)})
    if(filter.value)query.set('reportType',filter.value)
    const {data}=await props.api(`${base()}?${query}`)
    if(ticket!==listRead||scope!==epoch)return
    rows.value=data.items;total.value=data.total;page.value=data.page;canCreate.value=data.canCreateDraft===true
    generationRevision.value=data.weeklyGenerationRevision??null
  }catch(e){if(ticket===listRead&&scope===epoch){error.value=problem(e);canCreate.value=false;generationRevision.value=null}}
  finally{if(ticket===listRead&&scope===epoch)loading.value=false}
}
async function open(reportId:string){
  const ticket=++detailRead,scope=epoch
  reading.value=true
  try{
    const {data}=await props.api(`reports/${reportId}`)
    if(data.projectId!==props.projectId)throw Error('报告不属于当前项目。')
    if(ticket===detailRead&&scope===epoch)detail.value=data
  }catch(e){if(ticket===detailRead&&scope===epoch){detail.value=null;error.value=problem(e)}}
  finally{if(ticket===detailRead&&scope===epoch)reading.value=false}
}
async function loadSources(){
  const scope=epoch,ticket=++sourceRead,pid=props.projectId
  sourceError.value=''
  const result=await Promise.allSettled([props.api(`projects/${pid}/milestones`),
    props.api(`projects/${pid}/evidence?verifiedOnly=true&pageSize=100`)])
  if(scope!==epoch||ticket!==sourceRead)return
  if(result[0]?.status==='fulfilled')milestones.value=result[0].value.data.items
  if(result[1]?.status==='fulfilled')evidence.value=result[1].value.data.items
  if(result.some(item=>item.status==='rejected'))sourceError.value='部分里程碑或附件未加载。'
}
function begin(item?:Report){
  if(busy.value||reading.value)return
  const copy=(key:Section):Entry[]=>JSON.parse(JSON.stringify(item?.structuredFields?.[key]??[]))
  generating.value=false;error.value='';notice.value='';attempt=null
  editor.value={item,type:item?.type??'weekly',day:item?.periodStart??today(),effort:item?.structuredFields?.effort_person_days??'',
    fields:{milestone_progress:copy('milestone_progress'),requirement_progress:copy('requirement_progress'),risks_and_blockers:copy('risks_and_blockers')},
    narrative:item?.narrative??''}
  void loadSources()
}
function add(key:Section){
  const items=editor.value?.fields[key]
  if(!items||items.length>=100)return
  items.push(key==='milestone_progress'?{milestoneId:'',claim:'progress',summary:''}:{summary:''})
}
function cancel(){editor.value=null;generating.value=false;attempt=null;error.value=''}
function changeFilter(){detail.value=null;error.value='';notice.value='';void list(1)}
function refresh(){error.value='';notice.value='';void list();if(detail.value)void open(detail.value.reportId)}

async function write(path:string,method:'PATCH'|'POST',payload:object,noticeText:string,revision?:number){
  const scope=epoch,body=JSON.stringify(payload),signature=JSON.stringify([path,method,revision,body])
  if(busy.value)return
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  const headers:Record<string,string>={'Idempotency-Key':attempt.key}
  if(revision!==undefined)headers['If-Match']=String(revision)
  busy.value=true;error.value='';notice.value=''
  try{
    const {data}=await props.api(path,{method,headers,body})
    if(scope!==epoch)return
    editor.value=null;generating.value=false;attempt=null;notice.value=noticeText
    if(filter.value&&filter.value!==data.type)filter.value=''
    await Promise.all([list(1),open(data.reportId)])
  }catch(e){if(scope===epoch)error.value=problem(e)}
  finally{if(scope===epoch)busy.value=false}
}
function save(){
  const form=editor.value
  if(!form)return
  const effort=form.effort.trim()
  if(effort&&!/^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$/.test(effort)){error.value='人天须为非负数，最多两位小数。';return}
  if(form.fields.milestone_progress.some(item=>item.claim&&!item.milestoneId)){error.value='请为里程碑申报选择里程碑。';return}
  const fields:Fields={...form.fields,milestone_progress:form.fields.milestone_progress.map(item=>{
    const value={...item};if(!value.milestoneId)delete value.milestoneId;return value
  })}
  if(effort)fields.effort_person_days=effort
  const action={structuredFields:fields,narrative:form.narrative}
  if(form.item)void write(target(form.item),'PATCH',action,'草稿已保存。',form.item.revision)
  else void write(`${base()}/drafts`,'POST',{...action,reportType:form.type,period:form.type==='weekly'?isoWeek(form.day):form.day},'草稿已保存。')
}
function startGeneration(){
  error.value='';notice.value='';attempt=null;generationDay.value=detail.value?.type==='weekly'?detail.value.periodStart:today()
  generating.value=true
}
function generate(){
  if(generationRevision.value!==null&&generationDay.value)
    void write(`${base()}/weekly-drafts`,'POST',{period:isoWeek(generationDay.value)},'周报初稿已生成，尚未发布。',generationRevision.value)
}
function submitDraft(row:Report){void write(`${target(row)}/submit`,'POST',{},'报告已提交。',row.revision)}
function publish(row:Report){void write(`${target(row)}/confirm`,'POST',{},'报告已发布。',row.revision)}

watch(()=>props.projectId,()=>{
  epoch++;rows.value=[];detail.value=null;editor.value=null;attempt=null;busy.value=false;reading.value=false
  generating.value=false;generationRevision.value=null
  canCreate.value=false;total.value=0;filter.value='';notice.value='';error.value='';sourceError.value='';milestones.value=[];evidence.value=[]
  void list(1)
},{immediate:true})
watch(()=>[props.projectId,props.focusReportId],()=>{if(props.focusReportId)void open(props.focusReportId)},{immediate:true})
</script>

<template>
  <section class="panel reports" aria-label="日报与周报">
    <div class="report-toolbar">
      <label>报告类型 <select v-model="filter" aria-label="筛选报告类型" :disabled="busy||!!editor||reading" @change="changeFilter">
        <option value="">全部</option><option value="weekly">周报</option><option value="daily">日报</option>
      </select></label>
      <div class="report-actions">
        <el-button v-if="generationRevision!==null" :disabled="busy||loading||!!editor||reading||generating" @click="startGeneration"><FilePlus :size="15"/>生成周报初稿</el-button>
        <el-button v-if="canCreate" type="primary" :disabled="busy||!!editor||reading" @click="begin()"><Plus :size="15"/>新建报告草稿</el-button>
        <el-button aria-label="刷新报告" title="刷新报告" :disabled="busy||loading||!!editor" @click="refresh"><RefreshCw :size="15"/></el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <p v-if="notice" class="report-notice" role="status">{{notice}}</p>

    <form v-if="generating" class="report-form" aria-label="生成周报" @submit.prevent="generate">
      <div class="report-toolbar">
        <label>周内日期<input v-model="generationDay" type="date" required :max="today()" :disabled="busy"></label>
        <span v-if="generationDay" class="report-muted">{{isoWeek(generationDay)}}</span>
        <div class="report-actions">
          <el-button :disabled="busy" @click="cancel"><X :size="15"/>取消</el-button>
          <el-button type="primary" native-type="submit" :loading="busy" :disabled="generationRevision===null"><FilePlus :size="15"/>生成草稿</el-button>
        </div>
      </div>
    </form>

    <form v-if="editor" class="report-form" aria-label="报告草稿" @submit.prevent="save">
      <h3>{{editor.item ? `编辑 ${editor.item.period} · v${editor.item.versionNo}` : '新建报告草稿'}}</h3>
      <el-alert v-if="editor.item?.legacyStructuredFields" title="旧版扩展字段不会保留在这次草稿修改中" type="warning" :closable="false"/>
      <div class="report-fields">
        <label v-if="!editor.item">填报类型<select v-model="editor.type" :disabled="busy"><option value="weekly">周报</option><option value="daily">日报</option></select></label>
        <label v-if="!editor.item">{{editor.type==='weekly'?'报告周内日期':'报告日期'}}<input v-model="editor.day" type="date" required :max="today()" :disabled="busy"></label>
        <label>申报人天<input v-model="editor.effort" type="text" inputmode="decimal" maxlength="13" :disabled="busy"></label>
      </div>
      <el-alert v-if="sourceError" :title="sourceError" type="warning" :closable="false"/>
      <fieldset v-for="section in sections" :key="section.key" :disabled="busy">
        <legend>{{section.label}}</legend>
        <p v-if="!editor.fields[section.key].length" class="report-muted">{{section.empty}}</p>
        <div v-for="(entry,index) in editor.fields[section.key]" :key="index" class="report-entry">
          <div v-if="section.key==='milestone_progress'" class="report-fields">
            <label>里程碑<select v-model="entry.milestoneId" :required="!!entry.claim" @change="setRefs(entry,[])">
              <option value="">请选择</option>
              <option v-if="entry.milestoneId&&!milestones.some(m=>m.milestoneId===entry.milestoneId)" :value="entry.milestoneId">{{entry.milestoneId}}</option>
              <option v-for="m in milestones" :key="m.milestoneId" :value="m.milestoneId">{{m.name}}{{m.isKey ? '（关键）' : ''}}</option>
            </select></label>
            <label>申报类型<select v-model="entry.claim"><option v-if="!entry.claim" :value="undefined">备注</option><option value="progress">进展</option><option value="completion">完工申报</option></select></label>
            <label v-if="entry.milestoneId">附件引用<el-select :model-value="evidenceRefs(entry)" :options="evidenceOptions(entry)" multiple clearable filterable
              placeholder="未引用" :disabled="busy" @update:model-value="(value: string[])=>setRefs(entry,value)"/></label>
          </div>
          <div class="report-entry-text">
            <label>说明<textarea v-model="entry.summary" rows="2" maxlength="3000" :required="section.key!=='milestone_progress'&&!entryText(entry)"/></label>
            <el-button text title="删除条目" aria-label="删除条目" @click="editor.fields[section.key].splice(index,1)"><Trash2 :size="15"/></el-button>
          </div>
        </div>
        <el-button size="small" :disabled="busy||editor.fields[section.key].length>=100" @click="add(section.key)"><Plus :size="14"/>{{section.add}}</el-button>
      </fieldset>
      <label>补充说明<textarea v-model="editor.narrative" rows="3" maxlength="20000" :disabled="busy"/></label>
      <div class="report-actions"><el-button :disabled="busy" @click="cancel"><X :size="15"/>取消</el-button>
        <el-button type="primary" native-type="submit" :loading="busy"><Save :size="15"/>保存草稿</el-button></div>
    </form>

    <el-table v-else v-loading="loading" :data="rows" row-key="reportId" empty-text="暂无报告">
      <el-table-column label="报告周期" min-width="130"><template #default="{row}">{{row.period}} · {{row.type==='weekly'?'周报':'日报'}}</template></el-table-column>
      <el-table-column label="版本" width="78"><template #default="{row}">v{{row.versionNo}}</template></el-table-column>
      <el-table-column label="状态" min-width="100"><template #default="{row}">
        <el-tag size="small" :type="row.isCurrentConfirmed?'success':row.status==='submitted'?'warning':'info'">{{reportStatus(row)}}</el-tag>
      </template></el-table-column>
      <el-table-column label="提交时间（上海）" min-width="160"><template #default="{row}">{{row.submittedAt?time(row.submittedAt):'未提交'}}</template></el-table-column>
      <el-table-column label="操作" width="92"><template #default="{row}"><el-button text :disabled="busy||reading" @click="open(row.reportId)"><FileText :size="15"/>查看</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="!editor&&total>size" :current-page="page" :page-size="size" :total="total" layout="prev,pager,next" :disabled="loading||busy" @current-change="list"/>

    <article v-if="detail&&!editor" v-loading="reading" aria-label="报告详情" class="report-detail">
      <div class="report-toolbar">
        <h3>{{detail.period}} · v{{detail.versionNo}} · {{reportStatus(detail)}}</h3>
        <div class="report-actions">
          <el-button v-if="detail.canEdit" :disabled="busy||reading" @click="begin(detail)"><Pencil :size="15"/>编辑草稿</el-button>
          <el-button v-if="detail.canSubmit" type="primary" :disabled="reading||!detail.structuredFields?.effort_person_days" :loading="busy" @click="submitDraft(detail)"><Send :size="15"/>提交报告</el-button>
          <el-popconfirm v-if="detail.canConfirm" :title="`发布 ${detail.period} 的 v${detail.versionNo}？`"
            confirm-button-text="发布报告" cancel-button-text="取消" @confirm="publish(detail)">
            <template #reference><el-button type="primary" :disabled="reading" :loading="busy"><Check :size="15"/>确认并发布</el-button></template>
          </el-popconfirm>
          <el-button text title="关闭报告详情" aria-label="关闭报告详情" :disabled="busy||reading" @click="detail=null"><X :size="15"/></el-button>
        </div>
      </div>
      <p v-if="detail.legacyStructuredFields" class="report-muted">旧版扩展字段未展示，原记录不变。</p>
      <dl class="report-meta">
        <div><dt>申报人天</dt><dd>{{detail.structuredFields?.effort_person_days??'未填写'}}</dd></div>
        <div v-if="detail.weeklyBasis"><dt>当周核验人天净额</dt><dd>{{detail.weeklyBasis.verifiedPeriodPostingCount? `${detail.weeklyBasis.verifiedPeriodPersonDays}（${detail.weeklyBasis.verifiedPeriodPostingCount} 笔）`:'无核验流水'}}</dd></div>
        <div v-if="detail.confirmedAt"><dt>确认时间（上海）</dt><dd>{{time(detail.confirmedAt)}}</dd></div>
      </dl>
      <details v-if="detail.weeklyBasis" class="report-basis">
        <summary>生成依据 · {{time(detail.weeklyBasis.asOf)}}（上海） · {{lamp(detail.weeklyBasis.health.health)}}</summary>
        <p v-if="detail.weeklyBasis.health.timeLine">
          时间 {{lamp(detail.weeklyBasis.health.timeLine)}} · 成本 {{lamp(detail.weeklyBasis.health.costLine??'unavailable')}} · 质量 {{lamp(detail.weeklyBasis.health.qualityLine??'unavailable')}}
        </p>
        <p v-if="detail.weeklyBasis.health.requirementSatisfactionRate">需求满足率 {{detail.weeklyBasis.health.requirementSatisfactionRate==='UNAVAILABLE'?'不可计算':`${(Number(detail.weeklyBasis.health.requirementSatisfactionRate)*100).toFixed(2)}%`}}</p>
        <p v-if="detail.weeklyBasis.health.gaps.length" class="report-muted">{{detail.weeklyBasis.health.gaps.join(' · ')}}</p>
        <p v-if="detail.weeklyBasis.omittedRequirements||detail.weeklyBasis.omittedRisks" class="report-muted">
          摘要未列出：需求 {{detail.weeklyBasis.omittedRequirements}} 项 · 风险 {{detail.weeklyBasis.omittedRisks}} 项
        </p>
        <dl class="report-meta"><div><dt>规则版本</dt><dd>{{detail.weeklyBasis.ruleVersion}}</dd></div>
          <div><dt>项目版本</dt><dd>{{detail.weeklyBasis.projectRevision}}</dd></div></dl>
        <p class="report-copy">依据摘要 <code>{{detail.weeklyBasis.sourceHash}}</code></p>
        <section v-if="detail.weeklyBasis.verifiedFacts.length" aria-label="生成时已核验 Fact">
          <h4>当周已核验 Fact</h4>
          <dl v-for="fact in detail.weeklyBasis.verifiedFacts" :key="fact.sourceRef">
            <dt>{{fact.subject}} · {{fact.predicate}}（{{fact.trust==='high'?'高':'中'}}）</dt>
            <dd class="report-copy">{{fact.quote}}</dd>
            <dd class="report-copy report-muted">{{fact.reportRef}} · {{fact.quotePath}}:{{fact.quoteOffset}}{{fact.evidenceRef?` · ${fact.evidenceRef}`:''}}</dd>
          </dl>
          <p v-if="detail.weeklyBasis.omittedVerifiedFacts" class="report-muted">另有 {{detail.weeklyBasis.omittedVerifiedFacts}} 项未列出。</p>
        </section>
        <details><summary>来源引用（{{detail.weeklyBasis.sourceRefs.length}}）</summary>
          <ul class="report-copy"><li v-for="ref in detail.weeklyBasis.sourceRefs" :key="ref"><code>{{ref}}</code></li></ul>
        </details>
      </details>
      <div class="report-read-grid">
        <section v-for="section in sections" :key="section.key"><h4>{{section.label}}</h4>
          <ul v-if="detail.structuredFields?.[section.key].length">
            <li v-for="(entry,index) in detail.structuredFields[section.key]" :key="index">
              <span v-if="entry.milestoneId">{{sourceLabel(entry.milestoneId)}} · {{entry.claim==='completion'?'完工申报':entry.claim==='progress'?'进展申报':'备注'}}</span>
              <span v-if="entryText(entry)" class="report-copy">{{entryText(entry)}}</span>
              <small v-if="entry.claim||evidenceRefs(entry).length" class="report-muted">{{evidenceRefs(entry).length?`已引用 ${evidenceRefs(entry).length} 项附件`:'未引用佐证'}}</small>
              <pre v-if="!entryText(entry)&&!entry.milestoneId" class="report-copy">{{JSON.stringify(entry,null,2)}}</pre>
            </li>
          </ul><p v-else class="report-muted">{{section.empty}}</p>
        </section>
        <section v-if="detail.narrative"><h4>补充说明</h4><p class="report-copy">{{detail.narrative}}</p></section>
      </div>
      <WeeklyEffortPosting v-if="detail.type==='weekly'&&detail.isCurrentConfirmed&&detail.contentHash"
        :key="`effort-${detail.reportId}`" :report-id="detail.reportId" :revision="detail.revision" :api="api"/>
      <ReportFacts v-if="detail.submittedAt&&detail.contentHash" :key="detail.reportId" :project-id="projectId" :report-id="detail.reportId"
        :initially-expanded="focusReportId===detail.reportId"
        :current-weekly="detail.type==='weekly'&&detail.isCurrentConfirmed"
        :revision="detail.revision" :submitted-at="detail.submittedAt" :api="api"/>
    </article>
  </section>
</template>

<style scoped>
.reports{display:grid;gap:12px;color:var(--el-text-color-primary)}
.report-toolbar,.report-actions,.report-meta{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.report-actions{justify-content:flex-start}.report-actions :deep(.el-button){margin-left:0}.report-actions svg{margin-right:4px}
.report-toolbar label{display:flex;align-items:center;gap:8px}.report-toolbar select{width:auto;min-width:96px}
.report-form,.report-detail{display:grid;gap:16px;border:1px solid var(--pmo-line);background:var(--pmo-bg);border-radius:8px;padding:16px}
.report-form h3,.report-detail h3,.report-detail h4{margin:0;font-size:16px}.report-form label{display:grid;gap:4px;min-width:0}
.report-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,136px),1fr));gap:8px;align-items:end}
.report-form fieldset{min-width:0;padding:12px 16px;border:1px solid var(--pmo-line);background:var(--pmo-surface);border-radius:8px;margin:0}
.report-form legend{padding:0 4px;font-weight:600}.report-entry{margin-bottom:10px;display:grid;gap:6px}
.report-entry-text{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:4px}
.report-read-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px}
.report-read-grid section {min-width:0;padding:12px 16px;background:var(--pmo-surface);border:1px solid var(--pmo-line);border-radius:8px}
.report-read-grid ul{padding-left:18px;margin:6px 0}.report-read-grid li{margin-bottom:6px;overflow-wrap:anywhere}
.report-meta{justify-content:flex-start;gap:16px;margin:0}.report-meta div{display:flex;align-items:baseline;gap:7px}.report-meta dd{margin:0}
.report-copy{display:block;white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0}
.report-basis{padding:8px 0;border-block:1px solid var(--el-border-color-light);overflow-wrap:anywhere}.report-basis summary{cursor:pointer;font-weight:600}
.report-muted{color:var(--el-text-color-secondary);margin:4px 0}.report-notice{color:var(--el-color-success-dark-2);margin:0}
</style>
