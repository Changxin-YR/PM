<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Plus,Pencil,Search} from 'lucide-vue-next'
import RequirementAction from './RequirementAction.vue'
import RequirementHistory from './RequirementHistory.vue'
import RequirementDisputes from './RequirementDisputes.vue'
const actions=ref<string[]>([]),statuses:Record<string,string>={baseline:'基线',delivered:'已交付',accepted:'已验收',customer_confirmed:'客户已确认',cancelled:'已取消'}
type Priority='P0'|'P1'|'P2'
interface Requirement {requirementId:string;desc:string;priority:Priority;acceptanceCriteria:string;plannedConfirmAt?:string;revision:number;baselineVersion:number;status:string}
const props=defineProps<{projectId:string;stage:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/requirements`,rows=ref<Requirement[]>([]),page=ref(1),total=ref(0),query=ref(''),filter=ref('')
const loading=ref(false),saving=ref(false),form=ref(false),error=ref(''),editing=ref<Requirement|null>(null)
const desc=ref(''),criteria=ref(''),priority=ref<Priority>('P1'),date=ref('');let attempt='',signature='',seq=0
function message(e:unknown){return e instanceof Error?e.message:'操作失败'}
async function read(){const id=++seq;loading.value=true;error.value='';try{const params=new URLSearchParams({page:String(page.value),pageSize:'10','filters[keyword]':filter.value});const r=await props.api(path+'?'+params);if(id===seq){rows.value=r.data.items;total.value=r.data.total;actions.value=r.data.deliveryActions??[]}}catch(e){if(id===seq){rows.value=[];error.value=message(e)}}finally{if(id===seq)loading.value=false}}
function search(){page.value=1;filter.value=query.value.trim();void read()}
function begin(row:Requirement|null){editing.value=row;desc.value=row?.desc||'';criteria.value=row?.acceptanceCriteria||'';priority.value=row?.priority||'P1';date.value=row?.plannedConfirmAt||'';error.value='';form.value=true;attempt=''}
async function save(){saving.value=true;error.value='';try{const row=editing.value;if(row?.plannedConfirmAt&&!date.value)throw Error('已有计划确认日期不能在草稿编辑中清空。');const fields={desc:desc.value.trim(),priority:priority.value,acceptanceCriteria:criteria.value.trim(),...(date.value?{plannedConfirmAt:date.value}:{})};const body=JSON.stringify(row?{fieldsRequirement:fields}:fields),sig=body+':'+(row?.revision||0);if(!attempt||sig!==signature){attempt=crypto.randomUUID();signature=sig}
await props.api(row?'requirements/'+row.requirementId:path,{method:row?'PATCH':'POST',headers:{'Idempotency-Key':attempt,...(row?{'If-Match':String(row.revision)}:{})},body});form.value=false;await read()}catch(e){error.value=message(e)}finally{saving.value=false}}
onMounted(read)
</script>
<template><section class="panel requirement-panel">
<form class="toolbar" @submit.prevent="search"><label class="search"><Search :size="16" aria-hidden="true"/><input aria-label="搜索需求" placeholder="搜索需求" v-model="query"></label><el-button native-type="submit">查询</el-button><el-button v-if="['drafting','designing'].includes(stage)" type="primary" :disabled="form" @click="begin(null)"><Plus :size="15"/>新增需求</el-button></form>
<el-alert v-if="error" :title="error" type="error" :closable="false"/>
<form v-if="form" class="requirement-form" @submit.prevent="save"><label>需求描述<textarea v-model="desc" minlength="5" maxlength="4000" required rows="2"></textarea></label><label>验收标准<textarea v-model="criteria" minlength="5" maxlength="4000" required rows="2"></textarea></label><div class="fields"><label>优先级<select v-model="priority" aria-label="优先级"><option>P0</option><option>P1</option><option>P2</option></select></label><label>计划确认日期<input type="date" v-model="date"></label></div><div class="actions"><el-button @click="form=false">取消</el-button><el-button type="primary" :loading="saving" native-type="submit">保存需求</el-button></div></form>
<el-table v-loading="loading" :data="rows" row-key="requirementId" empty-text="暂无匹配需求">
  <el-table-column type="expand" width="44">
    <template #default="{row}">
      <div class="requirement-detail">
        <dl><dt>验收标准</dt><dd>{{row.acceptanceCriteria}}</dd><dt>计划确认日期</dt><dd>{{row.plannedConfirmAt||'未填报'}}</dd><dt>需求编号</dt><dd>{{row.requirementId}}</dd><dt>记录版本</dt><dd>{{row.revision}}</dd></dl>
        <RequirementDisputes v-if="row.baselineVersion" :api="api" :id="row.requirementId" :description="row.desc" @saved="read"/>
      </div>
    </template>
  </el-table-column>
  <el-table-column label="优先级" width="96">
    <template #default="{row}"><el-tag size="small" :type="row.priority==='P0'?'warning':row.priority==='P1'?'primary':'info'">{{row.priority}}</el-tag></template>
  </el-table-column>
  <el-table-column label="需求描述" min-width="210">
    <template #default="{row}"><span class="requirement-description">{{row.desc}}</span></template>
  </el-table-column>
  <el-table-column label="基线" width="88">
    <template #default="{row}"><el-tag size="small" type="info">{{row.baselineVersion?'V'+row.baselineVersion:'草稿'}}</el-tag></template>
  </el-table-column>
  <el-table-column label="状态" width="120">
    <template #default="{row}"><el-tag size="small" :type="row.status==='customer_confirmed'&&row.baselineVersion?'success':'info'">{{row.baselineVersion?statuses[row.status]:'草稿'}}</el-tag></template>
  </el-table-column>
  <el-table-column label="操作" width="142">
    <template #default="{row}">
      <div class="requirement-actions">
        <RequirementHistory :api="api" :id="row.requirementId"/>
        <RequirementAction v-if="row.baselineVersion" :row="row" :api="api" :project-id="projectId" :actions="actions" @saved="read"/>
        <el-button v-if="!row.baselineVersion&&['drafting','designing'].includes(stage)" text title="编辑需求" aria-label="编辑需求" @click="begin(row)"><Pencil :size="15"/></el-button>
      </div>
    </template>
  </el-table-column>
</el-table>
<el-pagination v-model:current-page="page" :total="total" :page-size="10" layout="prev, pager, next" @current-change="read"/>
</section></template>
<style scoped>
.requirement-form {display:grid;gap:12px;padding:16px;margin-bottom:16px;background:var(--pmo-bg);border:1px solid var(--pmo-line);border-radius:8px}
.fields {display:grid;gap:12px;grid-template-columns:repeat(2,minmax(0,1fr))}
.requirement-detail {padding:12px 20px;min-width:0}
dl {display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 16px;margin:0 0 8px}
dt {color:var(--pmo-secondary)}
dd,.requirement-description {margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
.toolbar {flex-wrap:wrap}
.requirement-actions {display:flex;align-items:center;gap:0;flex-wrap:wrap}
.requirement-actions :deep(.el-button) {padding-inline:8px}
@media (max-width:580px) {.fields {grid-template-columns:1fr}}
</style>
