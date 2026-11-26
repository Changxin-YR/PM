<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Pencil,Plus,RefreshCw,Save,ShieldCheck,TriangleAlert} from 'lucide-vue-next'
import RiskResolution from './RiskResolution.vue'
type Risk={riskId:string;statement:string;prob:number;impact:number;response:string;isBlocking:boolean;blockedSince?:string;ownerId?:string;status:string;revision:number;canEdit:boolean;canEscalate?:boolean}
const props=defineProps<{projectId:string;api:(p:string,o?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/risks`,items=ref<Risk[]>([]),canCreate=ref(false),loading=ref(false),busy=ref(false),page=ref(1),total=ref(0),error=ref('')
const form=ref(false),edit=ref<Risk|null>(null),statement=ref(''),prob=ref(3),impact=ref(3),response=ref(''),blocking=ref(false)
const escalation=ref<Risk|null>(null),escalationReason=ref('')
const resolutionId=ref(''),resolutionOpen=ref(false)
const statuses:Record<string,string>={open:'未关闭',mitigated:'已缓解',closed:'已关闭',transferred:'已移交'}
let seq=0,key='',signature=''
function shanghai(value:string){
  return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
}
async function load(n=page.value){const id=++seq;loading.value=true;error.value='';try{const {data}=await props.api(`${path}?page=${n}`);if(id===seq){items.value=data.items;canCreate.value=data.canCreate;total.value=data.total;page.value=n}}
 catch(e){if(id===seq){error.value=String(e);canCreate.value=false;items.value=[]}}finally{if(id===seq)loading.value=false}}
function open(row:Risk|null){edit.value=row;statement.value=row?.statement??'';prob.value=row?.prob??3;impact.value=row?.impact??3;response.value=row?.response??'';blocking.value=row?.isBlocking??false;key='';form.value=true;error.value=''}
function showResolution(row:Risk){resolutionId.value=row.riskId;resolutionOpen.value=true}
async function save(){busy.value=true;error.value='';try{const row=edit.value,fields={statement:statement.value.trim(),prob:prob.value,impact:impact.value,response:response.value.trim()}
 const body=JSON.stringify(row?{fieldsRisk:fields}:{...fields,isBlocking:blocking.value}),sig=body+':'+(row?.revision??0)
 if(!key||sig!==signature){key=crypto.randomUUID();signature=sig}
 await props.api(row?`risks/${row.riskId}`:path,{method:row?'PATCH':'POST',body,headers:{'Idempotency-Key':key,...(row?{'If-Match':String(row.revision)}:{})}});form.value=false;await load(row?page.value:1)
 }catch(e){error.value=String(e)}finally{busy.value=false}}
function openEscalation(row:Risk){
  escalation.value=row;escalationReason.value='';error.value='';key=''
}
async function escalate(){
  const row=escalation.value
  const reason=escalationReason.value.trim()
  if(!row||busy.value||reason.length<4)return
  const body=JSON.stringify({reason}),sig=body+':'+row.revision
  if(!key||sig!==signature){key=crypto.randomUUID();signature=sig}
  busy.value=true;error.value=''
  try{
    await props.api(`risks/${row.riskId}/escalate`,{method:'POST',body,headers:{'Idempotency-Key':key,'If-Match':String(row.revision)}})
    escalation.value=null
    await load(1)
  }catch(e){error.value=String(e)}finally{busy.value=false}
}
onMounted(()=>load())
</script>
<template><section class="panel risk-panel"><div class="toolbar"><span>项目风险 · {{total}}</span><el-button v-if="canCreate" :disabled="form||loading||!!escalation" @click="open(null)"><Plus :size="15"/>登记风险</el-button><el-button aria-label="刷新风险" title="刷新风险" :disabled="busy" @click="load()"><RefreshCw :size="15"/></el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/><form v-if="form" @submit.prevent="save">
<label>风险描述<textarea v-model="statement" minlength="5" maxlength="3000" required/></label>
<div class="ratings"><label>概率<input type="number" min="1" max="5" step="1" v-model.number="prob" required></label><label>影响<input type="number" min="1" max="5" step="1" v-model.number="impact" required></label></div>
<label>应对措施<textarea v-model="response" maxlength="3000"/></label><label v-if="!edit" class="flag"><input type="checkbox" v-model="blocking">阻塞风险</label>
<div class="actions"><el-button @click="form=false">取消</el-button><el-button native-type="submit" type="primary" :loading="busy"><Save :size="15"/>保存风险</el-button></div></form>
<form v-if="escalation" aria-label="升级阻塞风险" @submit.prevent="escalate">
  <p>{{escalation.statement}}</p>
  <label>升级原因<textarea v-model="escalationReason" minlength="4" maxlength="2000" required/></label>
  <div class="actions"><el-button :disabled="busy" @click="escalation=null">取消</el-button>
    <el-button native-type="submit" type="warning" :loading="busy" :disabled="escalationReason.trim().length<4">确认升级为阻塞</el-button></div>
</form>
<el-table :data="items" v-loading="loading" row-key="riskId" empty-text="暂无已登记风险"><el-table-column label="风险" min-width="170"><template #default="{row}">{{row.statement}} <el-tag size="small" type="danger" v-if="row.isBlocking">阻塞</el-tag></template></el-table-column>
<el-table-column prop="prob" label="概率" width="62"/><el-table-column prop="impact" label="影响" width="62"/><el-table-column prop="response" label="应对措施" min-width="140"/><el-table-column label="状态" width="74"><template #default="{row}">{{statuses[row.status]??row.status}}</template></el-table-column>
<el-table-column label="负责人 ID" min-width="156">
  <template #default="{row}"><code v-if="row.ownerId">{{row.ownerId}}</code><span v-else class="risk-meta">未登记</span></template>
</el-table-column>
<el-table-column label="阻塞起始（北京时间）" min-width="155">
  <template #default="{row}">
    <time v-if="row.blockedSince" :datetime="row.blockedSince">{{shanghai(row.blockedSince)}}</time>
    <span v-else class="risk-meta">{{row.isBlocking?'未记录':'—'}}</span>
  </template>
</el-table-column>
<el-table-column label="操作" width="132"><template #default="{row}"><div class="risk-actions">
  <el-button v-if="row.canEdit" text aria-label="编辑风险" title="编辑风险" :disabled="busy||!!escalation" @click="open(row)"><Pencil :size="15"/></el-button>
  <el-button v-if="row.canEscalate" text aria-label="升级为阻塞" title="升级为阻塞" :disabled="form||busy||!!escalation" @click="openEscalation(row)"><TriangleAlert :size="15"/></el-button>
  <el-button text aria-label="风险处置" title="风险处置" :disabled="form||busy||!!escalation" @click="showResolution(row)"><ShieldCheck :size="15"/></el-button>
</div></template></el-table-column></el-table>
<el-pagination v-if="total>20" :current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="load"/>
<RiskResolution v-if="resolutionId" :key="resolutionId" v-model="resolutionOpen" :risk-id="resolutionId" :api="api" @saved="load()"/>
</section></template>
<style scoped>form{display:grid;gap:12px;padding:16px;margin:0 0 16px;background:var(--pmo-bg);border:1px solid var(--pmo-line);border-radius:8px}.ratings{display:flex;gap:12px}.ratings label{width:6rem}.flag{display:flex;align-items:center;gap:6px}.flag input{width:auto}.risk-meta{color:var(--pmo-muted)}.risk-actions{display:flex;align-items:center;gap:2px}.risk-actions :deep(.el-button){padding:7px;margin-left:0}</style>
