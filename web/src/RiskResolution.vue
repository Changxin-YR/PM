<script setup lang="ts">
import {isRejectedWrite,isUnconfirmedWrite} from './api-error'
import {computed,nextTick,ref,watch} from 'vue'
import {Check,Copy,Download,FileCheck2,RefreshCw,RotateCcw,Send,ShieldCheck} from 'lucide-vue-next'
import ScopedEvidencePicker from './ScopedEvidencePicker.vue'

type Risk={riskId:string;projectId:string;ownerId?:string|null;statement:string;prob:number;impact:number;response:string;
  isBlocking:boolean;blockedSince?:string|null;status:string;revision:number;canEdit?:boolean|null}
type AcceptanceKind='requested'|'receiver_confirmed'|'reviewer_verified'
type AcceptanceReceipt={acceptanceId:string;projectId?:string;riskId:string;riskRevision:number;receiverPersonId:string;actorPersonId?:string;
  acceptanceScope:string;acceptanceEvidenceRef:string;evidenceSha256:string;evidenceRevision:number;kind:AcceptanceKind;createdAt:string}
type Acceptance=AcceptanceReceipt & {status:'pending'|'accepted';canConfirm:boolean;canHandoff:boolean}
type Resolution=Risk & {resolutionId:string;reviewerPersonId:string;receiverPersonId?:string|null;acceptanceId?:string|null;
  evidenceRef:string;evidenceSha256:string;evidenceRevision:number;closedAt?:string|null}
type State={status:'unresolved'|'pending'|'accepted'|'resolved';acceptances:Acceptance[];acceptancesTruncated:boolean;
  canClose:boolean;canRequestAcceptance:boolean;canVerifyAcceptance:boolean} &
  ({audience:'project';risk:Risk;resolution?:Resolution}|{audience:'receiver';risk:Pick<Risk,'riskId'|'revision'>})
type Action='close'|'request'|'confirm'|'verify'|'handoff'
type Attempt={mode:Action;endpoint:string;revision:number;body:string;key:string;receiverOnly:boolean}

