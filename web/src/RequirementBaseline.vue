<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {RefreshCw,Signature} from 'lucide-vue-next'
import RequirementMetrics from './RequirementMetrics.vue'
type Line={requirementId:string;revision:number;priority:string;desc:string;acceptanceCriteria:string;plannedConfirmAt?:string}
type State={projectId:string;revision:number;contentHash:string;items:Line[];status:'draft'|'awaiting_customer'|'frozen';canSignAs?:'pm'|'client_representative';pmSignedAt?:string;customerSignedAt?:string}
const props=defineProps<{projectId:string;api:(p:string,o?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/requirement-baseline`,data=ref<State|null>(null),busy=ref(false),error=ref(''),reason=ref(''),reviewed=ref(false)
let key='',signature=''
async function load(){busy.value=true;error.value='';try{data.value=(await props.api(path)).data;reason.value='';reviewed.value=false;key=''}catch(e){error.value=String(e);data.value=null}finally{busy.value=false}}
async function sign(){const d=data.value;if(!d?.canSignAs||!reviewed.value)return;busy.value=true;error.value='';try{const body=JSON.stringify({role:d.canSignAs,contentHash:d.contentHash,reason:reason.value.trim()}),sig=body+d.revision;if(!key||sig!==signature){key=crypto.randomUUID();signature=sig}
 data.value=(await props.api(path+'/sign',{method:'POST',headers:{'If-Match':String(d.revision),'Idempotency-Key':key},body})).data;reason.value='';reviewed.value=false
 }catch(e){error.value=String(e)}finally{busy.value=false}}
onMounted(load)
</script>
<template><section class="panel baseline-panel"><div class="toolbar"><h3>初始需求基线</h3><el-tag v-if="data">{{{draft:'草稿',awaiting_customer:'待客户确认',frozen:'已冻结'}[data.status]}}</el-tag><el-button class="baseline-reload" aria-label="重读基线" title="重读基线" :disabled="busy" @click="load"><RefreshCw :size="15"/></el-button></div><el-alert v-if="error" type="error" :title="error" :closable="false"/>
<template v-if="data"><el-alert v-if="data.status!=='frozen'&&data.items.some(i=>!i.plannedConfirmAt)" type="warning" title="部分需求缺少计划确认日，暂不能签署。" :closable="false"/>
<dl v-if="data.pmSignedAt||data.customerSignedAt" class="baseline-signatures">
  <div v-if="data.pmSignedAt"><dt>PM 确认</dt><dd><time :datetime="data.pmSignedAt">{{new Date(data.pmSignedAt).toLocaleString('zh-CN')}}</time></dd></div>
  <div v-if="data.customerSignedAt"><dt>客户确认</dt><dd><time :datetime="data.customerSignedAt">{{new Date(data.customerSignedAt).toLocaleString('zh-CN')}}</time></dd></div>
</dl>
<el-table :data="data.items" row-key="requirementId" empty-text="暂无待确认需求"><el-table-column prop="priority" label="优先级" width="72"/><el-table-column prop="desc" label="需求" min-width="150"/><el-table-column prop="acceptanceCriteria" label="验收标准" min-width="155"/><el-table-column prop="plannedConfirmAt" label="计划确认日" width="115"/><el-table-column prop="revision" label="版本" width="63"/></el-table>
<form v-if="data.canSignAs" @submit.prevent="sign"><label>确认说明<textarea v-model="reason" minlength="4" maxlength="2000" required/></label><label class="flag"><input type="checkbox" v-model="reviewed" required>已核对本版本的全部需求和验收标准</label><div class="actions"><el-button type="primary" native-type="submit" :disabled="!reviewed||reason.trim().length<4" :loading="busy"><Signature :size="16"/>{{data.canSignAs==='pm'?'PM 确认基线':'客户确认并冻结基线'}}</el-button></div></form></template></section>
<RequirementMetrics :key="data?.revision" :project-id="projectId" :api="api"/>
</template>
<style scoped>
.baseline-reload{margin-inline-start:auto}
.baseline-signatures{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,14rem),1fr));gap:8px 16px;margin:12px 0}
.baseline-signatures dt{color:var(--pmo-muted);font-size:13px}
.baseline-signatures dd{margin:0}
form{display:grid;gap:10px;margin-top:12px}
.flag{display:flex;align-items:center;gap:8px}
.flag input{width:auto}
</style>
