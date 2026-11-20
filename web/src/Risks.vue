<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Pencil,Plus,RefreshCw,Save} from 'lucide-vue-next'
type Risk={riskId:string;statement:string;prob:number;impact:number;response:string;isBlocking:boolean;blockedSince?:string;ownerId?:string;status:string;revision:number;canEdit:boolean}
const props=defineProps<{projectId:string;api:(p:string,o?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/risks`,items=ref<Risk[]>([]),canCreate=ref(false),loading=ref(false),busy=ref(false),page=ref(1),total=ref(0),error=ref('')
const form=ref(false),edit=ref<Risk|null>(null),statement=ref(''),prob=ref(3),impact=ref(3),response=ref(''),blocking=ref(false)
let seq=0,key='',signature=''
async function load(n=page.value){const id=++seq;loading.value=true;error.value='';try{const {data}=await props.api(`${path}?page=${n}`);if(id===seq){items.value=data.items;canCreate.value=data.canCreate;total.value=data.total;page.value=n}}
 catch(e){if(id===seq){error.value=String(e);canCreate.value=false;items.value=[]}}finally{if(id===seq)loading.value=false}}
function open(row:Risk|null){edit.value=row;statement.value=row?.statement??'';prob.value=row?.prob??3;impact.value=row?.impact??3;response.value=row?.response??'';blocking.value=row?.isBlocking??false;key='';form.value=true;error.value=''}
async function save(){busy.value=true;error.value='';try{const row=edit.value,fields={statement:statement.value.trim(),prob:prob.value,impact:impact.value,response:response.value.trim()}
 const body=JSON.stringify(row?{fieldsRisk:fields}:{...fields,isBlocking:blocking.value}),sig=body+':'+(row?.revision??0)
 if(!key||sig!==signature){key=crypto.randomUUID();signature=sig}
 await props.api(row?`risks/${row.riskId}`:path,{method:row?'PATCH':'POST',body,headers:{'Idempotency-Key':key,...(row?{'If-Match':String(row.revision)}:{})}});form.value=false;await load(row?page.value:1)
 }catch(e){error.value=String(e)}finally{busy.value=false}}
onMounted(()=>load())
</script>
<template><div class="toolbar"><span>项目风险 · {{total}}</span><el-button v-if="canCreate" :disabled="form||loading" @click="open(null)"><Plus :size="15"/>登记风险</el-button><el-button aria-label="刷新风险" title="刷新风险" :disabled="busy" @click="load()"><RefreshCw :size="15"/></el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/><form v-if="form" @submit.prevent="save">
<label>风险描述<textarea v-model="statement" minlength="5" maxlength="3000" required/></label>
<div class="ratings"><label>概率<input type="number" min="1" max="5" step="1" v-model.number="prob" required></label><label>影响<input type="number" min="1" max="5" step="1" v-model.number="impact" required></label></div>
<label>应对措施<textarea v-model="response" maxlength="3000"/></label><label v-if="!edit" class="flag"><input type="checkbox" v-model="blocking">阻塞风险</label>
<div class="actions"><el-button @click="form=false">取消</el-button><el-button native-type="submit" type="primary" :loading="busy"><Save :size="15"/>保存风险</el-button></div></form>
<el-table :data="items" v-loading="loading" row-key="riskId" empty-text="暂无已登记风险"><el-table-column label="风险" min-width="170"><template #default="{row}">{{row.statement}} <el-tag size="small" type="danger" v-if="row.isBlocking">阻塞</el-tag></template></el-table-column>
<el-table-column prop="prob" label="概率" width="62"/><el-table-column prop="impact" label="影响" width="62"/><el-table-column prop="response" label="应对措施" min-width="140"/><el-table-column label="状态" width="74"><template #default="{row}">{{row.status==='open'?'未关闭':row.status}}</template></el-table-column>
<el-table-column label="操作" width="65"><template #default="{row}"><el-button v-if="row.canEdit" text aria-label="编辑风险" title="编辑风险" @click="open(row)"><Pencil :size="15"/></el-button></template></el-table-column></el-table>
<el-pagination v-if="total>20" :current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="load"/></template>
<style scoped>form{display:grid;gap:10px;padding:12px 0}.ratings{display:flex;gap:12px}.ratings label{width:6rem}.flag{display:flex;align-items:center;gap:6px}.flag input{width:auto}</style>
