<script setup lang="ts">
import {computed,reactive,ref,watch} from 'vue'
import {RefreshCw,RotateCcw,Send} from 'lucide-vue-next'
import {isUnconfirmedWrite,PmoHttpError} from './api-error'

const kinds={milestone:'里程碑计划',budget:'获批预算',charter:'冻结立项卡'}
type Kind=keyof typeof kinds
type Risk={statement:string;response:string}
const narrativeFields={valueProp:'价值主张',targetClient:'目标客户',attractiveness:'客户或市场吸引力',expectedBenefit:'预期收益',successCriteria:'成功标准'}
type Narratives=Record<keyof typeof narrativeFields,string>
type Charter=Narratives&{roughCost:string;roughEffort:string;roughDurationDays:number;topRisks:Risk[]}
type Resource={targetId:string;revision:number}
type Target=Resource&(
  {kind:'milestone';name:string;plannedDate:string;isKey:boolean} |
  {kind:'budget';type:'money'|'effort';category:string;periodStart:string;periodEnd:string;budget:string} |
  {kind:'charter';charter:Partial<Charter>}
)
type Value=string|number|boolean|Risk[]|null
type Snapshot=Record<string,Value>
type Attempt={body:string;revision:number;key:string}
type TargetPage={items:Target[];page:number;pageSize:number;total:number}

