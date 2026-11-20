<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {Download,FileCheck2,RefreshCw} from 'lucide-vue-next'
import ScopedEvidencePicker from './ScopedEvidencePicker.vue'

type Kind='money'|'effort'
type Attribution='estimate'|'execution'|'scope'
type Variance={budget?:string|null;difference?:string|null;rate?:string|null;sourceRefs:string[]}
type Proof={evidenceRef:string;linkedResourceType:'project';linkedResourceId:string;linkRevision:number;
  sha256:string;verifiedBy:string;verifiedAt:string}
type Line={type:Kind;unit:'CNY'|'person_day';actual:string;sourceRef:string;sourceNetActual:string;difference:string;
  forecastRemaining:string;forecastSourceRefs:string[];initial:Variance;current:Variance;proof:Proof;overrunReason?:Attribution|null}
type Receipt={receiptId:string;projectId:string;revision:number;signedBy:string;signedAt:string;grantId:string;
  coveredThrough:string;ruleVersion:string;costHash:string;receiptHash:string;lines:Line[];policyStatus:'POLICY_PENDING'}
type State={projectId:string;revision:number;costHash:string;canCreate:boolean;current:Receipt|null;policyStatus:'POLICY_PENDING'}

const props=defineProps<{projectId:string;api:(path:string,options?:RequestInit)=>Promise<any>;costsBusy:boolean}>()
const path=`projects/${props.projectId}/cost-reconciliations`
const kinds:Kind[]=['money','effort'],labels={money:'金额（元）',effort:'人天'}
const attributions:Record<Attribution,string>={estimate:'估算',execution:'执行',scope:'范围'}
const baselines=[{key:'initial',label:'初始基线'},{key:'current',label:'当前基线'}] as const
const state=ref<State|null>(null),loading=ref(false),saving=ref(false),open=ref(false)
const signingBasis=ref<Pick<State,'revision'|'costHash'>|null>(null)
const error=ref(''),formError=ref(''),notice=ref(''),evidenceRefs=ref<string[]>([])
const reasons=ref<Record<Kind,Attribution|''>>({money:'',effort:''})
const disabled=computed(()=>loading.value||saving.value||props.costsBusy)
const current=computed(()=>state.value?.current)
const basisChanged=computed(()=>!!state.value&&!!signingBasis.value&&
  (state.value.revision!==signingBasis.value.revision||state.value.costHash!==signingBasis.value.costHash))
const signDisabled=computed(()=>disabled.value||basisChanged.value||state.value?.canCreate!==true)
let attempt:{signature:string;key:string}|undefined

function time(iso:string){return new Date(iso).toLocaleString('zh-CN',{hour12:false})}
function percent(rate?:string|null){
  if(rate==null)return '—'
  const [whole,fraction='']=rate.replace(/^-/,'').split('.')
  const digits=whole+fraction.padEnd(2,'0'),cut=whole.length+2,tail=digits.slice(cut).replace(/0+$/,'')
  return `${rate.startsWith('-')?'-':''}${digits.slice(0,cut).replace(/^0+(?=\d)/,'')}${tail?'.'+tail:''}%`
}
function message(e:unknown){return e instanceof Error?e.message:'未能读取对账结果。'}
async function read(){
  const {data}:{data:State}=await props.api(path)
  state.value=data
}
async function refresh(){
  if(loading.value||saving.value)return
  loading.value=true;error.value=''
  try{await read()}catch(e){state.value=null;error.value=message(e)}finally{loading.value=false}
}
watch(()=>props.costsBusy,value=>{if(!value)void refresh()},{immediate:true})

