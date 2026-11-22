<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {KeyRound,Plus,RefreshCw,ShieldOff} from 'lucide-vue-next'
type Token={tokenId:string;fingerprint:string;allowedActions:string[];allowedProjectIds:string[];allowProjectCreate:boolean;expiresAt:string;state:string;revision:number}
const props=defineProps<{projects:{projectId:string;name:string}[];admin:boolean;initialOpen?:boolean;api:(path:string,options?:RequestInit,headers?:(h:Headers)=>void)=>Promise<any>}>()
const show=ref(false),org=ref(false),items=ref<Token[]>([]),actions=ref<string[]>([]),page=ref(1),total=ref(0),revision=ref(0)
const editing=ref(false),chosen=ref<string[]>([]),projects=ref<string[]>([]),hours=ref(1),target=ref<Token|null>(null),reason=ref('')
const secret=ref(''),notice=ref(''),error=ref(''),busy=ref(false),loading=ref(false)
let epoch=0,readId=0,last='',key='',ends='',boundHours=0
const stateNames:Record<string,string>={active:'有效',expired:'已过期',revoked:'已撤销',inactive:'停用'}
function base(){return org.value?'admin/agents/tokens':'me/agent-tokens'}
function reset(){epoch++;readId++;secret.value='';notice.value='';error.value='';editing.value=false;target.value=null;chosen.value=[];projects.value=[];items.value=[];actions.value=[];total.value=0;revision.value=0;busy.value=false;loading.value=false}
async function read(p=1){const n=++readId;loading.value=true;error.value='';try{const {data}=await props.api(`${base()}?page=${p}`);if(n===readId){items.value=data.items;actions.value=data.grantableActions;revision.value=data.collectionRevision;total.value=data.total;page.value=p}}
 catch(e){if(n===readId)error.value=String(e)}finally{if(n===readId)loading.value=false}}
function open(organization=false){reset();org.value=organization;show.value=true;void read()}
onMounted(()=>{if(props.initialOpen)open()})
function newGrant(){secret.value='';notice.value='';chosen.value=[];projects.value=[];hours.value=1;boundHours=0;key='';target.value=null;editing.value=true}
function requestKey(body:string){if(!key||last!==body){key=crypto.randomUUID();last=body}return key}
async function issue(){const n=epoch;busy.value=true;error.value='';try{
 if(!ends||boundHours!==hours.value){boundHours=hours.value;ends=new Date(Date.now()+hours.value*3600000).toISOString()}
 const body=JSON.stringify({allowedProjectIds:projects.value,allowedActions:chosen.value,allowProjectCreate:chosen.value.includes('pmo_project_create'),expiresAt:ends})
 await props.api(base(),{method:'POST',headers:{'Idempotency-Key':requestKey(body+revision.value),'If-Match':String(revision.value)},body},h=>{if(n===epoch)secret.value=h.get('X-PMO-New-Token')??''})
 if(n!==epoch)return;editing.value=false;notice.value=secret.value?'已签发，密钥仅显示一次。':'签发已完成；原密钥不能重复读取。';await read()
 }catch(e){if(n===epoch)error.value=String(e)}finally{if(n===epoch)busy.value=false}}
function revoke(row:Token){target.value=row;reason.value='';key='';editing.value=false;secret.value='';notice.value=''}
async function confirm(){const row=target.value;if(!row)return;const n=epoch;busy.value=true;error.value='';try{
 const body=JSON.stringify({reason:reason.value.trim()});await props.api(`${base()}/${row.tokenId}/revoke`,{method:'POST',headers:{'Idempotency-Key':requestKey(row.tokenId+row.revision+body),'If-Match':String(row.revision)},body})
 if(n!==epoch)return;target.value=null;notice.value='令牌已撤销';await read(page.value)
 }catch(e){if(n===epoch)error.value=String(e)}finally{if(n===epoch)busy.value=false}}
</script>
<template><el-button @click="open()"><KeyRound :size="15"/>我的 Agent 令牌</el-button><el-button v-if="admin" @click="open(true)"><KeyRound :size="15"/>组织令牌</el-button>
<el-drawer v-model="show" :title="org?'组织令牌':'我的 Agent 令牌'" size="min(860px,100vw)" destroy-on-close @close="reset">
  <div class="toolbar"><span>令牌 · {{total}}</span><el-button v-if="!org&&actions.length" :disabled="busy||editing||loading" @click="newGrant"><Plus :size="15"/>签发令牌</el-button><el-button aria-label="刷新令牌" :disabled="busy" @click="read(page)"><RefreshCw :size="15"/></el-button></div>
  <el-alert v-if="error" type="error" :title="error" :closable="false"/><p v-if="notice" role="status">{{notice}}</p><label v-if="secret">一次性密钥<input aria-label="一次性密钥" readonly autocomplete="off" :value="secret"></label>
  <form v-if="editing" @submit.prevent="issue"><label>授权项目<select v-model="projects" aria-label="授权项目" multiple><option v-for="p in props.projects" :key="p.projectId" :value="p.projectId">{{p.name}}</option></select></label>
    <label>允许动作<select v-model="chosen" aria-label="允许动作" multiple required><option v-for="op in actions" :key="op" :value="op">{{op}}</option></select></label>
    <label>有效期（小时）<input type="number" v-model.number="hours" min="1" max="24" required></label><div class="actions"><el-button @click="editing=false">取消</el-button><el-button native-type="submit" type="primary" :loading="busy">确认签发</el-button></div></form>
  <form v-if="target" @submit.prevent="confirm"><b>撤销 {{target.fingerprint}}</b><label>撤销原因<input v-model="reason" minlength="4" maxlength="500" required></label><div class="actions"><el-button @click="target=null">取消</el-button><el-button type="danger" native-type="submit" :loading="busy">确认撤销</el-button></div></form>
  <el-table :data="items" v-loading="loading" row-key="tokenId" empty-text="暂无令牌"><el-table-column prop="fingerprint" label="指纹" min-width="135"/><el-table-column label="状态" width="80"><template #default="{row}">{{stateNames[row.state]}}</template></el-table-column><el-table-column label="授权范围" min-width="210"><template #default="{row}">{{row.allowedActions.join('、')}}<br/>{{row.allowedProjectIds.join('、')||'无已授权项目'}}</template></el-table-column><el-table-column label="到期时间" min-width="150"><template #default="{row}">{{new Date(row.expiresAt).toLocaleString('zh-CN')}}</template></el-table-column><el-table-column label="操作" width="75"><template #default="{row}"><el-button v-if="row.state!=='revoked'" text aria-label="撤销令牌" :disabled="busy" @click="revoke(row)"><ShieldOff :size="15"/></el-button></template></el-table-column></el-table>
  <el-pagination v-if="total>20" :current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="read"/>
</el-drawer></template>
<style scoped>form{display:grid;gap:12px;padding:12px 0}select[multiple]{min-height:5rem;max-width:100%}</style>