const props=defineProps<{projectId:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{close:[];saved:[]}>()
const path=`projects/${props.projectId}/changes`
const kind=ref<Kind|''>(''),items=ref<Target[]>([]),targetId=ref(''),page=ref(1),pageSize=ref(20),total=ref(0)
const busy=ref(false),reading=ref(false),readError=ref(''),error=ref(''),stale=ref(false),reason=ref('')
const retry=ref<Attempt>(),locked=computed(()=>busy.value||!!retry.value)
const selected=computed(()=>items.value.find(item=>item.targetId===targetId.value))
const baseline=ref<Snapshot>({})
const milestone=reactive({name:'',plannedDate:'',isKey:false})
const budget=ref('')
const charter=reactive({valueProp:'',targetClient:'',attractiveness:'',expectedBenefit:'',successCriteria:'',
  roughCost:'',roughEffort:'',roughDuration:undefined as number|string|undefined,topRisks:[] as Risk[]})
const moneyPattern='(0|[1-9][0-9]{0,15})(\\.[0-9]{1,2})?'
const effortPattern='(0|[1-9][0-9]{0,9})(\\.[0-9]{1,2})?'
let readId=0

function targetName(item:Target){
  if(item.kind==='milestone')return `${item.name} · ${item.plannedDate}`
  if(item.kind==='budget'){
    const category=item.category==='project_baseline'?'项目预算基线':item.category
    return `${item.type==='money'?'金额（元）':'人天'} · ${category} · ${item.periodStart} – ${item.periodEnd}`
  }
  return '当前冻结立项卡'
}

function populate(item:Target|undefined){
  if(!item){baseline.value={};return}
  if(item.kind==='milestone'){
    const values={name:item.name,plannedDate:item.plannedDate,isKey:item.isKey}
    Object.assign(milestone,values);baseline.value=values
  }else if(item.kind==='budget'){
    budget.value=item.budget;baseline.value={budget:item.budget}
  }else{
    const row=item.charter
    const values:Snapshot={roughCost:row.roughCost??null,roughEffort:row.roughEffort??null,
      roughDuration:row.roughDurationDays??null,topRisks:row.topRisks??null}
    for(const key of Object.keys(narrativeFields) as (keyof Narratives)[]){
      values[key]=row[key]??null;charter[key]=row[key]??''
    }
    charter.roughCost=row.roughCost??'';charter.roughEffort=row.roughEffort??'';charter.roughDuration=row.roughDurationDays
    charter.topRisks=row.topRisks?.map(risk=>({...risk}))??[];baseline.value=values
  }
}
watch(selected,populate)

async function read(next=page.value){
  const readKind=kind.value,id=++readId
  if(!readKind)return
  reading.value=true;readError.value='';error.value=''
  try{
    const {data}:{data:TargetPage}=await props.api(`${path}/targets?${new URLSearchParams({kind:readKind,page:String(next),pageSize:String(pageSize.value)})}`)
    if(id!==readId)return
    const last=Math.max(1,Math.ceil(data.total/data.pageSize))
    if(next>last){await read(last);return}
    if(!data.items.some(item=>item.targetId===targetId.value))targetId.value=data.items.length===1?data.items[0]!.targetId:''
    items.value=data.items;page.value=data.page;pageSize.value=data.pageSize;total.value=data.total;stale.value=false
  }catch(e){
    if(id===readId){items.value=[];targetId.value='';total.value=0;readError.value=e instanceof Error?e.message:'读取变更资料失败。'}
  }finally{if(id===readId)reading.value=false}
}
watch(kind,()=>{targetId.value='';items.value=[];total.value=0;page.value=1;void read(1)})

function decimal(value:string){
  const [whole,fraction='']=value.split('.')
  return `${whole}.${fraction.padEnd(2,'0')}`
}
const delta=computed(()=>{
  const current=baseline.value
  const edited:Snapshot=selected.value?.kind==='milestone'?{...milestone}
    :selected.value?.kind==='budget'?{budget:budget.value}
    :{...charter,roughDuration:typeof charter.roughDuration==='number'?charter.roughDuration:null}
  const before:Snapshot={},after:Snapshot={}
  let missing=false
  for(const [key,original] of Object.entries(current)){
    const value=edited[key]??null
    if((original===null&&(value===''||Array.isArray(value)&&!value.length))||JSON.stringify(original)===JSON.stringify(value))continue
    if(value===null||typeof value==='string'&&!value.trim()||Array.isArray(value)&&!value.length){missing=true;continue}
    if(typeof value==='string'&&['budget','roughCost','roughEffort'].includes(key)&&typeof original==='string'&&decimal(value)===decimal(original))continue
    before[key]=original;after[key]=value
  }
  return {before,after,missing,changed:Object.keys(after).length>0}
})

function close(){if(!locked.value)emit('close')}

async function send(request:Attempt){
  if(busy.value)return
  busy.value=true;error.value=''
  try{
    await props.api(path,{method:'POST',headers:{'If-Match':String(request.revision),'Idempotency-Key':request.key},body:request.body})
    retry.value=undefined;emit('saved')
  }catch(e){
    if(isUnconfirmedWrite(e)){
      retry.value=request;error.value='未能确认提交结果。可按原申请重试。'
    }else{
      retry.value=undefined
      stale.value=e instanceof PmoHttpError&&['VERSION_CONFLICT','BASELINE_CHANGED','NO_ACTIVE_BASELINE','MILESTONE_LOCKED'].includes(e.code)
      error.value=stale.value?'基线或资料状态已变化，请重新读取并核对后再提交。':e instanceof Error?e.message:'提交变更申请失败。'
    }
  }finally{busy.value=false}
}

function confirm(){
  const row=selected.value,{before,after,missing,changed}=delta.value,why=reason.value.trim()
  if(!row||locked.value||stale.value||missing||!changed||why.length<3)return
  void send({revision:row.revision,key:crypto.randomUUID(),
    body:JSON.stringify({targetType:row.kind,targetId:row.targetId,before,after,reason:why})})
}
</script>

<template>
  <el-dialog :model-value="true" title="申请正式变更" width="min(760px,94vw)" align-center append-to-body
    :show-close="!locked" :close-on-click-modal="false" :close-on-press-escape="!locked" @update:model-value="(visible:boolean)=>{if(!visible)close()}">
    <form aria-label="正式变更申请" @submit.prevent="confirm">
      <p class="proposal-note">提交后进入独立审批。当前计划、预算或立项卡在批准前按现有版本继续使用。</p>
      <label>变更资料类型<select v-model="kind" :disabled="locked" required>
        <option value="" disabled>选择要变更的资料类型</option>
        <option v-for="(text,value) in kinds" :key="value" :value="value">{{text}}</option>
      </select></label>
      <div v-if="kind" class="proposal-target">
        <p v-if="reading" role="status">正在读取当前基线资料…</p>
        <el-alert v-else-if="readError" :title="readError" type="error" :closable="false" show-icon/>
        <p v-else-if="!total" class="proposal-note">当前阶段没有可申请变更的此类资料。</p>
        <label v-else>变更目标<select v-model="targetId" :disabled="locked||reading||stale" required>
          <option value="" disabled>选择变更目标</option>
          <option v-for="item in items" :key="item.targetId" :value="item.targetId">{{targetName(item)}}</option>
        </select></label>
        <el-pagination v-if="total>pageSize" aria-label="变更目标分页" :page-size="pageSize" :total="total" :current-page="page" :pager-count="5"
          layout="prev,pager,next" :disabled="locked||reading" @current-change="read"/>
        <el-button v-if="readError||stale" :disabled="locked||reading" @click="read()"><RefreshCw :size="16"/>重新读取当前资料</el-button>
      </div>
      <fieldset v-if="selected" :disabled="locked||stale||reading">
        <legend>拟变更内容 <span>当前版本 {{selected.revision}}</span></legend>
        <template v-if="selected.kind==='milestone'">
          <label>里程碑名称<input v-model="milestone.name" required maxlength="160"></label>
          <label>计划日期<input v-model="milestone.plannedDate" type="date" required></label>
          <label class="key-field"><input v-model="milestone.isKey" type="checkbox"> 关键里程碑</label>
        </template>
        <label v-else-if="selected.kind==='budget'">{{selected.type==='money'?'拟变更预算（元）':'拟变更预算（人天）'}}
          <input v-model="budget" inputmode="decimal" :pattern="moneyPattern" required>
        </label>
        <template v-else>
          <label v-for="(text,field) in narrativeFields" :key="field">{{text}}
            <textarea v-model="charter[field]" :maxlength="field==='targetClient'?2000:3000" rows="2"/>
          </label>
          <div class="estimate-fields">
            <label>粗估金额（元）<input v-model="charter.roughCost" inputmode="decimal" :pattern="moneyPattern"></label>
            <label>粗估人天<input v-model="charter.roughEffort" inputmode="decimal" :pattern="effortPattern"></label>
            <label>预计工期（天）<input v-model.number="charter.roughDuration" type="number" min="1" max="3650" step="1"></label>
          </div>
          <div class="proposal-risks">
            <strong>主要风险</strong>
            <div v-for="(risk,index) in charter.topRisks" :key="index" class="risk-fields">
              <label>风险描述<textarea v-model="risk.statement" maxlength="3000" required rows="2"/></label>
              <label>应对措施<textarea v-model="risk.response" maxlength="3000" required rows="2"/></label>
              <el-button text :disabled="locked||stale||reading" @click="charter.topRisks.splice(index,1)">移除风险</el-button>
            </div>
            <el-button :disabled="locked||stale||reading||charter.topRisks.length>=100" @click="charter.topRisks.push({statement:'',response:''})">添加风险</el-button>
          </div>
        </template>
        <label>变更原因<textarea v-model="reason" rows="3" minlength="3" maxlength="2000" required/></label>
        <p v-if="delta.missing" class="proposal-note">已登记的字段不能清空，请填写变更后的内容。</p>
      </fieldset>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
      <div class="actions">
        <el-button :disabled="locked" @click="close">取消</el-button>
        <el-button v-if="retry" :loading="busy" @click="send(retry)"><RotateCcw :size="15"/>重试上次提交</el-button>
        <el-button v-else type="primary" native-type="submit" :loading="busy" :disabled="reading||stale||!delta.changed||delta.missing||reason.trim().length<3">
          <Send :size="15"/>提交正式变更申请
        </el-button>
      </div>
    </form>
  </el-dialog>
</template>

<style scoped>
.proposal-note, .proposal-target p {margin:0;color:var(--pmo-muted);font-size:13px}
.proposal-target {display:grid;gap:10px;justify-items:start}
.proposal-target label {width:100%}
fieldset {margin:0;padding:12px 0 0;min-width:0;border:0;border-top:1px solid var(--pmo-line);display:grid;gap:16px}
legend {padding:0 8px 0 0;font-size:16px;font-weight:600}
legend span {margin-left:8px;color:var(--pmo-muted);font-size:13px;font-weight:400}
.key-field {display:flex;align-items:center;gap:6px}
.key-field input {width:auto}
.estimate-fields,.risk-fields {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.risk-fields {grid-template-columns:repeat(2,minmax(0,1fr)) auto;align-items:end}
.proposal-risks {display:grid;gap:10px;justify-items:start}
@media(max-width:600px){.estimate-fields,.risk-fields {grid-template-columns:1fr}}
</style>