function begin(){
  const basis=state.value
  if(disabled.value||basis?.canCreate!==true)return
  signingBasis.value={revision:basis.revision,costHash:basis.costHash}
  evidenceRefs.value=[];reasons.value={money:'',effort:''};formError.value='';notice.value='';attempt=undefined
  open.value=true
}
async function save(){
  const basis=signingBasis.value
  if(signDisabled.value||!basis||!evidenceRefs.value.length||evidenceRefs.value.length>2)return
  const overrunReasons:Partial<Record<Kind,Attribution>>={}
  for(const kind of kinds){const value=reasons.value[kind];if(value)overrunReasons[kind]=value}
  const body=JSON.stringify({costHash:basis.costHash,evidenceRefs:[...evidenceRefs.value].sort(),overrunReasons})
  const signature=`${basis.revision}:${body}`
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  saving.value=true;formError.value='';error.value=''
  let saved=false
  try{
    await props.api(path,{method:'POST',headers:{'If-Match':String(basis.revision),'Idempotency-Key':attempt.key},body})
    saved=true;attempt=undefined;open.value=false;notice.value='已签署成本对账。'
    await read()
  }catch(e){
    if(saved){state.value=null;error.value='对账已签署，但未能重读当前回执。'}
    else formError.value=message(e)
  }finally{saving.value=false}
}
</script>

<template>
  <section class="reconciliation" aria-label="成本对账" :aria-busy="loading">
    <div class="toolbar reconcile-toolbar">
      <h3>成本对账</h3>
      <el-tag v-if="state?.policyStatus==='POLICY_PENDING'" size="small" type="warning">策略待签核</el-tag>
      <div class="controls">
        <el-button v-if="state?.canCreate===true" type="primary" :disabled="disabled" @click="begin"><FileCheck2 :size="15"/> 签署对账</el-button>
        <el-button aria-label="刷新成本对账" title="刷新成本对账" :disabled="disabled" @click="refresh"><RefreshCw :size="15"/></el-button>
      </div>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
    <p v-if="notice" role="status" class="signed-note">{{notice}}</p>
    <p v-if="loading" class="meta">正在读取成本对账…</p>
    <p v-else-if="state&&!current" class="meta">暂无与当前成本相符的签署回执。</p>
    <template v-if="current">
      <p class="meta">回执版本 {{current.revision}} · 截至 <time :datetime="current.coveredThrough">{{time(current.coveredThrough)}}</time></p>
      <p class="meta">签署人 {{current.signedBy}} · <time :datetime="current.signedAt">{{time(current.signedAt)}}</time></p>
      <div class="measure-grid">
        <section v-for="line in current.lines" :key="line.type" :aria-label="`${labels[line.type]}对账结果`" class="measure">
          <h4>{{labels[line.type]}}</h4>
          <dl>
            <dt>系统核验净实际</dt><dd>{{line.actual}}</dd>
            <dt>独立来源净实际</dt><dd>{{line.sourceNetActual}}</dd>
            <dt>对账差额</dt><dd>{{line.difference}}</dd>
            <dt>剩余预测</dt><dd>{{line.forecastRemaining}}</dd>
            <template v-if="line.overrunReason"><dt>超支归因</dt><dd>{{attributions[line.overrunReason]}}</dd></template>
          </dl>
          <details class="sources">
            <summary>独立对账凭据与预测来源</summary>
            <dl>
              <dt>外部来源</dt><dd>{{line.sourceRef}}</dd>
              <dt>核验附件</dt><dd><a :href="`/api/v1/evidence/${line.proof.evidenceRef.slice(9)}?download=true`" download><Download :size="14"/><code>{{line.proof.evidenceRef}}</code></a></dd>
              <dt>附件版本</dt><dd>{{line.proof.linkRevision}}</dd>
              <dt>核验人</dt><dd>{{line.proof.verifiedBy}}</dd>
              <dt>核验时间</dt><dd><time :datetime="line.proof.verifiedAt">{{time(line.proof.verifiedAt)}}</time></dd>
              <dt>SHA-256</dt><dd><code>{{line.proof.sha256}}</code></dd>
              <dt>预测来源</dt><dd><code v-for="source in line.forecastSourceRefs" :key="source" class="source">{{source}}</code><span v-if="!line.forecastSourceRefs.length">—</span></dd>
            </dl>
          </details>
          <div v-for="base in baselines" :key="base.key" class="variance">
            <h5>{{base.label}}偏差</h5>
            <dl>
              <dt>预算</dt><dd>{{line[base.key].budget??'—'}}</dd>
              <dt>实际 − 预算</dt><dd>{{line[base.key].difference??'—'}}</dd>
              <dt>偏差率</dt><dd>{{percent(line[base.key].rate)}}</dd>
            </dl>
            <details v-if="line[base.key].sourceRefs.length" class="sources">
              <summary>{{base.label}}来源</summary>
              <code v-for="source in line[base.key].sourceRefs" :key="source" class="source">{{source}}</code>
            </details>
          </div>
        </section>
      </div>
      <details class="sources trace">
        <summary>签署回执版本与标识</summary>
        <dl><dt>回执编号</dt><dd>{{current.receiptId}}</dd><dt>授权编号</dt><dd>{{current.grantId}}</dd>
          <dt>规则版本</dt><dd>{{current.ruleVersion}}</dd><dt>成本快照</dt><dd><code>{{current.costHash}}</code></dd>
          <dt>回执哈希</dt><dd><code>{{current.receiptHash}}</code></dd>
        </dl>
      </details>
    </template>
  </section>
  <el-dialog v-model="open" append-to-body destroy-on-close title="签署成本对账" width="min(560px,94vw)"
    :close-on-click-modal="false" :show-close="!saving" :close-on-press-escape="!saving">
    <form class="sign-form" @submit.prevent="save">
      <ScopedEvidencePicker v-model="evidenceRefs" :project-id="projectId" resource-type="project" :resource-id="projectId"
        label="已核验成本来源 JSON" mime-type="application/json" :max-bytes="16*1024" :limit="2" :api="api" :disabled="signDisabled"/>
      <fieldset class="reason-fields" :disabled="signDisabled">
        <legend>超支归因（超出当前预算时必填）</legend>
        <label v-for="kind in kinds" :key="kind">{{labels[kind]}}
          <select v-model="reasons[kind]" :aria-label="`${labels[kind]}超支归因`">
            <option value="">未填写</option><option v-for="(label,value) in attributions" :key="value" :value="value">{{label}}</option>
          </select>
        </label>
      </fieldset>
      <el-alert v-if="basisChanged" type="warning" title="成本快照或版本已变化，请关闭后重新发起签署。" :closable="false" show-icon/>
      <el-alert v-else-if="formError" type="error" :title="formError" :closable="false" show-icon/>
      <div class="actions">
        <el-button :disabled="saving" @click="open=false">取消</el-button>
        <el-button type="primary" native-type="submit" :disabled="signDisabled||!evidenceRefs.length" :loading="saving"><FileCheck2 :size="15"/>确认签署对账</el-button>
      </div>
    </form>
  </el-dialog>
