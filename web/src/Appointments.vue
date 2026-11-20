<script setup lang="ts">
import {ref} from 'vue'
import {Mail, RefreshCw, Send} from 'lucide-vue-next'

type Item={invitationId:string;projectName:string;nomineeName:string;role:'pm'|'sponsor';revision:number}
const props=defineProps<{api:(path:string,opts?:RequestInit)=>Promise<any>;projects:{projectId:string;name:string;revision:number;stage:string}[]}>()
const emit=defineEmits<{changed:[]}>()
const visible=ref(false),busy=ref(false),loading=ref(false),error=ref(''),notice=ref('')
const inbox=ref<Item[]>([]),queue=ref<Item[]>([]),pid=ref(''),person=ref(''),role=ref('pm')
const attempts=new Map<string,string>()
function msg(e:unknown){return e instanceof Error?e.message:'请求失败'}
function roleName(value:string){return value==='pm'?'项目经理':'Sponsor'}
async function refresh(){loading.value=true;error.value='';try{const [mine,todo]=await Promise.all([props.api('me/role-invitations'),props.api('me/appointment-queue')]);inbox.value=mine.data;queue.value=todo.data}catch(e){inbox.value=[];queue.value=[];error.value=msg(e)}finally{loading.value=false}}
async function open(){visible.value=true;notice.value='';await refresh()}
async function write(path:string,revision:number,body:object,success:string){
  if(busy.value)return;busy.value=true;error.value='';notice.value=''
  const signature=JSON.stringify([path,revision,body]),key=attempts.get(signature)||crypto.randomUUID();attempts.set(signature,key)
  try{await props.api(path,{method:'POST',headers:{'If-Match':String(revision),'Idempotency-Key':key},body:JSON.stringify(body)});attempts.delete(signature);notice.value=success;emit('changed');await refresh()}
  catch(e){error.value=msg(e);emit('changed')}finally{busy.value=false}
}
function propose(){const p=props.projects.find(x=>x.projectId===pid.value);if(p)void write(`projects/${p.projectId}/role-invitations`,p.revision,{personId:person.value.trim(),role:role.value},'已提交提名，尚未授予项目权限。')}
function decide(item:Item,action:string){void write(`role-invitations/${item.invitationId}/${action}`,item.revision,{},({issue:'邀请已发出。',accept:'已接受任命。',decline:'已拒绝任命。'})[action]||'已保存。')}
</script>
<template>
<el-button @click="open"><Mail :size="16"/> 人员任命</el-button>
<el-drawer v-model="visible" title="人员任命" size="min(650px,100vw)" destroy-on-close>
<el-alert v-if="error" :title="error" type="error" :closable="false"/><p role="status" v-if="notice">{{notice}}</p>
<el-button aria-label="刷新任命列表" title="刷新任命列表" :disabled="loading||busy" @click="refresh"><RefreshCw :size="16"/></el-button>
<section><h3>我的邀请</h3><p v-if="!loading&&!error&&!inbox.length">暂无待确认邀请</p>
<div class="item" v-for="i in inbox" :key="i.invitationId"><span>{{i.projectName}} · {{roleName(i.role)}}</span><el-button :disabled="busy" @click="decide(i,'accept')">接受任命</el-button><el-button :disabled="busy" @click="decide(i,'decline')">拒绝任命</el-button></div></section>
<section><h3>待我发出的邀请</h3><p v-if="!loading&&!error&&!queue.length">暂无待办</p><div class="item" v-for="i in queue" :key="i.invitationId"><span>{{i.projectName}} · {{i.nomineeName}} · {{roleName(i.role)}}</span><el-button :disabled="busy" @click="decide(i,'issue')"><Send :size="14"/> 发出邀请</el-button></div></section>
<section><h3>新提名</h3><form @submit.prevent="propose"><label>项目草稿<select v-model="pid" required><option value="" disabled>选择项目</option><option v-for="p in projects.filter(x=>x.stage==='drafting')" :key="p.projectId" :value="p.projectId">{{p.name}}</option></select></label><label>被提名人 ID<input v-model="person" required maxlength="36" pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"></label><label>项目角色<select v-model="role"><option value="pm">项目经理</option><option value="sponsor">Sponsor</option></select></label><el-button type="primary" native-type="submit" :loading="busy">提交提名</el-button></form></section>
</el-drawer>
</template>
<style scoped>section{padding:12px 0;border-bottom:1px solid #dfe5e3}h3{font-size:15px}.item{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:6px 0}.item span{flex:1;min-width:130px}select{font:inherit;padding:6px;max-width:100%}</style>
