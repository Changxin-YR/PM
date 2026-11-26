<script lang="ts">
export const calibrationLabels={
  strategicAlignment:'战略对齐',customerAttractiveness:'客户/市场吸引力',
  financialResourceReturn:'财务/资源回报',riskControllability:'风险可控度',
} as const
export type CalibrationKey=keyof typeof calibrationLabels
export type Deviation={resultRevision:number;explanation:string;improvement:string}
type Status='evaluated'|'pending'|'incomparable'
type Expectation={expected:string;objective?:string;unit?:string;observeAfter:string;reviewerId:string;evidenceRefs:string[]}
type Result={status:Status;reasoning:string;score?:string;evidenceRefs:string[];followUpBy?:string;evidencePlan?:string;
  revision:number;reviewerId:string;evaluatedAt:string;followUpDeviation?:Deviation;actualAnchorCode?:string;closingDecisionRef?:string}
type Dimension={originalMean:string;current?:Result;history:Result[];difference?:string;requiresRetrospective:boolean;canEvaluate:boolean;
  anchors?:Record<string,string>;originalScores?:{score:number;round:number;anchor:string;scorecardRef:string;evidenceRefs:string[]}[]}
export type Calibration={projectId:string;planRevision:number;canPlan:boolean;plan?:{dimensions:Record<CalibrationKey,Expectation>};
  status:'historical_undefined'|'unplanned'|'planned'|'open'|'complete';anchorVersion?:string;dimensions:Partial<Record<CalibrationKey,Dimension>>;
  overestimated:CalibrationKey[]}
</script>

<script setup lang="ts">
import {computed,ref,watch} from 'vue'

type PlanDraft=Omit<Expectation,'evidenceRefs'> & {refs:string}

const props=defineProps<{projectId:string;stage:string;api:(path:string,options?:RequestInit)=>Promise<any>;revision?:number}>()
const emit=defineEmits<{loaded:[state:Calibration];saved:[]}>()
const keys=Object.keys(calibrationLabels) as CalibrationKey[]
const outcomes={evaluated:'已评价',pending:'待观察',incomparable:'证据不足或不可比'}
const anchorCodes=['score_1','between_1_3','score_3','between_3_5','score_5']
const state=ref<Calibration|null>(null),editing=ref<'plan'|CalibrationKey|null>(null),busy=ref(false),error=ref(''),notice=ref('')
const plan=ref<Partial<Record<CalibrationKey,PlanDraft>>>({})
const members=ref<{personId:string;name:string;role:string}[]>([])
const result=ref({status:'evaluated' as Status,reasoning:'',score:'',refs:'',followUpBy:'',evidencePlan:'',explanation:'',improvement:''})
const path=computed(()=>`projects/${props.projectId}/review-calibration`)
const show=computed(()=>state.value?.canPlan||state.value?.plan||state.value?.status==='historical_undefined'||error.value)
let readId=0,attempt:{signature:string;key:string}|undefined
const list=(value:string)=>value.trim().toLowerCase().split(/\s+/u).filter(Boolean)
const errorMessage=(value:unknown)=>value instanceof Error?value.message:'未能读取评分结果校准。'
const selectedAnchorCode=computed(()=>{
  const key=editing.value,score=Number(result.value.score)
  if(!key||key==='plan'||!Number.isInteger(score)||score<1||score>5)return undefined
  const code=anchorCodes[score-1]!
  return state.value?.dimensions[key]?.anchors?.[code]?code:undefined
})

async function read(){
  const id=++readId
  try{
    const {data}:{data:Calibration}=await props.api(path.value)
    if(id===readId){state.value=data;emit('loaded',data)}
  }catch(failure){if(id===readId)error.value=errorMessage(failure)}
}
watch(()=>[props.projectId,props.revision],()=>{editing.value=null;error.value='';notice.value='';void read()},{immediate:true})

