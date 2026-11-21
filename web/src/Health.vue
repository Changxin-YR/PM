<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {CircleHelp,ClipboardCheck,Gavel,RefreshCw,Save} from 'lucide-vue-next'

type Appeal={appealId:string;reason:string;appealedAt:string;revision:number;verdict?:string;rationale?:string;reviewedAt?:string;canReview:boolean}
type CostTrace={budget:string|null;actual:string;remaining:string;projected:string}
type Health={health:string;dataTrust:string;publication:string;historical:boolean;asOf:string;ruleVersion:string;sourceRefs:string[];
  timeLine?:string;costLine?:string;qualityLine?:string;moneyVarianceRate?:string;effortVarianceRate?:string;requirementSatisfactionRate?:string;
  snapshotId?:string;inputHash?:string;calculatedAt?:string;gaps?:string[];appeal?:Appeal;canAppeal?:boolean;canRefresh?:boolean;
  projectRevision?:number;zeroBudgetHashes?:Record<string,string>;inputs?:{cost:Record<string,CostTrace>;weekly:{missingPeriods:string[]}}}
type Intent={mode:'appeal'|'review'|'zero';row:Health;kind?:string}
const props=defineProps<{projectId:string;active:boolean;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const current=ref<Health|null>(null),rows=ref<Health[]>([]),page=ref(1),total=ref(0)
const loading=ref(false),saving=ref(false),error=ref(''),formError=ref(''),notice=ref('')
const intent=ref<Intent|null>(null),open=ref(false),reason=ref(''),verdict=ref('upheld')
const base=`projects/${props.projectId}/health`
const healthNames:Record<string,string>={green:'绿',yellow:'黄',red:'红',untrusted:'数据不可信',unavailable:'不可用'}
const publication:Record<string,string>={official:'正式快照',preview:'预览',withheld:'暂停正式发布',unavailable:'未进入监控阶段'}
const gaps:Record<string,string>={LR06_TWO_WEEKS_MISSED:'连续两期应交周报缺失',LR06_CLAIM_WITHOUT_EVIDENCE:'关键里程碑申报缺少已核验佐证',
  WEEKLY_PM_CONFIRMATION_PENDING:'最近一期应交周报尚未获 PM 确认',INITIAL_BASELINE_MISSING:'缺少初始需求基线',
  NO_ACTIVE_REQUIREMENTS:'没有有效需求，满足率不可用',NO_ACTIVE_MILESTONES:'没有有效里程碑',
  MILESTONE_COMPLETION_EVIDENCE_INVALID:'里程碑完成凭据不可核验',BASELINE_CHAIN_INVALID:'需求基线链不可核验',
  MONEY_BUDGET_MISSING:'缺金额预算',EFFORT_BUDGET_MISSING:'缺人天预算',MONEY_ZERO_BUDGET_UNDECLARED:'零金额预算缺 PM 声明',
  EFFORT_ZERO_BUDGET_UNDECLARED:'零人天预算缺 PM 声明',HEALTH_NOT_ACTIVE:'当前阶段不计算三线',
  EXECUTION_RECEIPT_MISSING:'缺执行阶段回执',REQUIREMENT_PLAN_DATE_MISSING:'缺需求计划确认日',
  CONFIRMATION_RECEIPT_MISSING:'客户确认缺少有效回执'}
const title=computed(()=>intent.value?.mode==='appeal'?'申诉健康快照':intent.value?.mode==='review'?'复核健康申诉':'声明零预算')
let attempt:{signature:string;key:string}|undefined,sequence=0

function time(value?:string){return value?new Date(value).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'未记录'}
function rate(value?:string){return value&&value!=='UNAVAILABLE'?`${(Number(value)*100).toFixed(2)}%`:'不可用'}
function label(value?:string){return value?healthNames[value]??value:'不可用'}
function tag(value:string){return value==='green'?'success':value==='yellow'||value==='untrusted'?'warning':value==='red'?'danger':'info'}
function message(e:unknown){return e instanceof TypeError?'无法连接服务。':e instanceof Error?e.message:'操作失败。'}
function writeHeaders(rev:number,path:string,body?:string){
  const signature=JSON.stringify([path,rev,body])
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  return {'If-Match':String(rev),'Idempotency-Key':attempt.key}
}
async function read(n=1){
  const id=++sequence; loading.value=true;error.value=''
  try{
    const [now,history]=await Promise.all([props.api(base),props.api(`${base}/history?page=${n}`)])
    if(id!==sequence)return
    current.value=now.data;rows.value=history.data.items;total.value=history.data.total;page.value=n
  }catch(e){if(id===sequence){current.value=null;rows.value=[];total.value=0;error.value=message(e)}}
  finally{if(id===sequence)loading.value=false}
}
onMounted(()=>{if(props.active)void read()})
watch(()=>props.active,active=>{if(active)void read()})

async function refresh(){
  const row=current.value,path=`${base}/refresh`
  if(!row?.canRefresh||!row.projectRevision||loading.value||saving.value)return
  saving.value=true;error.value='';notice.value=''
  try{
    await props.api(path,{method:'POST',headers:writeHeaders(row.projectRevision,path)})
    attempt=undefined;notice.value='已记录本次计算。';await read()
  }catch(e){error.value=message(e)}finally{saving.value=false}
}
function start(mode:Intent['mode'],row:Health,kind?:string){
  intent.value={mode,row,kind};reason.value='';verdict.value='upheld';attempt=undefined;formError.value='';notice.value='';open.value=true
}
async function send(){
  const act=intent.value,why=reason.value.trim()
  if(!act||why.length<5||saving.value)return
  const {mode,row,kind}=act
  const hash=kind&&row.zeroBudgetHashes?.[kind]
  const operation=mode==='appeal'&&row.snapshotId?{path:`health-snapshots/${row.snapshotId}/appeals`,revision:1,body:{reason:why}}:
    mode==='review'&&row.appeal?.canReview?{path:`health-appeals/${row.appeal.appealId}/review`,revision:row.appeal.revision,body:{verdict:verdict.value,rationale:why}}:
    mode==='zero'&&kind&&hash&&row.projectRevision?{path:`${base}/zero-budget`,revision:row.projectRevision,body:{type:kind,reason:why,baselineHash:hash}}:null
  if(!operation)return
  saving.value=true;formError.value=''
  try{
    const body=JSON.stringify(operation.body)
    await props.api(operation.path,{method:'POST',body,headers:writeHeaders(operation.revision,operation.path,body)})
    open.value=false;attempt=undefined;notice.value=mode==='review'?'复核已登记，原快照保持不变。':mode==='zero'?'零预算声明已登记。':'申诉已登记。'
    await read(page.value)
  }catch(e){formError.value=message(e)}finally{saving.value=false}
}
</script>

<template>
  <section aria-label="三线健康" :aria-busy="loading||saving">
    <div class="toolbar health-toolbar">
      <h3>当前健康</h3>
      <div class="controls">
        <el-button v-if="current?.canRefresh" :disabled="loading||saving" @click="refresh"><Save :size="15"/>记录本次快照</el-button>
        <el-button aria-label="重读健康" title="重读健康" :disabled="loading||saving" @click="read(page)"><RefreshCw :size="15"/></el-button>
      </div>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <p v-if="notice" role="status">{{notice}}</p>
    <template v-if="current">
      <div class="toolbar health-meta">
        <el-tag :type="tag(current.health)" effect="plain">{{label(current.health)}}</el-tag>
        <span>{{publication[current.publication]}}</span><span>{{time(current.asOf)}} 北京时间</span>
      </div>
      <dl class="metrics">
        <div><dt>时间线</dt><dd>{{label(current.timeLine)}}</dd></div>
        <div><dt>金额偏差</dt><dd>{{rate(current.moneyVarianceRate)}}</dd></div>
        <div><dt>人天偏差</dt><dd>{{rate(current.effortVarianceRate)}}</dd></div>
        <div><dt>需求满足率</dt><dd>{{rate(current.requirementSatisfactionRate)}}</dd></div>
      </dl>
      <p v-if="current.costLine||current.qualityLine" class="health-meta">成本线：{{label(current.costLine)}} · 质量线：{{label(current.qualityLine)}}</p>
      <ul v-if="current.gaps?.length" class="gaps"><li v-for="gap in current.gaps" :key="gap">{{gaps[gap]??gap}}</li></ul>
      <div class="controls" v-if="Object.keys(current.zeroBudgetHashes??{}).length">
        <el-button v-for="kind in Object.keys(current.zeroBudgetHashes??{})" :key="kind" :disabled="saving||loading" @click="start('zero',current,kind)">
          <ClipboardCheck :size="15"/>声明零{{kind==='money'?'金额':'人天'}}预算
        </el-button>
      </div>
      <details class="trace"><summary>本次来源与规则版本</summary>
        <p>{{current.ruleVersion}}</p><ul><li v-for="source in current.sourceRefs" :key="source"><code>{{source}}</code></li></ul>
      </details>
    </template>
    <h3>历史快照与申诉</h3>
    <p v-if="!rows.length&&!loading" class="health-meta">暂无历史快照。</p>
    <ul class="history" v-else>
      <li v-for="row in rows" :key="row.snapshotId">
        <div class="toolbar health-toolbar">
          <div class="history-meta"><time :datetime="row.asOf">{{time(row.asOf)}}</time>
            <el-tag :type="tag(row.health)" size="small" effect="plain">{{label(row.health)}}</el-tag><span>{{publication[row.publication]}}</span></div>
          <el-button v-if="row.canAppeal" :disabled="loading||saving" @click="start('appeal',row)"><CircleHelp :size="15"/>申请复核</el-button>
        </div>
        <dl v-if="row.appeal" class="appeal">
          <dt>申诉理由</dt><dd>{{row.appeal.reason}}</dd>
          <dt>复核结果</dt><dd>{{row.appeal.verdict==='upheld'?'维持原快照':row.appeal.verdict==='recompute'?'按新材料重新计算':'待独立复核'}}</dd>
          <template v-if="row.appeal.rationale"><dt>复核理由</dt><dd>{{row.appeal.rationale}} · {{time(row.appeal.reviewedAt)}}</dd></template>
        </dl>
        <el-button v-if="row.appeal?.canReview" :disabled="loading||saving" @click="start('review',row)"><Gavel :size="15"/>复核申诉</el-button>
        <details class="trace"><summary>快照指标与来源</summary>
          <p>时间 {{label(row.timeLine)}} · 金额 {{rate(row.moneyVarianceRate)}} · 人天 {{rate(row.effortVarianceRate)}} · 需求 {{rate(row.requirementSatisfactionRate)}}</p>
          <p v-for="gap in row.gaps??[]" :key="gap">{{gaps[gap]??gap}}</p>
          <p>计算时间 {{time(row.calculatedAt)}} · {{row.ruleVersion}}</p><code>{{row.inputHash}}</code>
          <ul><li v-for="source in row.sourceRefs" :key="source"><code>{{source}}</code></li></ul>
        </details>
      </li>
    </ul>
    <el-pagination v-if="total>10" :total="total" :page-size="10" :current-page="page" :disabled="loading||saving" layout="prev,pager,next" @current-change="read"/>
    <el-dialog v-model="open" :title="title" append-to-body width="min(480px,94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
      <form @submit.prevent="send">
        <label v-if="intent?.mode==='review'">复核结果<select v-model="verdict"><option value="upheld">维持原快照</option><option value="recompute">按新材料重新计算</option></select></label>
        <label>{{intent?.mode==='review'?'复核理由':intent?.mode==='zero'?'零预算依据':'申诉理由'}}<textarea v-model="reason" rows="4" required minlength="5" maxlength="2000"/></label>
        <el-alert v-if="formError" type="error" :title="formError" :closable="false"/>
        <div class="actions"><el-button :disabled="saving" @click="open=false">取消</el-button><el-button native-type="submit" type="primary" :loading="saving" :disabled="reason.trim().length<5">确认提交</el-button></div>
      </form>
    </el-dialog>
  </section>
</template>

<style scoped>
.health-toolbar,.controls,.history-meta,.health-meta{display:flex;align-items:center;flex-wrap:wrap;gap:.5rem}
.health-toolbar{justify-content:space-between}.health-toolbar h3{margin:.4rem 0}.health-meta{color:#516069;font-size:.88rem}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(115px,1fr));gap:.65rem;margin:1rem 0}
.metrics div{border-left:2px solid #d7e2df;padding:.3rem .7rem}.metrics dt{font-size:.86rem;color:#516069}.metrics dd{margin:.25rem 0;font-weight:650;font-size:1.05rem}
.trace,.appeal{font-size:.88rem;overflow-wrap:anywhere;margin:.5rem 0}.trace summary{cursor:pointer;color:#176b56}.trace ul,.gaps{padding-inline-start:1.3rem}
.history{padding:0;list-style:none}.history>li{border-top:1px solid #dfe6e4;padding:.75rem 0}.history time{font-variant-numeric:tabular-nums}
.appeal{display:grid;grid-template-columns:5rem minmax(0,1fr);gap:.25rem}.appeal dd{margin:0}
</style>
