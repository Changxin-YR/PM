<script setup lang="ts">
import {computed,ref} from 'vue'
import {PmoHttpError} from './api-error'

const actions={continue:'继续投入',adjust:'调整方向',hold:'建议暂停',terminate:'建议终止'}
const reasons={estimate:'估算偏差',execution:'执行偏差',scope:'范围变化'}
const states={material_required:'待重估',decision_required:'待决议',decided:'已决议',stale:'已失效（历史）'}
const errors:Record<string,string>={COST_REASSESSMENT_STALE:'成本依据已经变化，请刷新任务并核对最新重估。',
  COST_REASSESSMENT_SUBMISSION_CHANGED:'重估材料已经修订，请刷新后核对当前版本。',
  COST_REASSESSMENT_EVIDENCE_CHANGED:'佐证已变化，请由项目经理重新提交。',
  REVIEW_RESPONSE_EVIDENCE_INVALID:'请使用本项目内已经独立核验的附件。'}
type Action=keyof typeof actions
type Reason=keyof typeof reasons
type Material={currentSituation:string;attribution:Reason;recommendation:string;evidenceRefs:string[]}
type Decision={submissionId:string;option:Action;rationale:string;conditions?:string}
export type Reassessment={eventId:string;costType:'money'|'effort';budget:string;actual:string;remaining:string;
  projected:string;variance:string;sourceRefs:string[];state:keyof typeof states;policyStatus:string;canSubmit:boolean;canDecide:boolean;
  history:{recordId:string;actorId:string;recordedAt:string;material?:Material;decision?:Decision;decisionId?:string}[]}
