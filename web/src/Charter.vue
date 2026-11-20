<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {Plus,RefreshCw,Save,Trash2} from 'lucide-vue-next'

const props=defineProps<{projectId:string;stage:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const textFields=[['valueProp','价值主张'],['targetClient','目标客户'],['attractiveness','客户或市场吸引力'],['expectedBenefit','预期收益'],['successCriteria','成功标准']] as const
type TextField=typeof textFields[number][0]
type Risk={statement:string;response:string}
type Card=Partial<Record<TextField,string>> & {roughCost?:string;roughEffort?:string;roughDurationDays?:number;topRisks?:Risk[];isFrozen?:boolean}
const path=`projects/${props.projectId}/charter`,card=ref<Card>({}),base=ref<Card>({}),revision=ref(0),exists=ref(false)
const error=ref(''),notice=ref(''),busy=ref(false),conflict=ref(false),dirty=computed(()=>JSON.stringify(base.value)!==JSON.stringify(card.value))
const readonly=computed(()=>busy.value||!!base.value.isFrozen||props.stage!=='drafting'||!revision.value)
let attempt='',signature=''
async function read(){busy.value=true;error.value='';try{const {data}=await props.api(path);const source=data.charter||{};base.value=structuredClone(source);card.value=structuredClone(source);revision.value=data.expectedRevision;exists.value=!!data.charter;conflict.value=false;notice.value=''}catch(e){error.value=e instanceof Error?e.message:'读取失败'}finally{busy.value=false}}
async function save(){busy.value=true;error.value='';notice.value='';try{
 const fields:Record<string,unknown>={};for(const key of [...textFields.map(f=>f[0]),'roughCost','roughEffort','roughDurationDays','topRisks'] as const){const current=card.value[key];if(JSON.stringify(current)===JSON.stringify(base.value[key]))continue
 if(current==null||current===''||(Array.isArray(current)&&current.length===0)){if(base.value[key]!=null)throw Error('已保存的字段不能清空。');continue}
 fields[key==='roughDurationDays'?'roughDuration':key]=current}
 if(Object.keys(fields).length===0)return
 const body=JSON.stringify({fieldsCharter:fields}),sig=`${revision.value}:${body}`;if(!attempt||sig!==signature){attempt=crypto.randomUUID();signature=sig}
 await props.api(path,{method:'PUT',headers:{'If-Match':String(revision.value),'Idempotency-Key':attempt},body});await read();if(!error.value)notice.value='已保存'
 }catch(e){error.value=e instanceof Error?e.message:'保存失败';conflict.value=error.value.includes('版本已变化')}finally{busy.value=false}}
function addRisk(){(card.value.topRisks??=[]).push({statement:'',response:''})}
onMounted(read)
</script>
<template><div class="bar"><strong>立项卡</strong><span>{{base.isFrozen?'已冻结':exists?'草稿':'未填写'}} · 版本 {{revision||'—'}}</span><el-button text title="重新读取立项卡" aria-label="重新读取立项卡" :disabled="busy||dirty" @click="read"><RefreshCw :size="16"/></el-button></div>
<el-alert v-if="error" :title="error" type="error" :closable="false"/><el-button v-if="conflict" @click="read">放弃本次修改并读取新版</el-button>
<form @submit.prevent="save"><fieldset :disabled="readonly"><div class="narratives"><label v-for="[key,label] in textFields" :key="key">{{label}}<textarea v-model="card[key]" :maxlength="key==='targetClient'?2000:3000" rows="2"></textarea></label></div>
<div class="numbers"><label>粗估金额（元）<input v-model="card.roughCost" inputmode="decimal" pattern="(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?"></label><label>粗估人天<input v-model="card.roughEffort" inputmode="decimal" pattern="(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?"></label><label>预计工期（天）<input v-model.number="card.roughDurationDays" type="number" min="1" max="3650" step="1"></label></div>
<div class="bar"><strong>主要风险</strong><el-button text :disabled="readonly" @click="addRisk"><Plus :size="15"/>添加风险</el-button></div>
<div v-for="(risk,index) in card.topRisks" :key="index" class="risk"><label>风险描述<input v-model="risk.statement" required maxlength="3000"></label><label>应对措施<input v-model="risk.response" required maxlength="3000"></label><el-button text :disabled="readonly" title="移除风险项" aria-label="移除风险项" @click="card.topRisks?.splice(index,1)"><Trash2 :size="15"/></el-button></div>
</fieldset><div class="actions"><span role="status">{{notice}}</span><el-button type="primary" native-type="submit" :disabled="readonly||!dirty||conflict"><Save :size="15"/>保存立项卡</el-button></div></form></template>
<style scoped>fieldset{border:0;margin:0;padding:0;min-width:0}.bar,.risk{display:flex;align-items:center;gap:12px;margin:14px 0}.bar span,.actions span{font-size:13px;color:#5e6a63}.narratives{display:grid;gap:12px}.numbers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}.risk label{flex:1;min-width:0}@media(max-width:560px){.numbers{grid-template-columns:1fr}.risk{flex-wrap:wrap}.risk label{flex-basis:100%}}</style>
