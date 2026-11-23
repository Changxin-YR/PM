<script setup lang="ts">
import {isRejectedWrite} from './api-error'
import {computed,ref,watch} from 'vue'
import {Check,ClipboardPenLine,Gavel,Plus,RefreshCw,RotateCcw,Send,Trash2} from 'lucide-vue-next'
import RequirementExceptionReceipt, {type RequirementException} from './RequirementExceptionReceipt.vue'

const gates={charter:'立项',feasibility:'落地',closing:'结项'}
const statuses={draft:'草稿',pending:'待评审',decided:'已裁决'}
const verdicts={pass:'通过',conditional_pass:'有条件通过',defer:'暂缓',redirect:'转向',reject:'拒绝'}
const classifications={veto:'触发否决项',below_threshold:'未达阈值',normal:'常规',fast_track:'快速通道',shortfall:'存在短板',feasible:'可行'}
const roles={sponsor:'Sponsor',initiator:'发起人'}
const stages:Record<string,string>={drafting:'草稿',designing:'方案细化',rejected:'已拒绝',executing:'执行中',delivering:'交付验收',closed:'已结项',terminated:'已终止'}
const anchorCodes={1:'score_1',2:'between_1_3',3:'score_3',4:'between_3_5',5:'score_5'} as const
const scoreValues=[1,2,3,4,5] as const
const uuidPattern=/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i
const maxEvidence=12,maxConditions=20

type Role=keyof typeof roles
type Dimension={key:string;label:string;anchors:Record<string,string>}
type Rating={score:number;selectedAnchorCode:string;rationale:string;evidenceRefs:string[]}
type Workbench={
  review:{reviewId:string;projectId:string;type:keyof typeof gates;status:keyof typeof statuses;revision:number;verdict?:string}
  round:number;approvalRequestId?:string;dimensions:Dimension[];materialRefs:string[]
  scores:{scorecardId:string;reviewerId:string;round:number;dimensions:Record<string,Rating>;reviewedAt:string}[]
  appeal?:{reason:string;requestedAt:string;requestedBy:string}
  assessment?:{classification:keyof typeof classifications;total?:string;means:Record<string,string>;weakDimensions:string[]}
  confirmations:{role:Role;confirmedAt:string;confirmedBy:string;current:boolean}[]
  decision?:{decisionId:string;option:string;rationale:string;decidedAt:string;decidedBy:string;stage?:string;sourceRefs:string[]}
  withdrawalReason?:string
  requirementException?:RequirementException
  tasks:{taskId:string;title:string;assigneeId?:string;dueAt?:string;sourceType:string;sourceId:string;status:string;revision:number}[]
  canScore:boolean;canAppeal:boolean;canDecide:boolean;canWithdraw:boolean;canConfirmSponsor:boolean;canConfirmInitiator:boolean
  policyPending:boolean;stale:boolean;scoreEvidenceStale?:true
}
type ScoreDraft=Omit<Rating,'score'> & {score:keyof typeof anchorCodes|'';attachment:string}
type Condition={id:number;title:string;assigneeId:string;dueAt:string}
const actions=[
  {id:'score',flag:'canScore',label:'填写评分',submit:'提交评分',success:'已提交评分。',endpoint:'scores',icon:ClipboardPenLine},
  {id:'decide',flag:'canDecide',label:'登记裁决',submit:'确认裁决',success:'已登记裁决。',endpoint:'decisions',icon:Gavel},
  {id:'appeal',flag:'canAppeal',label:'申请复审',submit:'提交复审申请',success:'已提交复审申请。',endpoint:'appeals',icon:RotateCcw},
  {id:'sponsor',flag:'canConfirmSponsor',label:'Sponsor 确认',submit:'确认签署',success:'已记录 Sponsor 确认。',endpoint:'confirmations',icon:Check},
  {id:'initiator',flag:'canConfirmInitiator',label:'发起人确认',submit:'确认签署',success:'已记录发起人确认。',endpoint:'confirmations',icon:Check},
  {id:'withdraw',flag:'canWithdraw',label:'撤回评审',submit:'确认撤回',success:'已撤回评审。',endpoint:'withdraw',icon:RotateCcw},
] as const
type Action=typeof actions[number]
type Attempt={endpoint:Action['endpoint'];revision:number;body:string;key:string;success:string}