const props=defineProps<{taskId:string;revision:number;item:Reassessment;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{saved:[]}>()
const mode=ref<'submit'|'decide'|null>(null),busy=ref(false),error=ref('')
const currentSituation=ref(''),attribution=ref<Reason>('estimate'),recommendation=ref(''),evidenceRefs=ref('')
const option=ref<Action>('continue'),rationale=ref(''),conditions=ref('')
const latest=computed(()=>props.item.history.find(entry=>entry.material))
const difference=computed(()=>new Intl.NumberFormat('zh-CN',{style:'percent',minimumFractionDigits:2}).format(Number(props.item.variance)))
const unit=computed(()=>props.item.costType==='money'?'元':'人天')
const dateFormat=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'})
let previous='',key=''

function open(next:'submit'|'decide'){
  const existing=latest.value?.material
  currentSituation.value=existing?.currentSituation??'';attribution.value=existing?.attribution??'estimate'
  recommendation.value=existing?.recommendation??'';evidenceRefs.value=existing?.evidenceRefs.join('\n')??''
  option.value='continue';rationale.value='';conditions.value='';error.value='';mode.value=next
}

async function save(){
  if(!mode.value||busy.value)return
  const deciding=mode.value==='decide',submissionId=latest.value?.recordId
  if(deciding&&!submissionId)return
  const path='tasks/'+props.taskId+'/cost-reassessment'+(deciding?'/decision':'')
  const body=JSON.stringify(deciding?{submissionId,option:option.value,rationale:rationale.value.trim(),
    ...(conditions.value.trim()?{conditions:conditions.value.trim()}:{})}:{currentSituation:currentSituation.value.trim(),
    attribution:attribution.value,recommendation:recommendation.value.trim(),evidenceRefs:evidenceRefs.value.trim().split(/\s+/u).filter(Boolean)})
  const request=JSON.stringify([path,props.revision,body])
  if(request!==previous){previous=request;key=crypto.randomUUID()}
  busy.value=true;error.value=''
  try{
    await props.api(path,{method:'POST',headers:{'If-Match':String(props.revision),'Idempotency-Key':key},body})
    mode.value=null;emit('saved')
  }catch(e){error.value=e instanceof PmoHttpError?(errors[e.code]??e.message):
    e instanceof Error?e.message:'未能保存重估，请重试。'}
  finally{busy.value=false}
}
</script>

<template>
  <details class="reassessment" open>
    <summary>{{item.costType==='money'?'金额':'人天'}}超支重估 · {{states[item.state]}}</summary>
    <dl class="reassessment-metrics">
      <div><dt>获批预算</dt><dd>{{item.budget}} {{unit}}</dd></div>
      <div><dt>核验实际</dt><dd>{{item.actual}} {{unit}}</dd></div>
      <div><dt>剩余预测</dt><dd>{{item.remaining}} {{unit}}</dd></div>
      <div><dt>预计总投入</dt><dd>{{item.projected}} {{unit}}</dd></div>
      <div><dt>偏差</dt><dd>{{difference}}</dd></div>
    </dl>
    <p v-if="item.policyStatus==='POLICY_PENDING'" class="reassessment-policy">继续投入的正式授权口径待签。</p>
    <p>重估裁决只结清当前任务。预算变更和阶段流转仍走各自流程。</p>
    <details>
      <summary>触发事件与成本来源</summary>
      <p><code>{{item.eventId}}</code></p>
      <ul><li v-for="source in item.sourceRefs" :key="source"><code>{{source}}</code></li></ul>
    </details>
    <article v-for="entry in item.history" :key="entry.recordId" class="reassessment-history">
      <strong>{{entry.decision?actions[entry.decision.option]:'书面重估'}}</strong>
      <small>{{dateFormat.format(new Date(entry.recordedAt))}} · {{entry.actorId}}</small>
      <template v-if="entry.material">
        <p>{{entry.material.currentSituation}}</p><p>{{reasons[entry.material.attribution]}} · {{entry.material.recommendation}}</p>
        <ul><li v-for="source in entry.material.evidenceRefs" :key="source"><code>{{source}}</code></li></ul>
      </template>
      <template v-if="entry.decision">
        <p>{{entry.decision.rationale}}</p><p v-if="entry.decision.conditions">适用条件　{{entry.decision.conditions}}</p>
        <code>{{entry.decisionId}}</code>
      </template>
    </article>
    <div v-if="!mode" class="reassessment-actions">
      <el-button v-if="item.canSubmit" size="small" @click="open('submit')">{{latest?'修订重估':'填写重估'}}</el-button>
      <el-button v-if="item.canDecide" size="small" type="primary" @click="open('decide')">记录投入决议</el-button>
    </div>
    <form v-else class="reassessment-form" @submit.prevent="save">
      <fieldset :disabled="busy">
        <template v-if="mode==='submit'">
          <label>原目标与当前情况<textarea v-model="currentSituation" rows="3" minlength="4" maxlength="6000" required/></label>
          <label>差异归因<select v-model="attribution"><option v-for="(label,value) in reasons" :key="value" :value="value">{{label}}</option></select></label>
          <label>更新建议<textarea v-model="recommendation" rows="2" minlength="4" maxlength="6000" required/></label>
          <label>已核验附件引用<textarea v-model="evidenceRefs" rows="2" placeholder="每行一个 evidence:… 引用" required/></label>
        </template>
        <template v-else>
          <label>决议<select v-model="option"><option v-for="(label,value) in actions" :key="value" :value="value">{{label}}</option></select></label>
          <label>裁决理由<textarea v-model="rationale" rows="2" minlength="4" maxlength="6000" required/></label>
          <label>适用条件<input v-model="conditions" minlength="4" maxlength="6000"></label>
        </template>
      </fieldset>
      <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
      <div class="reassessment-actions"><el-button :disabled="busy" @click="mode=null">取消</el-button>
        <el-button native-type="submit" type="primary" :loading="busy">{{mode==='submit'?'提交重估':'确认决议'}}</el-button></div>
    </form>
  </details>
</template>

<style scoped>
.reassessment{margin-top:6px;overflow-wrap:anywhere}
.reassessment summary{cursor:pointer;color:var(--pmo-primary,#405eb8)}
.reassessment-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px;margin:10px 0}
dt,small{color:var(--el-text-color-secondary)}
dd{margin:0;font-weight:600;font-variant-numeric:tabular-nums}
.reassessment-history,.reassessment-form{border-top:1px solid var(--pmo-line);padding:8px 0}
.reassessment-history small{display:block}
.reassessment-policy{color:var(--el-color-warning-dark-2)}
.reassessment-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.reassessment-actions button{margin:0}
fieldset{display:grid;gap:8px;border:0;padding:0;margin:0;min-width:0}
.reassessment-form fieldset{max-width:720px}
ul{margin:4px 0;padding-inline-start:16px}
</style>
