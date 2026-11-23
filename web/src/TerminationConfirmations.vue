<script setup lang="ts">
import {nextTick,ref,watch} from 'vue'
import {ClipboardCheck,RefreshCw} from 'lucide-vue-next'

type Entry={requestId:string;projectName:string;revision:number;reason:string;requestedBy:string}
const props=defineProps<{active?:boolean;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const busy=ref(false),loading=ref(false),items=ref<Entry[]>([]),active=ref<Entry|null>(null)
const reason=ref(''),error=ref(''),notice=ref('')
const reasonInput=ref<HTMLTextAreaElement>()
let recent:{signature:string;key:string}|undefined
async function load(){
  if(loading.value)return
  loading.value=true;error.value=''
  try{items.value=(await props.api('me/termination-confirmations')).data.items}
  catch(e){items.value=[];error.value=e instanceof Error?e.message:'未能读取待确认记录。'}
  finally{loading.value=false}
}
function select(row:Entry|null){active.value=row;reason.value='';error.value='';notice.value=''}
function openForm(row:Entry){
  select(row)
  void nextTick(()=>reasonInput.value?.focus())
}
function returnToList(){
  const id=active.value?.requestId
  select(null)
  void nextTick(()=>{if(id)document.getElementById(`termination-item-${id}`)?.focus()})
}
watch(()=>props.active,visible=>{
  if(!visible||busy.value||(active.value&&recent))return
  select(null);void load()
},{immediate:true})
async function confirm(){
  const item=active.value,why=reason.value.trim()
  if(!item||busy.value||why.length<4||why.length>2000)return
  const signature=JSON.stringify([item.requestId,item.revision,why])
  if(recent?.signature!==signature)recent={signature,key:crypto.randomUUID()}
  busy.value=true;error.value=''
  try{
    await props.api(`approvals/${item.requestId}/confirm-termination`,{method:'POST',body:JSON.stringify({reason:why}),
      headers:{'If-Match':String(item.revision),'Idempotency-Key':recent.key}})
    select(null);recent=undefined;notice.value='已记录发起人确认。';await load()
  }catch(e){error.value=e instanceof Error?e.message:'未能确认提交结果。'}
  finally{busy.value=false}
}
</script>

<template>
  <section v-loading="loading" class="panel confirmations" aria-label="我的终止确认">
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
      <p v-if="notice" role="status">{{notice}}</p>
      <form v-if="active" @submit.prevent="confirm">
        <h3>{{active.projectName}} · 终止落地评审</h3>
        <p>{{active.reason}}</p>
        <small v-if="active.requestedBy">申请人 ID <code>{{active.requestedBy}}</code></small>
        <label>确认理由<textarea ref="reasonInput" v-model="reason" rows="3" required minlength="4" maxlength="2000" :disabled="busy"/></label>
        <div class="actions"><el-button :disabled="busy" @click="returnToList">返回</el-button>
          <el-button native-type="submit" type="primary" :disabled="reason.trim().length<4" :loading="busy"><ClipboardCheck :size="15"/> 确认终止申请</el-button></div>
      </form>
      <template v-else>
        <div class="actions"><el-button :disabled="loading" title="刷新待确认" aria-label="刷新待确认" @click="load"><RefreshCw :size="15"/> 刷新</el-button></div>
        <p v-if="!items.length&&!loading&&!error" class="confirmations-empty"><ClipboardCheck :size="28" aria-hidden="true"/>暂无待确认的终止申请。</p>
        <ul v-if="items.length"><li v-for="item in items" :key="item.requestId"><div><strong>{{item.projectName}}</strong><p>{{item.reason}}</p><small v-if="item.requestedBy">申请人 ID <code>{{item.requestedBy}}</code></small></div>
          <el-button :id="`termination-item-${item.requestId}`" size="small" @click="openForm(item)">处理</el-button></li></ul>
      </template>
  </section>
</template>

<style scoped>
.confirmations{min-height:120px;overflow-wrap:anywhere}
.confirmations form{max-width:760px}
.confirmations-empty{min-height:126px;display:grid;justify-items:center;align-content:center;gap:12px;color:var(--pmo-muted)}
ul{list-style:none;padding:0}
li{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:16px 0;border-top:1px solid var(--pmo-line)}
li .el-button{flex:none}
p,h3{margin:8px 0}
small{color:var(--pmo-muted);font-size:13px}
label{margin:16px 0}
</style>
