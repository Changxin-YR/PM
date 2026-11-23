<script setup lang="ts">
import {isRejectedWrite,isUnconfirmedWrite} from './api-error'
import {computed,onMounted,ref,watch} from 'vue'
import {Copy,RefreshCw,RotateCcw,Signature} from 'lucide-vue-next'
import ScopedEvidencePicker from './ScopedEvidencePicker.vue'
import RequirementExceptionReceipt, {type RequirementException,type RequirementExceptionItem} from './RequirementExceptionReceipt.vue'

type Gap=Omit<RequirementExceptionItem,'deviation'>&{desc:string;priority:string;status:string;acceptanceCriteria:string}
type State={revision:number;gaps:Gap[];gapHash?:string;gapsTruncated:boolean;current?:RequirementException;
  canSign:boolean;canRevoke:boolean;signerPersonId?:string;revocableExceptionId?:string}
type Attempt={url:string;body:string;revision:number;key:string;notice:string}
const props=defineProps<{projectId:string;stage:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/requirement-exceptions`
const state=ref<State|null>(null),loading=ref(false),saving=ref(false),readFailed=ref(false)
const error=ref(''),notice=ref(''),expires=ref(''),deviations=ref<Record<string,string>>({}),proofs=ref<string[]>([])
const accept=ref(false),revocation=ref(''),retry=ref<Attempt|null>(null)
let lastAttempt:Attempt|undefined
const gaps=computed(()=>state.value?.gaps??[])
const targets=computed(()=>gaps.value.map(row=>row.requirementId))
const locked=computed(()=>loading.value||saving.value||readFailed.value||!!retry.value)
const navigationLocked=computed(()=>saving.value||!!retry.value)
defineExpose({navigationLocked})
const clean=(s:string)=>s.trim().replace(/\s+/gu,' ').normalize('NFC')
function statement(value:string){const n=Array.from(value).length;return n>=5&&n<=1000&&/[\p{L}\p{N}]/u.test(value)&&!/\p{C}/u.test(value)}
const scope=computed(()=>{
  const current=state.value,time=Date.parse(expires.value)
  if(!current?.canSign||!current.gapHash||!current.signerPersonId||!Number.isFinite(time))return null
  const items=gaps.value.map(({requirementId,revision,baselineVersion,requirementHash})=>({requirementId,revision,baselineVersion,
    requirementHash,deviation:clean(deviations.value[requirementId]??'')})).sort((a,b)=>a.requirementId<b.requirementId?-1:1)
  if(!items.length||items.some(item=>!statement(item.deviation)))return null
  return {decision:'accept',gapHash:current.gapHash,expiresAt:new Date(time).toISOString(),items}
})
const written=computed(()=>{
  if(!scope.value)return ''
  const {gapHash,items,expiresAt}=scope.value
  return ['PMO DEC-09 SYNTHETIC REQUIREMENT EXCEPTION',
    'I explicitly accept only the listed requirement deviations for conditional closure.',
    `Project: ${props.projectId}`,`Customer: ${state.value?.signerPersonId}`,`Gap SHA-256: ${gapHash}`,
    `Expires at (UTC): ${expiresAt.replace(/(\.\d{3})Z$/,(_,fraction)=>`${fraction}000Z`)}`,
    ...items.flatMap(i=>[`Requirement: ${i.requirementId} revision ${i.revision} baseline ${i.baselineVersion} hash ${i.requirementHash}`,`Deviation: ${i.deviation}`]),
  ].join('\n')
})

async function load(){
  if(loading.value)return
  loading.value=true;error.value=''
  try{
    const {data}:{data:State}=await props.api(path)
    if(data.gapHash!==state.value?.gapHash){deviations.value={};proofs.value=[];accept.value=false}
    state.value=data;readFailed.value=false
  }catch(e){state.value=null;readFailed.value=true;error.value=e instanceof Error?e.message:'未能读取需求例外。'}
  finally{loading.value=false}
}
async function send(attempt:Attempt){
  if(saving.value||loading.value)return
  saving.value=true;error.value='';notice.value='';lastAttempt=attempt
  try{
    await props.api(attempt.url,{method:'POST',body:attempt.body,headers:{'If-Match':String(attempt.revision),'Idempotency-Key':attempt.key}})
    retry.value=null;lastAttempt=undefined;accept.value=false;revocation.value='';notice.value=attempt.notice
    await load()
  }catch(e){
    const ambiguous=isUnconfirmedWrite(e)
    retry.value=ambiguous?attempt:null
    if(isRejectedWrite(e)){lastAttempt=undefined;accept.value=false;await load()}
    error.value=ambiguous?'未能确认结果，请按原请求重试。':e instanceof Error?e.message:'操作失败。'
  }finally{saving.value=false}
}
function submit(revoke=false){
  if(locked.value||!state.value)return
  const revision=state.value.revision, id=state.value.revocableExceptionId
  const value=revoke?(state.value.canRevoke&&id&&statement(clean(revocation.value))?{reason:clean(revocation.value)}:null):
    (scope.value&&proofs.value.length===1&&accept.value?{...scope.value,evidenceRef:proofs.value[0]}:null)
  if(!value)return
  const url=revoke?`${path}/${id}/revoke`:path, body=JSON.stringify(value)
  const prior=lastAttempt
  void send(prior?.url===url&&prior.revision===revision&&prior.body===body?prior:
    {url,body,revision,key:crypto.randomUUID(),notice:revoke?'已撤销书面例外。':'已登记客户书面接受。'})
}
async function copy(){
  try{await navigator.clipboard.writeText(written.value);notice.value='已复制未签署文本。'}
  catch{error.value='无法访问剪贴板。'}
}
watch(()=>props.stage,()=>{if(!retry.value)void load()})
watch(written,()=>{accept.value=false})
onMounted(load)
</script>

<template>
  <section class="panel exceptions" v-loading="loading">
    <div class="exception-header">
      <h3>需求书面例外</h3>
      <el-button size="small" :disabled="saving||loading||!!retry" @click="load"><RefreshCw :size="14"/> 重读需求例外</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <p v-if="notice" role="status">{{notice}}</p>
    <el-button v-if="retry" :loading="saving" :disabled="loading" @click="send(retry)"><RotateCcw :size="14"/> 重试上次提交</el-button>
    <template v-if="state">
      <el-alert title="DEC-09 待真实业务签核" type="warning" :closable="false" show-icon/>
      <RequirementExceptionReceipt v-if="state.current" :receipt="state.current" title="当前有效的客户书面接受"/>
      <p v-else-if="!gaps.length" class="muted">没有可办理书面例外的需求缺口。</p>
      <p v-else-if="state.gapsTruncated" class="muted">缺口超过本次可签范围，不能登记不完整的例外。</p>
      <p v-else-if="!state.canSign" class="muted">当前无有效例外。</p>
      <form v-if="gaps.length&&!state.current" @submit.prevent="submit()">
        <div v-for="gap in gaps" :key="gap.requirementId" class="gap">
          <strong>{{gap.priority}} · {{gap.desc}}</strong>
          <p>{{gap.acceptanceCriteria}}</p>
          <code>{{gap.requirementId}} · 需求版本 {{gap.revision}} · 基线 v{{gap.baselineVersion}}</code>
          <label v-if="state.canSign">具体接受的偏差<textarea v-model="deviations[gap.requirementId]" rows="2" required minlength="5" maxlength="1000" :disabled="locked"/></label>
        </div>
        <template v-if="state.canSign">
          <label>接受有效截至（本地时间）<input v-model="expires" type="datetime-local" required :disabled="locked"/></label>
          <details v-if="written">
            <summary>未签署的偏差文本</summary>
            <pre>{{written}}</pre>
            <el-button size="small" :disabled="locked" @click="copy"><Copy :size="14"/> 复制未签署文本</el-button>
          </details>
          <ScopedEvidencePicker v-model="proofs" :project-id="projectId" resource-type="requirement" :resource-ids="targets"
            label="已核验的书面 PDF 原件" mime-type="application/pdf" :max-bytes="1048576" :disabled="locked" :api="api"/>
          <label class="check"><input v-model="accept" type="checkbox" required :disabled="locked"/> 我作为客户代表明确接受所列需求偏差。</label>
          <el-button native-type="submit" type="primary" :disabled="locked||!scope||!proofs.length||!accept"><Signature :size="14"/> 签署书面例外</el-button>
        </template>
      </form>
      <form v-if="state.canRevoke" @submit.prevent="submit(true)">
        <label>撤销原因<textarea v-model="revocation" required minlength="5" maxlength="1000" rows="2" :disabled="locked"/></label>
        <el-button native-type="submit" type="warning" plain :disabled="locked||!statement(clean(revocation))"><RotateCcw :size="14"/> 撤销书面例外</el-button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.exceptions{display:grid;gap:12px;overflow-wrap:anywhere}
.exception-header{display:flex;align-items:center;justify-content:space-between;gap:.5rem;flex-wrap:wrap}
h3,p{margin:.25rem 0}
form,.gap,label{display:grid;gap:.4rem;min-width:0}
form{gap:.7rem;justify-items:start}
form>label,.gap,fieldset,details{width:100%;box-sizing:border-box}
.gap{border-bottom:1px solid var(--el-border-color);padding:.55rem 0}

.check{display:flex;align-items:baseline;gap:.4rem}
.muted,code{color:var(--el-text-color-secondary)}
pre{white-space:pre-wrap;overflow-wrap:anywhere;border-left:2px solid var(--el-border-color);padding-left:.6rem}
summary{cursor:pointer}
</style>