const props=defineProps<{reviewId:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const open=defineModel<boolean>({required:true})
const emit=defineEmits<{saved:[]}>()
const path=`reviews/${props.reviewId}`
const view=ref<Workbench|null>(null),loading=ref(false),saving=ref(false),readFailed=ref(false)
const error=ref(''),notice=ref(''),retry=ref<Attempt|null>(null)
const selected=ref<Action['id']|''>(''),scoreDraft=ref<Record<string,ScoreDraft>>({})
const reason=ref(''),verdict=ref<keyof typeof verdicts|''>(''),conditions=ref<Condition[]>([])
let conditionId=0,lastAttempt:Attempt|undefined

const busy=computed(()=>loading.value||saving.value)
const available=computed(()=>actions.filter(action=>view.value?.[action.flag]))
const current=computed(()=>available.value.find(action=>action.id===selected.value))
const materialRefs=computed(()=>Array.from(new Set(view.value?.materialRefs??[])))
const workDisabled=computed(()=>busy.value||readFailed.value||!!retry.value)

function message(e:unknown,fallback:string){return e instanceof Error?e.message:fallback}
function textLength(value:string){return Array.from(value.trim()).length}
function validReason(value:string){const n=textLength(value);return n>=4&&n<=1500}
function validAttachment(ref:string){return ref.startsWith('evidence:')&&uuidPattern.test(ref.slice(9))}
function anchorFor(score:ScoreDraft['score']){return score?anchorCodes[score]:''}
function localTime(value:string){return new Date(value).toLocaleString('zh-CN',{hour12:false})}
function dimensionLabel(key:string){return view.value?.dimensions.find(item=>item.key===key)?.label??key}
function verdictLabel(option:string){return verdicts[option as keyof typeof verdicts]??(option==='withdrawn'?'已撤回':option)}
function addCondition(){if(conditions.value.length<maxConditions)conditions.value.push({id:++conditionId,title:'',assigneeId:'',dueAt:''})}
function selectAction(id:Action['id']){
  selected.value=id;reason.value='';verdict.value='';conditions.value=[];error.value='';notice.value=''
}
function changeVerdict(){if(verdict.value==='conditional_pass'&&view.value?.review.type!=='closing'&&!conditions.value.length)addCondition()}

async function read(){
  const {data:next}:{data:Workbench}=await props.api(`${path}/workbench`)
  const sameRound=view.value?.round===next.round
  scoreDraft.value=Object.fromEntries(next.dimensions.map(({key})=>[key,
    (sameRound&&scoreDraft.value[key])||{score:'',selectedAnchorCode:'',rationale:'',evidenceRefs:[],attachment:''},
  ]))
  view.value=next
  if(!current.value)selected.value=''
  readFailed.value=false
}

async function refresh(){
  if(busy.value)return
  loading.value=true;error.value=''
  try{await read();retry.value=null}
  catch(e){view.value=null;readFailed.value=true;error.value=message(e,'未能读取评审工作台。')}
  finally{loading.value=false}
}
watch(open,value=>{if(value)void refresh()},{immediate:true})

const command=computed(()=>{
  const data=view.value,action=current.value
  if(!data||!action)return null
  if(action.id==='score'){
    const dimensions:Record<string,Rating>={}
    if(!data.dimensions.length)return null
    for(const dimension of data.dimensions){
      const line=scoreDraft.value[dimension.key],materials=line.evidenceRefs.filter(ref=>materialRefs.value.includes(ref))
      const attachment=line.attachment.trim().toLowerCase(),score=line.score
      const evidenceRefs=[...new Set(attachment?[...materials,attachment]:materials)]
      if(!score||line.selectedAnchorCode!==anchorCodes[score]||!dimension.anchors[line.selectedAnchorCode]||
         !validReason(line.rationale)||!materials.length||evidenceRefs.length>maxEvidence||(attachment&&!validAttachment(attachment)))return null
      dimensions[dimension.key]={score,selectedAnchorCode:line.selectedAnchorCode,rationale:line.rationale.trim(),evidenceRefs}
    }
    return {reviewType:data.review.type,round:data.round,dimensions}
  }
  if(action.id==='sponsor'||action.id==='initiator')return {role:action.id}
  if(!validReason(reason.value))return null
  if(action.id!=='decide')return {reason:reason.value.trim()}
  if(!verdict.value||!data.approvalRequestId)return null
  const base={verdict:verdict.value,rationale:reason.value.trim(),approvalRequestId:data.approvalRequestId}
  if(data.review.type==='closing'){
    if(verdict.value==='conditional_pass'&&!data.requirementException||verdict.value==='pass'&&data.requirementException)return null
    return base
  }
  if(verdict.value!=='conditional_pass')return base
  if(!conditions.value.length||conditions.value.some(row=>textLength(row.title.replace(/\s+/g,' '))<4||
    !uuidPattern.test(row.assigneeId.trim())||!(Date.parse(row.dueAt)>Date.now())))return null
  return {...base,conditions:conditions.value.map(row=>({title:row.title.trim(),assigneeId:row.assigneeId.trim(),
    dueAt:new Date(row.dueAt).toISOString()}))}
})

async function checkAttachments(data:Workbench){
  const refs=data.dimensions.map(({key})=>scoreDraft.value[key].attachment.trim().toLowerCase())
    .filter(ref=>ref&&!materialRefs.value.includes(ref))
  const subjects=new Set([`review:${props.reviewId}`,...data.materialRefs.filter(ref=>/^(charter|feasibility):/.test(ref)).map(ref=>ref.split('@')[0])])
  for(const ref of new Set(refs)){
    const {data:evidence}=await props.api(`evidence/${ref.slice(9)}`)
    if(evidence.projectId!==data.review.projectId||evidence.evidenceRef!==ref||
       !subjects.has(`${evidence.linkedResourceType}:${evidence.linkedResourceId}`)||
       evidence.status!=='verified')throw Error(`附件 ${ref} 尚未核验或未关联本次评审资料。`)
  }
}

async function submit(previous?:Attempt){
  const data=view.value,action=current.value,payload=command.value
  if(busy.value||(!previous&&(workDisabled.value||!data||!action||!payload)))return
  saving.value=true;error.value='';notice.value=''
  let sent:Attempt|undefined,saved=false
  try{
    if(previous)sent=previous
    else if(data&&action&&payload){
      const {endpoint,success}=action,revision=data.review.revision,body=JSON.stringify(payload)
      if(lastAttempt?.endpoint===endpoint&&lastAttempt.revision===revision&&lastAttempt.body===body)sent=lastAttempt
      else{
        if(action.id==='score')await checkAttachments(data)
        sent={endpoint,revision,body,key:crypto.randomUUID(),success}
      }
    }
    if(!sent)return
    lastAttempt=sent
    await props.api(`${path}/${sent.endpoint}`,{method:'POST',body:sent.body,
      headers:{'If-Match':String(sent.revision),'Idempotency-Key':sent.key}})
    saved=true;retry.value=null;lastAttempt=undefined;selected.value='';notice.value=sent.success
    emit('saved')
    await read()
  }catch(e){
    if(saved){readFailed.value=true;error.value='操作已保存，但未能重读工作台。'}
    else if(sent&&(e instanceof TypeError||e instanceof SyntaxError)){
      retry.value=sent;error.value='未能确认提交结果，可按原请求重试。'
    }else{
      retry.value=null
      if(isRejectedWrite(e)){
        lastAttempt=undefined
        try{await read()}catch{view.value=null;readFailed.value=true}
        emit('saved') // Parent lists must also reread a decision that has changed elsewhere.
      }
      error.value=message(e,'提交失败。')
    }
  }finally{saving.value=false}
}
</script>

<template>
  <el-dialog v-model="open" append-to-body title="关口人工工作台" width="min(680px,94vw)" top="6vh"
    :close-on-click-modal="false" :show-close="!saving" :close-on-press-escape="!saving">
    <section class="workbench" aria-label="评审详情" v-loading="loading">
      <div class="workbench-header">
        <div v-if="view">
          <h3>{{gates[view.review.type]}}评审</h3>
          <span class="meta">第 {{view.round}} 轮 · {{statuses[view.review.status]}} · 版本 {{view.review.revision}}</span>
        </div>
        <el-button :disabled="busy" @click="refresh"><RefreshCw :size="14"/> 重读工作台</el-button>
      </div>
      <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
      <el-button v-if="retry" :loading="saving" :disabled="loading" @click="submit(retry)"><RotateCcw :size="14"/> 重试上次提交</el-button>
      <p v-if="notice" role="status" class="result-note">{{notice}}</p>
      <template v-if="view">
        <el-alert v-if="view.policyPending" type="warning" title="该评审的业务策略尚待签核。" :closable="false" show-icon/>
        <el-alert v-if="view.stale" type="warning" title="送审所依据的材料或授权已变化。" :closable="false" show-icon/>
        <el-alert v-if="view.scoreEvidenceStale" type="warning" title="有效评分的证据无法通过复核，暂不可确认或裁决。" :closable="false" show-icon/>
        <RequirementExceptionReceipt v-if="view.requirementException" :receipt="view.requirementException"
          :title="view.decision?'裁决接受的具体需求偏差':'本次送审冻结的客户书面例外'"/>
        <section v-if="view.assessment" class="group" aria-label="评分评定">
          <h4>评分评定 · {{classifications[view.assessment.classification]}}</h4>
          <p v-if="view.assessment.total!=null">总分：{{view.assessment.total}}</p>
          <ul v-if="Object.keys(view.assessment.means).length" class="compact-list">
            <li v-for="(mean,key) in view.assessment.means" :key="key">{{dimensionLabel(key)}}：{{mean}}</li>
          </ul>
          <p v-if="view.assessment.weakDimensions.length">薄弱维度：{{view.assessment.weakDimensions.map(dimensionLabel).join('、')}}</p>
        </section>
        <details v-if="materialRefs.length" class="group">
          <summary>送审材料引用 · {{materialRefs.length}}</summary>
          <ul class="compact-list"><li v-for="source in materialRefs" :key="source"><code>{{source}}</code></li></ul>
        </details>
        <div v-if="available.length" class="toolbar workbench-actions" role="group" aria-label="评审操作">
          <el-button v-for="action in available" :key="action.id" :type="current?.id===action.id?'primary':undefined"
            :aria-pressed="current?.id===action.id" :disabled="workDisabled" @click="selectAction(action.id)">
            <component :is="action.icon" :size="15"/>{{action.label}}
          </el-button>
        </div>
        <form v-if="current" :aria-label="current.label" @submit.prevent="submit()">
          <fieldset class="form-fields" :disabled="workDisabled">
            <template v-if="current.id==='score'">
              <fieldset v-for="dimension in view.dimensions" :key="dimension.key" class="form-block">
                <legend>{{dimension.label}}</legend>
                <div class="field-row">
                  <label>评分<select v-model="scoreDraft[dimension.key].score" aria-label="评分" required @change="scoreDraft[dimension.key].selectedAnchorCode=''">
                    <option value="" disabled>请选择分值</option>
                    <option v-for="n in scoreValues" :key="n" :value="n" :disabled="!dimension.anchors[anchorCodes[n]]">{{n}} 分</option>
                  </select></label>
                  <label>评分锚点<select v-model="scoreDraft[dimension.key].selectedAnchorCode" aria-label="评分锚点" required :disabled="!scoreDraft[dimension.key].score">
                    <option value="" disabled>请选择锚点</option>
                    <option v-if="scoreDraft[dimension.key].score" :value="anchorFor(scoreDraft[dimension.key].score)">
                      {{dimension.anchors[anchorFor(scoreDraft[dimension.key].score)]}}
                    </option>
                  </select></label>
                </div>
                <p v-if="scoreDraft[dimension.key].score" class="meta">{{dimension.anchors[anchorFor(scoreDraft[dimension.key].score)]}}</p>
                <label>评分理由<textarea v-model="scoreDraft[dimension.key].rationale" rows="2" required minlength="4" maxlength="1500"/></label>
                <details>
                  <summary>材料引用（至少一项） · 已选 {{scoreDraft[dimension.key].evidenceRefs.filter(ref=>materialRefs.includes(ref)).length}} / {{maxEvidence}}</summary>
                  <div class="ref-options" role="group" aria-label="可选材料引用">
                    <label v-for="source in materialRefs" :key="source" class="ref-option">
                      <input v-model="scoreDraft[dimension.key].evidenceRefs" type="checkbox" :value="source"/><code>{{source}}</code>
                    </label>
                  </div>
                </details>
                <label>补充已核验附件引用（可选）<input v-model.trim="scoreDraft[dimension.key].attachment" placeholder="evidence:…" maxlength="45"/></label>
              </fieldset>
            </template>
            <template v-else-if="current.id==='decide'">
              <label>裁决<select v-model="verdict" aria-label="裁决" required @change="changeVerdict">
                <option value="" disabled>请选择裁决</option>
                <option v-for="(label,option) in verdicts" :key="option" :value="option"
                  :disabled="view.review.type==='closing'&&((option==='conditional_pass'&&!view.requirementException)||(option==='pass'&&!!view.requirementException))">{{label}}</option>
              </select></label>
              <label>裁决理由<textarea v-model="reason" rows="3" required minlength="4" maxlength="1500"/></label>
              <template v-if="verdict==='conditional_pass'&&view.review.type!=='closing'">
                <fieldset v-for="(condition,index) in conditions" :key="condition.id" class="form-block">
                  <legend>条件 {{index+1}}</legend>
                  <label>条件标题<input v-model.trim="condition.title" required minlength="4" maxlength="180"/></label>
                  <div class="field-row">
                    <label>负责人 ID<input v-model.trim="condition.assigneeId" maxlength="36" required/></label>
                    <label>截止时间（本地）<input v-model="condition.dueAt" type="datetime-local" required/></label>
                  </div>
                  <el-button text :disabled="conditions.length===1||workDisabled" @click="conditions.splice(index,1)"><Trash2 :size="14"/> 移除条件</el-button>
                </fieldset>
                <el-button class="add-condition" :disabled="workDisabled||conditions.length>=maxConditions" @click="addCondition"><Plus :size="14"/> 添加条件</el-button>
              </template>
            </template>
            <p v-else-if="current.id==='sponsor'||current.id==='initiator'">以{{roles[current.id]}}身份确认本轮评分。</p>
            <label v-else>{{current.id==='appeal'?'复审原因':'撤回原因'}}<textarea v-model="reason" rows="3" required minlength="4" maxlength="1500"/></label>
          </fieldset>
          <div class="actions">
            <el-button :disabled="saving || !!retry" @click="selected=''">取消</el-button>
            <el-button type="primary" native-type="submit" :disabled="workDisabled||!command" :loading="saving"><Send :size="14"/>{{current.submit}}</el-button>
          </div>
        </form>
        <section v-if="view.decision||view.review.verdict" class="group" aria-label="裁决记录">
          <h4>裁决记录 · {{verdictLabel(view.decision?.option??view.review.verdict??'')}}</h4>
          <template v-if="view.decision">
            <p class="reason">{{view.decision.rationale}}</p>
            <p class="meta"><time :datetime="view.decision.decidedAt">{{localTime(view.decision.decidedAt)}}</time> · 裁决人 {{view.decision.decidedBy}}</p>
            <p v-if="view.decision.stage">流转阶段：{{stages[view.decision.stage]??view.decision.stage}}</p>
            <details v-if="view.decision.sourceRefs.length" class="meta">
              <summary>裁决来源</summary>
              <ul class="refs"><li v-for="source in view.decision.sourceRefs" :key="source">{{source}}</li></ul>
            </details>
          </template>
          <p v-else-if="view.withdrawalReason" class="reason">{{view.withdrawalReason}}</p>
        </section>
        <section v-if="view.appeal" class="group" aria-label="复审申请记录">
          <h4>复审申请记录</h4><p class="reason">{{view.appeal.reason}}</p>
          <p class="meta"><time :datetime="view.appeal.requestedAt">{{localTime(view.appeal.requestedAt)}}</time> · 申请人 {{view.appeal.requestedBy}}</p>
        </section>
        <section v-if="view.confirmations.length" class="group" aria-label="确认记录">
          <h4>确认记录</h4>
          <ul class="compact-list"><li v-for="entry in view.confirmations" :key="`${entry.role}:${entry.confirmedAt}`">
            {{roles[entry.role]}} · {{entry.current?'当前有效':'历史确认'}} · <time :datetime="entry.confirmedAt">{{localTime(entry.confirmedAt)}}</time>
            <span class="meta"> · 确认人 {{entry.confirmedBy}}</span>
          </li></ul>
        </section>
        <section class="group" aria-label="评分历史">
          <h4>评分历史 · {{view.scores.length}}</h4>
          <p v-if="!view.scores.length" class="meta">暂无评分记录</p>
          <details v-for="card in view.scores" :key="card.scorecardId" class="history-item" open>
            <summary>第 {{card.round}} 轮 · <time :datetime="card.reviewedAt">{{localTime(card.reviewedAt)}}</time><span class="meta"> · 评审人 {{card.reviewerId}}</span></summary>
            <dl v-for="(rating,key) in card.dimensions" :key="key">
              <dt>{{dimensionLabel(key)}} · {{rating.score}} 分</dt>
              <dd class="meta">{{view.dimensions.find(item=>item.key===key)?.anchors[rating.selectedAnchorCode]??rating.selectedAnchorCode}}</dd>
              <dd class="reason">{{rating.rationale}}</dd>
              <dd v-for="source in rating.evidenceRefs" :key="source"><code>{{source}}</code></dd>
            </dl>
          </details>
        </section>
        <section v-if="view.tasks.length" class="group" aria-label="关口任务">
          <h4>关口任务</h4>
          <ul class="compact-list"><li v-for="task in view.tasks" :key="task.taskId">
            <strong>{{task.title}}</strong> · {{task.status}}
            <p class="meta">负责人 {{task.assigneeId||'—'}} · 截止时间 <time v-if="task.dueAt" :datetime="task.dueAt">{{localTime(task.dueAt)}}</time><span v-else>—</span></p>
            <p class="meta">来源 <code>{{task.sourceType}}:{{task.sourceId}}</code> · 版本 {{task.revision}}</p>
          </li></ul>
        </section>
      </template>
    </section>
  </el-dialog>
</template>

<style scoped>
.workbench{min-height:4rem;overflow-wrap:anywhere}
.workbench-header,.workbench-actions{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.workbench-header{justify-content:space-between}
.workbench h3,.workbench h4{margin:0 0 .4rem}
.workbench p{margin:.4rem 0}
.meta{color:var(--el-text-color-secondary);font-size:13px}
.result-note{color:var(--el-color-primary)}
.group{border-top:1px solid var(--el-border-color);padding-top:.65rem;margin-top:.8rem}
.compact-list{margin:.35rem 0;padding-left:1.25rem}
summary{cursor:pointer}
.workbench-actions{margin-top:.8rem}
.workbench form{gap:.5rem}
.form-fields,.form-block{min-width:0;margin:0;display:grid;gap:.65rem}
.form-fields{padding:0;border:0}
.form-block{border:1px solid var(--el-border-color);padding:.7rem;border-radius:var(--el-border-radius-base)}
.form-block legend{font-weight:600;padding:0 .25rem}
.field-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,175px),1fr));gap:.6rem}
.field-row label{min-width:0}
select{min-width:0;width:100%;padding:.4rem;color:inherit;font:inherit;border:1px solid var(--el-border-color);border-radius:var(--el-border-radius-base);background:var(--el-bg-color)}
.add-condition{justify-self:start}
.ref-options{max-height:9rem;overflow:auto;margin-top:.3rem}
.ref-option{display:flex;align-items:baseline;padding:.2rem 0}
.ref-option input{width:auto;flex:none}
.history-item{padding:.4rem 0}
.history-item dl{margin:.35rem 0 0 .5rem;padding-left:.55rem;border-left:2px solid var(--el-border-color)}
.history-item dd{margin:.15rem 0}
.reason{white-space:pre-wrap}
</style>