async function openPlan(){
  if(!state.value?.canPlan)return
  error.value='';notice.value='';attempt=undefined
  plan.value=Object.fromEntries(keys.map(key=>{
    const existing=state.value?.plan?.dimensions[key]
    return [key,existing?{...existing,refs:existing.evidenceRefs.join('\n')}:
      {expected:'',objective:'',unit:'',observeAfter:'',reviewerId:'',refs:''}]
  }))
  editing.value='plan'
  try{
    const {data}=await props.api(`projects/${props.projectId}/members?pageSize=100`)
    const items:{personId:string;name:string;role:string}[]=data.items
    const conflicted=new Set(items.filter(member=>['pm','sponsor','initiator'].includes(member.role)).map(member=>member.personId))
    members.value=items.filter(member=>member.role==='reviewer'&&!conflicted.has(member.personId))
  }catch(failure){error.value=errorMessage(failure)}
}
function openResult(key:CalibrationKey){
  const row=state.value?.dimensions[key]
  if(!row?.canEvaluate)return
  const current=row.current
  editing.value=key;error.value='';notice.value='';attempt=undefined
  result.value={status:current?.status??'evaluated',reasoning:current?.reasoning??'',score:current?.score??'',
    refs:current?.evidenceRefs.join('\n')??'',followUpBy:current?.followUpBy??'',evidencePlan:current?.evidencePlan??'',
    explanation:'',improvement:''}
}

async function save(){
  if(!state.value||!editing.value||busy.value)return
  const mode=editing.value,current=state.value
  let endpoint:string,revision:number,payload:object
  if(mode==='plan'){
    endpoint=`${path.value}/plan`;revision=current.planRevision
    payload={dimensions:Object.fromEntries(keys.map(key=>{
      const line=plan.value[key]!
      return [key,{expected:line.expected.trim(),observeAfter:line.observeAfter,reviewerId:line.reviewerId,evidenceRefs:list(line.refs),
        ...(line.objective?.trim()?{objective:line.objective.trim()}:{}),...(line.unit?.trim()?{unit:line.unit.trim()}:{})}]
    }))}
  }else{
    endpoint=`${path.value}/results/${mode}`;revision=current.dimensions[mode]?.current?.revision??1
    const {status,reasoning,refs,score,followUpBy,evidencePlan,explanation,improvement}=result.value
    payload={status,reasoning:reasoning.trim(),evidenceRefs:list(refs),
      ...(status==='evaluated'?{score:score.trim(),actualAnchorCode:selectedAnchorCode.value}:
        status==='pending'?{followUpBy,evidencePlan:evidencePlan.trim()}:{}),
      ...(props.stage==='archived'&&status==='evaluated'&&(explanation.trim()||improvement.trim())?
        {followUpDeviation:{resultRevision:revision+1,explanation:explanation.trim(),improvement:improvement.trim()}}:{})}
  }
  const body=JSON.stringify(payload),signature=JSON.stringify([endpoint,revision,body])
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  busy.value=true;error.value='';notice.value=''
  try{
    const {data}:{data:Calibration}=await props.api(endpoint,{method:'PUT',body,
      headers:{'If-Match':String(revision),'Idempotency-Key':attempt.key}})
    state.value=data;editing.value=null;attempt=undefined
    notice.value=mode==='plan'?'立项预期已保存。':'评分结果已记录。'
    emit('loaded',data);emit('saved')
  }catch(failure){error.value=errorMessage(failure)}
  finally{busy.value=false}
}
</script>