</template>

<style scoped>
.reconciliation{margin-top:1rem;overflow-wrap:anywhere}
.reconcile-toolbar,.controls{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap}
.reconcile-toolbar h3,.measure h4,.variance h5{margin:0}
.controls{margin-left:auto}
.meta,.sources{font-size:13px;color:var(--el-text-color-secondary)}
.reconciliation p{margin:.3rem 0}
.signed-note{color:var(--el-color-primary)}
.measure-grid,.reason-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,14rem),1fr));gap:.75rem}
.measure,.variance,.trace{border-top:1px solid var(--el-border-color);margin-top:.7rem;padding-top:.5rem}
.reconciliation dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.3rem .6rem;margin:.5rem 0}
.reconciliation dd{margin:0;font-variant-numeric:tabular-nums;text-align:right;overflow-wrap:anywhere}
.reconciliation a{color:var(--el-color-primary);display:inline-flex;align-items:center;gap:.3rem;max-width:100%}
.reconciliation a svg{flex:none}
.sources summary{cursor:pointer}
.source{display:block}
.sign-form{gap:.75rem}
.reason-fields{min-width:0;border:0;padding:0;margin:0}
.reason-fields legend{padding:0;font-weight:600;margin-bottom:.35rem}
.reason-fields select{width:100%;border:1px solid var(--el-border-color);border-radius:var(--el-border-radius-base);color:inherit;background:var(--el-bg-color);font:inherit;padding:.4rem}
</style>
