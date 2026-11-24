<script setup lang="ts">
import {watch,ref,computed,reactive} from 'vue'
import {FilePlus,Plus,RefreshCw,Send,X} from 'lucide-vue-next'
import ChangeDecision from './ChangeDecision.vue'
import FormalChangeProposal from './FormalChangeProposal.vue'
const decidable=ref<string[]>([]),approvable=ref<string[]>([]),policyPending=ref(true)
const props=defineProps<{projectId:string;focusChangeId?:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{clearFocus:[]}>()
type Req={requirementId:string;desc:string;priority:string;revision:number;baselineVersion:number;status:string}
type Item={changeId:string;targetType:string;targetId:string;baseRevision:number;revision:number;status:string;reason:string;before:unknown;after:unknown;legacySnapshot?:true}
const items=ref<Item[]>([]),reqs=ref<Req[]>([]),can=ref(false),busy=ref(false),form=ref(false),error=ref(''),notice=ref(''),page=ref(1),total=ref(0),reqPage=ref(1),reqTotal=ref(0),rid=ref(''),priority=ref(''),reason=ref('')
const additionRevision=ref<number|null>(null),additionOpen=ref(false)
const formalOpen=ref(false)
const addition=reactive({desc:'',priority:'',acceptanceCriteria:'',plannedConfirmAt:'',reason:''})
const path=`projects/${props.projectId}/changes`,selected=computed(()=>reqs.value.find(i=>i.requirementId===rid.value)),labels:Record<string,string>={pending:'待审批',applied:'已生效',approved:'已批准',rejected:'已拒绝',withdrawn:'已撤回',draft:'草稿'},kinds:Record<string,string>={requirement:'需求',milestone:'里程碑',budget:'预算',charter:'立项卡'}
let key='',last='',readId=0
async function load(n:number){const id=++readId,params=new URLSearchParams({page:String(n)});if(props.focusChangeId)params.set('changeId',props.focusChangeId);const r=await props.api(`${path}?${params}`);if(id!==readId)return;items.value=r.data.items;total.value=r.data.total;can.value=r.data.canPropose;decidable.value=r.data.decidableIds;approvable.value=r.data.approvableIds;policyPending.value=r.data.policyPending;additionRevision.value=r.data.additionProjectRevision??null;page.value=n}
async function refresh(n=page.value){busy.value=true;error.value='';const id=readId+1;try{await load(n)}catch(e){if(id===readId){error.value=String(e);can.value=false;additionRevision.value=null;items.value=[]}}finally{if(id===readId)busy.value=false}}
async function requirements(n:number){busy.value=true;error.value='';rid.value='';priority.value='';reqs.value=[];try{const r=await props.api(`projects/${props.projectId}/requirements?page=${n}&pageSize=20`);reqs.value=r.data.items.filter((i:Req)=>i.baselineVersion>0&&i.status!=='cancelled');reqTotal.value=r.data.total;reqPage.value=n}catch(e){error.value=String(e)}finally{busy.value=false}}
async function begin(){form.value=true;reason.value='';notice.value='';key='';await requirements(1)}
async function save(){const row=selected.value;if(!row)return;busy.value=true;error.value='';try{const body=JSON.stringify({targetType:'requirement',targetId:row.requirementId,before:{priority:row.priority},after:{priority:priority.value},reason:reason.value.trim()});const sig=body+row.revision;if(!key||sig!==last){key=crypto.randomUUID();last=sig}await props.api(path,{method:'POST',headers:{'If-Match':String(row.revision),'Idempotency-Key':key},body});form.value=false;notice.value='变更申请已提交';await load(1)}catch(e){error.value=String(e)}finally{busy.value=false}}
function beginAddition(){
  Object.assign(addition,{desc:'',priority:'',acceptanceCriteria:'',plannedConfirmAt:'',reason:''})
  key='';error.value='';notice.value='';additionOpen.value=true
}
async function saveAddition(){
  const revision=additionRevision.value
  if(busy.value||revision===null)return
  const body=JSON.stringify(addition),sig=JSON.stringify([revision,body])
  if(!key||sig!==last){key=crypto.randomUUID();last=sig}
  busy.value=true;error.value=''
  try{
    await props.api(`${path}/requirement-additions`,{method:'POST',headers:{'If-Match':String(revision),'Idempotency-Key':key},body})
    additionOpen.value=false;notice.value='新增需求已送审';await load(1)
  }catch(e){error.value=String(e)}finally{busy.value=false}
}
function formalSaved(){
  formalOpen.value=false;notice.value='正式变更申请已提交';void refresh(1)
}
watch(()=>props.focusChangeId,()=>{items.value=[];form.value=additionOpen.value=false;void refresh(1)},{immediate:true})
</script>
<template><section class="panel change-panel"><div class="toolbar"><span>{{focusChangeId?'定位变更':'变更记录'}} · {{total}}</span><el-button v-if="focusChangeId" :disabled="busy" @click="emit('clearFocus')">全部变更</el-button><el-button v-if="!focusChangeId&&additionRevision!==null" :disabled="busy||form||additionOpen||formalOpen" @click="beginAddition"><FilePlus :size="15"/>申请新增需求</el-button><el-button v-if="!focusChangeId&&can" :disabled="busy||form||additionOpen||formalOpen" @click="begin"><Plus :size="15"/>调整需求优先级</el-button><el-button v-if="!focusChangeId&&can" :disabled="busy||form||additionOpen||formalOpen" @click="formalOpen=true"><FilePlus :size="15"/>申请正式变更</el-button><el-button aria-label="刷新变更" title="刷新变更" :disabled="busy||form||additionOpen||formalOpen" @click="refresh()"><RefreshCw :size="15"/></el-button></div><span role="status">{{notice}}</span><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-alert v-else-if="policyPending&&items.some(item=>item.status==='pending')" title="变更裁决策略待签核" type="warning" :closable="false"/>
<FormalChangeProposal v-if="formalOpen" :project-id="projectId" :api="api" @close="formalOpen=false" @saved="formalSaved"/>
<form v-if="additionOpen" aria-label="新增需求申请" @submit.prevent="saveAddition">
  <label>需求说明<textarea v-model="addition.desc" minlength="5" maxlength="4000" required :disabled="busy"/></label>
  <div class="addition-fields">
    <label>优先级<select v-model="addition.priority" required :disabled="busy"><option value="" disabled>请选择</option><option v-for="p in ['P0','P1','P2']" :key="p">{{p}}</option></select></label>
    <label>计划确认日期<input v-model="addition.plannedConfirmAt" type="date" required :disabled="busy"/></label>
  </div>
  <label>验收标准<textarea v-model="addition.acceptanceCriteria" minlength="5" maxlength="4000" required :disabled="busy"/></label>
  <label>新增原因<textarea v-model="addition.reason" minlength="3" maxlength="2000" required :disabled="busy"/></label>
  <div class="actions"><el-button :disabled="busy" @click="additionOpen=false"><X :size="15"/>取消</el-button>
    <el-button type="primary" native-type="submit" :loading="busy"><Send :size="15"/>送审新增需求</el-button></div>
</form>
<form v-if="form" @submit.prevent="save"><label>基线需求<select v-model="rid" @change="priority=''" required><option value="" disabled>请选择需求</option><option v-for="r in reqs" :key="r.requirementId" :value="r.requirementId">{{r.desc}} · {{r.priority}}</option></select></label><el-pagination v-if="reqTotal>20" :current-page="reqPage" :page-size="20" :total="reqTotal" layout="prev,pager,next" :disabled="busy" @current-change="requirements"/><label>申请优先级<select v-model="priority" required><option disabled value="">请选择</option><option v-for="p in ['P0','P1','P2']" :key="p" :value="p" :disabled="p===selected?.priority">{{p}}</option></select></label><label>变更原因<textarea v-model="reason" minlength="3" maxlength="2000" required/></label><div class="actions"><el-button :disabled="busy" @click="form=false">取消</el-button><el-button native-type="submit" type="primary" :loading="busy" :disabled="!selected||!priority||reason.trim().length<3">提交变更申请</el-button></div></form>
<el-table :data="items" v-loading="busy" row-key="changeId" empty-text="暂无变更记录"><el-table-column type="expand"><template #default="{row}">
  <p v-if="row.legacySnapshot" class="legacy">旧快照 · 非现行格式</p><dl><dt>目标编号</dt><dd>{{row.targetId}}</dd><dt>基准版本</dt><dd>{{row.baseRevision}}</dd><dt>修改前</dt><dd><pre>{{JSON.stringify(row.before,null,2)}}</pre></dd><dt>修改后</dt><dd><pre>{{JSON.stringify(row.after,null,2)}}</pre></dd><template v-if="row.decidedBy"><dt>裁决人</dt><dd>{{row.decidedBy}}</dd><dt>裁决理由</dt><dd>{{row.decisionReason}}</dd></template></dl>
</template></el-table-column><el-table-column label="资料" width="85"><template #default="{row}">{{kinds[row.targetType]}}</template></el-table-column><el-table-column prop="reason" label="变更原因"/><el-table-column label="状态" width="90"><template #default="{row}">{{labels[row.status]??row.status}}</template></el-table-column><el-table-column label="审批" width="82"><template #default="{row}"><ChangeDecision v-if="decidable.includes(row.changeId)" :row="row" :api="api" :can-approve="approvable.includes(row.changeId)" @saved="refresh()"/></template></el-table-column></el-table><el-pagination v-if="total>20" :current-page="page" :page-size="20" :total="total" layout="prev,pager,next" :disabled="busy||form" @current-change="refresh"/></section></template>
<style scoped>form{display:grid;gap:12px;padding:16px;margin:0 0 16px;background:var(--pmo-bg);border:1px solid var(--pmo-line);border-radius:8px}dl,.legacy{padding:0 1rem}dd,pre{margin:0 0 .5rem;overflow-wrap:anywhere;white-space:pre-wrap}.addition-fields{display:grid;gap:8px;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr))}</style>