<template>
  <section v-if="show" class="calibration" aria-label="立项评分结果校准">
    <div class="toolbar">
      <h4>立项评分结果校准</h4>
      <el-button v-if="state?.canPlan&&!editing" text @click="openPlan">{{state.plan?'编辑立项预期':'登记立项预期'}}</el-button>
      <el-button v-if="!editing" text :disabled="busy" @click="read">刷新校准</el-button>
    </div>
    <p v-if="state?.status==='historical_undefined'" class="meta">历史立项未登记评分预期。</p>
    <p v-if="state?.status==='open'" class="meta">仍有维度待观察、待评价或不可比。</p>
    <p v-if="state?.anchorVersion" class="meta">立项锚点版本 <code>{{state.anchorVersion}}</code></p>
    <p v-if="state?.status==='complete'" class="meta">四个维度均已完成结果评价。</p>
    <p v-if="state?.overestimated.length" class="meta">原分高于结果至少 1.00 分：{{state.overestimated.map(key=>calibrationLabels[key]).join('、')}}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <p v-if="notice" role="status">{{notice}}</p>
    <form v-if="editing==='plan'" @submit.prevent="save">
      <fieldset v-for="key in keys" :key="key" :disabled="busy">
        <legend>{{calibrationLabels[key]}}</legend>
        <template v-if="plan[key]">
          <label>预期及评估依据<textarea v-model="plan[key].expected" rows="2" maxlength="6000" required/></label>
          <div class="calibration-grid">
            <label>目标（适用时）<input v-model="plan[key].objective" maxlength="6000"/></label>
            <label>单位（适用时）<input v-model="plan[key].unit" maxlength="60"/></label>
            <label>观察日期<input v-model="plan[key].observeAfter" type="date" required/></label>
            <label>评价责任人<select v-model="plan[key].reviewerId" required>
              <option value="" disabled>选择独立评审人</option>
              <option v-for="member in members" :key="member.personId" :value="member.personId">{{member.name}}</option>
            </select></label>
          </div>
          <label>补充已核验附件引用<textarea v-model="plan[key].refs" rows="2" placeholder="每行填写一个 evidence:… 引用"/></label>
        </template>
      </fieldset>
      <div class="toolbar"><el-button :disabled="busy" @click="editing=null">取消</el-button>
        <el-button native-type="submit" type="primary" :loading="busy">保存立项预期</el-button></div>
    </form>
    <template v-else-if="state?.plan">
      <div v-if="state.anchorVersion" class="calibration-comparison">
        <table aria-label="立项评分与实际结果对照">
          <thead><tr><th scope="col">维度</th><th scope="col" class="score-cell">原立项分</th>
            <th scope="col" class="score-cell">实际评价</th><th scope="col" class="score-cell">差值</th><th scope="col">状态</th></tr></thead>
          <tbody><tr v-for="key in keys" :key="key">
            <th scope="row">{{calibrationLabels[key]}}</th>
            <td class="score-cell">{{state.dimensions[key]?.originalMean??'—'}}</td>
            <td class="score-cell">{{state.dimensions[key]?.current?.score??'—'}}</td>
            <td class="score-cell">{{state.dimensions[key]?.difference??'—'}}</td>
            <td v-if="state.dimensions[key]?.requiresRetrospective"><span class="deviation">需在复盘解释</span></td>
            <td v-else>{{state.dimensions[key]?.current?outcomes[state.dimensions[key]!.current!.status]:'待评价'}}</td>
          </tr></tbody>
        </table>
      </div>
      <details v-for="key in keys" :key="key" class="calibration-dimension">
        <summary>
          {{calibrationLabels[key]}} · {{state.anchorVersion?'评价与证据':'立项预期'}}
        </summary>
        <dl>
          <dt>立项预期</dt><dd>{{state.plan.dimensions[key].expected}}</dd>
          <template v-if="state.plan.dimensions[key].objective"><dt>目标</dt><dd>{{state.plan.dimensions[key].objective}} {{state.plan.dimensions[key].unit}}</dd></template>
          <dt>观察日期</dt><dd>{{state.plan.dimensions[key].observeAfter}}</dd>
          <dt>评价责任人</dt><dd>{{state.plan.dimensions[key].reviewerId}}</dd>
          <template v-if="state.dimensions[key]"><dt>原评分</dt><dd>{{state.dimensions[key]?.originalMean}} 分</dd></template>
          <template v-if="state.dimensions[key]?.difference!==undefined">
            <dt>结果与原评分差值</dt><dd>{{state.dimensions[key]?.difference}} 分</dd>
          </template>
        </dl>
        <details v-if="state.dimensions[key]?.originalScores?.length">
          <summary>原始评分与证据</summary>
          <dl v-for="rating in state.dimensions[key]?.originalScores" :key="rating.scorecardRef">
            <dt>第 {{rating.round}} 轮 · {{rating.score}} 分</dt>
            <dd>{{state.dimensions[key]?.anchors?.[rating.anchor]??rating.anchor}}</dd>
            <dd><code>{{rating.scorecardRef}}</code></dd>
            <dd v-for="source in rating.evidenceRefs" :key="source"><code>{{source}}</code></dd>
          </dl>
        </details>
        <details v-for="entry in state.dimensions[key]?.history" :key="entry.revision">
          <summary>版本 {{entry.revision}} · {{outcomes[entry.status]}} · {{entry.score??entry.followUpBy??''}}</summary>
          <p>{{entry.reasoning}}</p>
          <p v-if="entry.actualAnchorCode">实际锚点 {{state.dimensions[key]?.anchors?.[entry.actualAnchorCode]??entry.actualAnchorCode}}</p>
          <p v-if="entry.evidencePlan">{{entry.evidencePlan}}</p>
          <p v-if="entry.followUpDeviation">{{entry.followUpDeviation.explanation}} · {{entry.followUpDeviation.improvement}}</p>
          <p class="meta">{{entry.reviewerId}} · {{entry.evaluatedAt}}</p>
          <p v-if="entry.closingDecisionRef">结项裁决 <code>{{entry.closingDecisionRef}}</code></p>
          <code v-for="source in entry.evidenceRefs" :key="source">{{source}}</code>
        </details>
        <el-button v-if="state.dimensions[key]?.canEvaluate&&!editing" text @click="openResult(key)">登记结果</el-button>
        <form v-if="editing===key" @submit.prevent="save">
          <fieldset :disabled="busy">
            <label>结果状态<select v-model="result.status">
              <option v-for="(label,value) in outcomes" :key="value" :value="value">{{label}}</option>
            </select></label>
            <label>评价说明<textarea v-model="result.reasoning" rows="2" maxlength="6000" required/></label>
            <label v-if="result.status==='evaluated'">结果评分<input v-model="result.score" inputmode="decimal" pattern="[1-4](\.[0-9]{1,2})?|5(\.0{1,2})?" required/></label>
            <p v-if="result.status==='evaluated'&&selectedAnchorCode" class="meta">
              冻结锚点 {{state.dimensions[key]?.anchors?.[selectedAnchorCode]}}
            </p>
            <label>已核验附件引用<textarea v-model="result.refs" rows="2" :required="result.status==='evaluated'" placeholder="每行填写一个 evidence:… 引用"/></label>
            <template v-if="result.status==='pending'">
              <label>跟进期限<input v-model="result.followUpBy" type="date" required/></label>
              <label>证据计划<textarea v-model="result.evidencePlan" rows="2" maxlength="6000" required/></label>
            </template>
            <template v-if="stage==='archived'&&result.status==='evaluated'">
              <label>偏差解释（绝对差值达到 1.00 分时必填）<textarea v-model="result.explanation" rows="2"/></label>
              <label>改进措施（绝对差值达到 1.00 分时必填）<textarea v-model="result.improvement" rows="2"/></label>
            </template>
          </fieldset>
          <div class="toolbar"><el-button :disabled="busy" @click="editing=null">取消</el-button>
            <el-button native-type="submit" type="primary" :loading="busy">保存结果</el-button></div>
        </form>
      </details>
    </template>
  </section>
