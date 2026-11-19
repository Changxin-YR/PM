<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Pencil,Plus,RefreshCw} from 'lucide-vue-next'
type Item={milestoneId:string;name:string;plannedDate:string;forecastDate?:string;actualDate?:string;isKey:boolean;revision:number}
const props=defineProps<{projectId:string;stage:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const items=ref<Item[]>([]),edit=ref<Item|null>(null),form=ref(false),busy=ref(false),loading=ref(false),error=ref('')
const name=ref(''),plan=ref(''),forecast=ref(''),keyMilestone=ref(false)
let key='',signature=''
const path=`projects/${props.projectId}/milestones`
async function read(){loading.value=true;error.value='';try{items.value=(await props.api(path)).data.items}catch(e){error.value=e instanceof Error?e.message:'读取失败'}finally{loading.value=false}}
function open(row:Item|null){edit.value=row;name.value=row?.name||'';plan.value=row?.plannedDate||'';forecast.value=row?.forecastDate||'';keyMilestone.value=row?.isKey||false;form.value=true;error.value='';key=''}
async function save(){busy.value=true;error.value='';try{
 const row=edit.value;if(row?.forecastDate&&!forecast.value)throw Error('已有预测日期不能在草稿编辑中清空。')
 const fields={name:name.value.trim(),plannedDate:plan.value,isKey:keyMilestone.value,...(forecast.value?{forecastDate:forecast.value}:{})}
 const body=JSON.stringify(row?{fieldsMilestone:fields}:fields),sig=body+':'+(row?.revision||0)
 if(!key||sig!==signature){key=crypto.randomUUID();signature=sig}
 await props.api(row?'milestones/'+row.milestoneId:path,{method:row?'PATCH':'POST',headers:{'Idempotency-Key':key,...(row?{'If-Match':String(row.revision)}:{})},body});form.value=false;await read()
 }catch(e){error.value=e instanceof Error?e.message:'未能保存'}finally{busy.value=false}}
onMounted(read)
</script>
<template><div class="toolbar"><span>有效里程碑 {{items.length}} / 8</span><el-button v-if="['drafting','designing'].includes(stage)" :disabled="items.length>=8 || form" @click="open(null)"><Plus :size="15"/>新增里程碑</el-button><el-button aria-label="刷新里程碑" title="刷新里程碑" @click="read"><RefreshCw :size="15"/></el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/>
<form v-if="form" class="milestone-form" @submit.prevent="save"><label>里程碑名称<input required maxlength="160" v-model="name"></label><label>计划日期<input required type="date" v-model="plan"></label><label>预测日期<input type="date" v-model="forecast"></label><label class="flag"><input type="checkbox" v-model="keyMilestone">关键里程碑</label><div class="actions"><el-button @click="form=false">取消</el-button><el-button native-type="submit" type="primary" :loading="busy">保存里程碑</el-button></div></form>
<el-table v-loading="loading" :data="items" row-key="milestoneId" empty-text="暂无有效里程碑"><el-table-column label="里程碑" min-width="140"><template #default="{row}">{{row.name}} <el-tag v-if="row.isKey" size="small">关键</el-tag></template></el-table-column><el-table-column prop="plannedDate" label="计划日期" width="118"/><el-table-column label="预测日期" width="118"><template #default="{row}">{{row.forecastDate||'未填报'}}</template></el-table-column><el-table-column label="实际日期" width="118"><template #default="{row}">{{row.actualDate||'未完成'}}</template></el-table-column><el-table-column label="操作" width="64"><template #default="{row}"><el-button v-if="['drafting','designing'].includes(stage)" text title="编辑里程碑" aria-label="编辑里程碑" @click="open(row)"><Pencil :size="15"/></el-button></template></el-table-column></el-table></template>
<style scoped>.milestone-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:14px 0}.flag{display:flex;align-items:center;gap:7px}.flag input{width:auto}.actions{grid-column:1/-1}</style>