const props=defineProps<{riskId:string;acceptanceId?:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const open=defineModel<boolean>({required:true})
const emit=defineEmits<{saved:[]}>()
const path=`risks/${props.riskId}`
const uuid=/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i
const unavailable='没有可确认的接收请求。'
const riskStatuses:Record<string,string>={open:'未关闭',mitigated:'已缓解',closed:'已关闭',transferred:'已移交'}
const receiptKinds:Record<AcceptanceKind,string>={requested:'接收请求已登记',receiver_confirmed:'接收人已认证接收',reviewer_verified:'独立接收证据已核验'}
const actions={
  close:{label:'复核关闭',submit:'签署关闭',success:'已签署风险关闭。',icon:ShieldCheck},
  request:{label:'发出接收请求',submit:'提交接收请求',success:'接收请求已登记，尚未确认接收。',icon:Send},
  confirm:{label:'本人认证接收',submit:'确认本人接收',success:'已记录接收人认证回执。',icon:Check},
  verify:{label:'核验外部接收证据',submit:'签署证据核验',success:'已记录独立接收证据核验。',icon:FileCheck2},
  handoff:{label:'复核签署移交',submit:'确认签署移交',success:'已签署风险移交。',icon:ShieldCheck},
}
const state=ref<State|null>(null),loading=ref(false),saving=ref(false),error=ref(''),formError=ref(''),notice=ref('')
const confirmedHere=ref(false),issued=ref<AcceptanceReceipt|null>(null),copyNotice=ref('')
const action=ref<Action|null>(null),receiptId=ref(''),form=ref<HTMLFormElement|null>(null)
const evidenceRefs=ref<string[]>([]),receiver=ref(''),scope=ref(''),acceptedAt=ref(''),identity=ref(''),excerpt=ref('')
const busy=computed(()=>loading.value||saving.value)
const retry=ref<Attempt|null>(null)
const locked=computed(()=>busy.value||!!retry.value)
const incoming=computed(()=>props.acceptanceId!==undefined)
const projectView=computed(()=>!incoming.value&&state.value?.audience==='project'?state.value:null)
const acceptances=computed(()=>state.value?.acceptances.filter(item=>!incoming.value||canConfirm(item))??[])
const receipt=computed(()=>acceptances.value.find(item=>item.acceptanceId===receiptId.value))
const externalClause=computed(()=>{
  const risk=projectView.value?.risk
  if(action.value!=='verify'||!risk||!uuid.test(receiver.value)||!validText(scope.value,5,3000))return ''
  return `I explicitly accept risk ${risk.riskId} revision ${risk.revision} as receiver ${receiver.value.toLowerCase()} for scope: ${scope.value.trim().replace(/\s+/g,' ')}`
})

function time(iso:string){return new Date(iso).toLocaleString('zh-CN',{hour12:false})}
function message(e:unknown,fallback:string){return e instanceof Error?e.message:fallback}
function validText(value:string,min:number,max:number){const text=value.trim(),length=Array.from(text).length;return !text.includes('\0')&&length>=min&&length<=max}
function requestCode(item:AcceptanceReceipt){return `risk-request:${item.riskId}:${item.acceptanceId}`}
async function copy(item:AcceptanceReceipt){
  try{await navigator.clipboard.writeText(requestCode(item));copyNotice.value='已复制接收请求编号。'}
  catch{copyNotice.value='未能复制编号。'}
}
function canConfirm(item?:Acceptance,revision=state.value?.risk.revision){return item?.canConfirm===true&&
  item.status==='pending'&&item.kind==='requested'&&item.riskId===props.riskId&&item.riskRevision===revision&&
  (!incoming.value||item.acceptanceId===props.acceptanceId)}
function canHandoff(item?:Acceptance){return item?.canHandoff===true&&item.status==='accepted'&&
  (item.kind==='receiver_confirmed'||item.kind==='reviewer_verified')&&item.riskRevision===projectView.value?.risk.revision}
function allowed(mode:Action,item?:Acceptance){
  switch(mode){
    case 'close':return projectView.value?.canClose===true
    case 'request':return projectView.value?.canRequestAcceptance===true
    case 'verify':return projectView.value?.canVerifyAcceptance===true
    case 'confirm':return canConfirm(item)
    case 'handoff':return canHandoff(item)
  }
}
function reset(){action.value=null;receiptId.value='';formError.value='';retry.value=null}
async function read(){
  const {data}:{data:State}=await props.api(`${path}/resolution`)
  if(incoming.value&&(data.risk.riskId!==props.riskId||!data.acceptances.some(item=>canConfirm(item,data.risk.revision))))throw Error(unavailable)
  const changed=state.value?.risk.revision!==data.risk.revision
  state.value=data
  if(action.value&&(changed||!allowed(action.value,receipt.value)))reset()
}
async function refresh(){
  if(locked.value)return
  loading.value=true;error.value='';issued.value=null;copyNotice.value=''
  try{await read()}catch(e){
    state.value=null;reset()
    error.value=incoming.value?(e instanceof TypeError?'无法连接服务，请检查网络。':unavailable):message(e,'未能读取风险处置。')
  }finally{loading.value=false}
}
watch(open,value=>{if(value){reset();confirmedHere.value=false;notice.value='';void refresh()}},{immediate:true})

function begin(mode:Action,item?:Acceptance){
  if(locked.value||!allowed(mode,item))return
  reset();notice.value='';issued.value=null;copyNotice.value='';evidenceRefs.value=[];receiver.value='';scope.value='';acceptedAt.value='';identity.value='';excerpt.value=''
  receiptId.value=item?.acceptanceId??'';action.value=mode
  void nextTick(()=>form.value?.scrollIntoView({block:'nearest'}))
}

const command=computed(()=>{
  const mode=action.value,item=receipt.value,revision=state.value?.risk.revision
  if(!mode||revision===undefined||!allowed(mode,item))return null
  const [evidenceRef]=evidenceRefs.value
  if(mode==='close')return evidenceRef?{endpoint:'close',revision,payload:{resolutionEvidenceRef:evidenceRef}}:null
  if(mode==='confirm'||mode==='handoff'){
    if(!item)return null
    const binding={receiverPersonId:item.receiverPersonId,acceptanceEvidenceRef:item.acceptanceEvidenceRef}
    return mode==='handoff'?{endpoint:'handoff',revision,payload:binding}:{endpoint:'handoff/acceptance',revision,
      payload:{...binding,kind:'confirm',decision:'accept',acceptanceScope:item.acceptanceScope}}
  }
  if(!evidenceRef||!uuid.test(receiver.value)||!validText(scope.value,5,3000))return null
  const binding={receiverPersonId:receiver.value,acceptanceEvidenceRef:evidenceRef,acceptanceScope:scope.value.trim()}
  if(mode==='request')return {endpoint:'handoff/acceptance',revision,payload:{...binding,kind:'request'}}
  if(!Number.isFinite(Date.parse(acceptedAt.value))||!validText(identity.value,12,1000)||!validText(excerpt.value,12,4000))return null
  return {endpoint:'handoff/acceptance',revision,payload:{...binding,kind:'verify',decision:'accept',acceptedAt:new Date(acceptedAt.value).toISOString(),
    identityVerification:identity.value.trim(),acceptanceExcerpt:excerpt.value.trim()}}
})

function save(){
  const mode=action.value,write=command.value
  if(locked.value||!mode||!write)return
  const receiverOnly=incoming.value||state.value?.audience==='receiver'
  const {endpoint,revision,payload}=write
  void send({mode,endpoint,revision,body:JSON.stringify(payload),key:crypto.randomUUID(),receiverOnly})
}
async function send(attempt:Attempt){
  if(busy.value)return
  const {mode,endpoint,revision,body,key,receiverOnly}=attempt
  saving.value=true;formError.value='';error.value=''
  let saved=false
  try{
    const {data}:{data:AcceptanceReceipt|Resolution}=await props.api(`${path}/${endpoint}`,{method:'POST',headers:{'If-Match':String(revision),'Idempotency-Key':key},body})
    saved=true;reset();notice.value=actions[mode].success
    if(receiverOnly){state.value=null;confirmedHere.value=true;return}
    if(mode==='request'&&'kind' in data&&data.kind==='requested'&&data.riskId===props.riskId&&uuid.test(data.acceptanceId))issued.value=data
    emit('saved');await read()
  }catch(e){
    if(saved){state.value=null;error.value='操作已提交，但未能重读风险处置回执。'}
    else if(isUnconfirmedWrite(e)){
      retry.value=attempt;formError.value='未能确认提交结果，可按原请求重试。'
    }else if(isRejectedWrite(e)){
      retry.value=null;issued.value=null
      try{await read()}catch{state.value=null;reset()}
      if(!action.value)error.value=e.message
      else formError.value=e.message
    }else{
      retry.value=null;formError.value=message(e,'未能提交风险处置。')
    }
  }finally{saving.value=false}
}
</script>

<template>
  <el-dialog v-model="open" :title="incoming?'风险接收请求':'风险处置'" width="min(680px,94vw)" top="6vh" append-to-body
    :close-on-click-modal="false" :show-close="!saving&&!retry" :close-on-press-escape="!saving&&!retry">
    <section class="resolution" aria-label="风险处置详情" :aria-busy="busy">
      <div class="resolution-header">
        <div v-if="state">
          <h3>{{projectView?.risk.statement??'接收请求'}}</h3>
          <p class="meta"><template v-if="projectView">台账状态：{{riskStatuses[projectView.risk.status]??projectView.risk.status}} · </template>版本 {{state.risk.revision}}</p>
          <code class="meta">{{state.risk.riskId}}</code>
        </div>
        <el-button v-if="!confirmedHere" :disabled="locked" :aria-label="incoming?'刷新接收请求':'刷新风险处置'"
          :title="incoming?'刷新接收请求':'刷新风险处置'" @click="refresh"><RefreshCw :size="15"/></el-button>
      </div>
      <p v-if="loading" class="meta">正在读取风险处置…</p>
      <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
      <p v-if="notice" role="status" class="result-note">{{notice}}</p>
      <div v-if="issued" class="request-code">
        <label>接收请求编号<input :value="requestCode(issued)" class="code-input" readonly/></label>
        <el-button text title="复制接收请求编号" aria-label="复制接收请求编号" @click="copy(issued)"><Copy :size="15"/></el-button>
      </div>
      <p v-if="copyNotice" role="status" class="meta">{{copyNotice}}</p>
      <template v-if="state">
        <div v-if="projectView&&(projectView.canClose||projectView.canRequestAcceptance||projectView.canVerifyAcceptance)" class="toolbar resolution-actions" role="group" aria-label="风险处置操作">
          <el-button v-for="mode in (['close','request','verify'] as const).filter(mode=>allowed(mode))" :key="mode"
            :disabled="locked" :aria-pressed="action===mode" @click="begin(mode)"><component :is="actions[mode].icon" :size="15"/>{{actions[mode].label}}</el-button>
        </div>
        <form v-if="action" :key="action" ref="form" :aria-label="actions[action].label" @submit.prevent="save">
          <fieldset class="form-fields" :disabled="locked">
            <legend>{{actions[action].label}}</legend>
            <template v-if="action==='close'||action==='request'||action==='verify'">
              <label v-if="action!=='close'">接收人 ID<input v-model.trim="receiver" maxlength="36" required/></label>
              <label v-if="action!=='close'">接收范围<textarea v-model="scope" rows="2" minlength="5" maxlength="3000" required/></label>
              <ScopedEvidencePicker v-if="projectView" :key="action" v-model="evidenceRefs" :project-id="projectView.risk.projectId" resource-type="risk" :resource-id="riskId" :api="api"
                :label="action==='close'?'已核验关闭证据':action==='verify'?'已核验接收 PDF（不超过 1 MiB）':'已核验风险附件'"
                :mime-type="action==='verify'?'application/pdf':undefined" :max-bytes="action==='verify'?1024*1024:undefined" :disabled="locked"/>
              <template v-if="action==='verify'">
                <label>接收时间（本地）<input v-model="acceptedAt" type="datetime-local" step="1" required/></label>
                <label>身份核验依据<textarea v-model="identity" rows="2" minlength="12" maxlength="1000" required/></label>
                <details v-if="externalClause" class="meta"><summary>PDF 必须匹配的接收条款</summary><code>{{externalClause}}</code></details>
                <label>PDF 接收声明原文<textarea v-model="excerpt" rows="3" minlength="12" maxlength="4000" required/></label>
              </template>
            </template>
            <dl v-else-if="receipt" class="bindings">
              <dt>接收人</dt><dd>{{receipt.receiverPersonId}}</dd>
              <dt>接收范围</dt><dd class="scope">{{receipt.acceptanceScope}}</dd>
              <dt>证据</dt><dd><code>{{receipt.acceptanceEvidenceRef}}</code></dd>
              <dt>风险版本</dt><dd>{{receipt.riskRevision}}</dd>
            </dl>
          </fieldset>
          <el-alert v-if="formError" type="error" :title="formError" :closable="false" show-icon/>
          <div class="actions">
            <el-button :disabled="locked" @click="reset">取消</el-button>
            <el-button v-if="retry" :loading="saving" @click="send(retry)"><RotateCcw :size="15"/>重试上次提交</el-button>
            <el-button type="primary" native-type="submit" :disabled="locked||!command" :loading="saving"><component :is="actions[action].icon" :size="15"/>{{actions[action].submit}}</el-button>
          </div>
        </form>

        <section v-if="projectView" class="group" aria-label="处置回执">
          <h4>处置回执</h4>
          <template v-if="projectView.resolution">
            <p>{{projectView.resolution.status==='transferred'?'已签署移交':'已签署关闭'}}</p>
            <dl class="bindings">
              <dt>复核人</dt><dd>{{projectView.resolution.reviewerPersonId}}</dd>
              <template v-if="projectView.resolution.receiverPersonId"><dt>接收人</dt><dd>{{projectView.resolution.receiverPersonId}}</dd></template>
              <template v-if="projectView.resolution.closedAt"><dt>关闭时间</dt><dd><time :datetime="projectView.resolution.closedAt">{{time(projectView.resolution.closedAt)}}</time></dd></template>
              <dt>证据</dt><dd><a :href="`/api/v1/evidence/${projectView.resolution.evidenceRef.slice(9)}?download=true`" download><Download :size="14"/><code>{{projectView.resolution.evidenceRef}}</code></a> · V{{projectView.resolution.evidenceRevision}}</dd>
            </dl>
            <details>
              <summary>回执标识</summary>
              <dl class="bindings"><dt>处置回执</dt><dd>{{projectView.resolution.resolutionId}}</dd>
                <template v-if="projectView.resolution.acceptanceId"><dt>接收回执</dt><dd>{{projectView.resolution.acceptanceId}}</dd></template>
                <dt>SHA-256</dt><dd><code>{{projectView.resolution.evidenceSha256}}</code></dd></dl>
            </details>
          </template>
          <p v-else class="meta">暂无有效处置回执。</p>
        </section>
        <section class="group" aria-label="接收记录">
          <h4>接收记录</h4>
          <p v-if="!incoming&&state.acceptancesTruncated" class="meta">仅显示部分接收记录。</p>
          <p v-if="!acceptances.length" class="meta">暂无接收记录。</p>
          <ul v-else class="receipts">
            <li v-for="item in acceptances" :key="item.acceptanceId">
              <div class="entry-head">
                <strong>{{item.status==='pending'?receiptKinds.requested:receiptKinds[item.kind]}}</strong>
                <span class="meta">风险版本 {{item.riskRevision}} · <time :datetime="item.createdAt">{{time(item.createdAt)}}</time></span>
              </div>
              <dl class="bindings">
                <dt>接收人</dt><dd>{{item.receiverPersonId}}</dd>
                <dt>接收范围</dt><dd class="scope">{{item.acceptanceScope}}</dd>
                <dt>证据</dt><dd><code>{{item.acceptanceEvidenceRef}}</code> · V{{item.evidenceRevision}}</dd>
              </dl>
              <details>
                <summary>记录来源</summary>
                <dl class="bindings"><template v-if="projectView&&item.actorPersonId"><dt>记录人</dt><dd>{{item.actorPersonId}}</dd></template><dt>接收回执</dt><dd>{{item.acceptanceId}}</dd>
                  <dt>SHA-256</dt><dd><code>{{item.evidenceSha256}}</code></dd></dl>
                <div v-if="projectView&&item.status==='pending'&&item.kind==='requested'&&issued?.acceptanceId!==item.acceptanceId" class="request-code">
                  <label>接收请求编号<input :value="requestCode(item)" class="code-input" readonly/></label>
                  <el-button text title="复制接收请求编号" aria-label="复制接收请求编号" @click="copy(item)"><Copy :size="15"/></el-button>
                </div>
              </details>
              <div v-if="canConfirm(item)||canHandoff(item)" class="entry-actions">
                <el-button v-if="canConfirm(item)" :disabled="locked" @click="begin('confirm',item)"><Check :size="15"/>本人认证接收</el-button>
                <el-button v-if="canHandoff(item)" :disabled="locked" @click="begin('handoff',item)"><ShieldCheck :size="15"/>复核签署移交</el-button>
              </div>
            </li>
          </ul>
        </section>
      </template>
    </section>
  </el-dialog>
</template>

<style scoped>
.resolution{overflow-wrap:anywhere}
.resolution-header,.resolution-actions,.entry-head,.entry-actions{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.resolution-header{justify-content:space-between}
.resolution h3,.resolution h4{margin:0 0 .35rem}
.resolution p{margin:.3rem 0}
.group{border-top:1px solid var(--el-border-color);padding-top:.65rem;margin-top:.8rem}
.toolbar{margin-top:.7rem;padding:.65rem 0}
.meta,summary{color:var(--el-text-color-secondary);font-size:13px}
.result-note{color:var(--el-color-primary)}
.resolution form{gap:.5rem;margin:.4rem 0 1rem}
.form-fields{min-width:0;border:0;padding:0;margin:0;display:grid;gap:.65rem}
.form-fields legend{font-weight:600;padding:0;margin-bottom:.4rem}
.bindings{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.3rem .65rem;margin:.4rem 0}
.bindings dd{margin:0}
.scope{white-space:pre-wrap}
summary{cursor:pointer}
.resolution a{display:inline-flex;align-items:center;gap:.3rem;color:var(--el-color-primary);max-width:100%}
.resolution a svg{flex:none}
.receipts{list-style:none;padding:0;margin:.35rem 0}
.receipts li{padding:.65rem 0}
.receipts li+li{border-top:1px solid var(--el-border-color-lighter)}
.entry-actions{margin-top:.5rem}
.request-code{display:flex;align-items:end;gap:.35rem;margin:.5rem 0}
.request-code label{min-width:0;flex:1}
.code-input{font-family:monospace}
</style>