</template>

<style scoped>
.calibration{border:1px solid var(--pmo-line);border-radius:8px;padding:12px;margin:12px 0;overflow-wrap:anywhere}
h4{margin:0}.calibration-dimension,fieldset{margin:8px 0;padding:8px;border:1px solid var(--pmo-line);border-radius:6px}
fieldset,form,.calibration-grid{display:grid;gap:8px}
.calibration-grid{grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.calibration-comparison{max-width:100%;overflow:auto;margin:12px 0}
.calibration-comparison table{width:100%;min-width:490px;border-collapse:collapse}
.calibration-comparison tr{border-bottom:1px solid var(--pmo-line)}
.calibration-comparison th,.calibration-comparison td{text-align:left;padding:9px 10px}
.calibration-comparison thead{background:var(--pmo-bg);color:var(--el-text-color-secondary)}
.calibration-comparison tbody th{font-weight:400}
.calibration-comparison .score-cell{text-align:right;font-variant-numeric:tabular-nums}
.deviation{color:var(--el-color-warning-dark-2)}
label{display:grid;gap:4px}
input,select,textarea{width:100%;box-sizing:border-box;border:1px solid var(--pmo-line);border-radius:6px;padding:8px;color:inherit;background:var(--pmo-surface,#fff);font:inherit}
dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px}dd{margin:0}
code{display:block;margin:3px 0}
</style>
