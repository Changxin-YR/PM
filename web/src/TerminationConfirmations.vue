<script setup lang="ts">
import {ref} from 'vue'
import {ClipboardCheck,RefreshCw} from 'lucide-vue-next'

type Entry={requestId:string;projectName:string;revision:number;reason:string;requestedBy:string}
const props=defineProps<{api:(path:string,options?:RequestInit)=>Promise<any>}>()
const open=ref(false),busy=ref(false),loading=ref(false),items=ref<Entry[]>([]),active=ref<Entry|null>(null)
const reason=ref(''),error=ref(''),notice=ref('')
let recent:{signature:string;key:string}|undefined
async function load(){
  if(loading.value)return
  loading.value=true;error.value=''
  try{items.value=(await props.api('me/termination-confirmations')).data.items}
  catch(e){items.value=[];error.value=e instanceof Error?e.message:'未能读取待确认记录。'}
  finally{loading.value=false}
}
function select(row:Entry|null){active.value=row;reason.value='';error.value='';notice.value=''}
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
  <el-button @click="open=true;select(null);load()"><ClipboardCheck :size="15"/> 我的终止确认</el-button>
  <el-dialog v-model="open" title="我的终止确认" width="min(540px,94vw)" append-to-body :show-close="!busy"
    :close-on-click-modal="false" :close-on-press-escape="!busy">
    <div v-loading="loading" class="confirmations">
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
      <p v-if="notice" role="status">{{notice}}</p>
      <form v-if="active" @submit.prevent="confirm">
        <h3>{{active.projectName}} · 终止落地评审</h3>
        <p>{{active.reason}}</p>
        <label>确认理由<textarea v-model="reason" rows="3" required minlength="4" maxlength="2000" :disabled="busy"/></label>
        <div class="actions"><el-button :disabled="busy" @click="select(null)">返回</el-button>
          <el-button native-type="submit" type="primary" :disabled="reason.trim().length<4" :loading="busy"><ClipboardCheck :size="15"/> 确认终止申请</el-button></div>
      </form>
      <template v-else>
        <div class="actions"><el-button :disabled="loading" title="刷新待确认" aria-label="刷新待确认" @click="load"><RefreshCw :size="15"/></el-button></div>
        <p v-if="!items.length&&!loading&&!error">暂无待确认的终止申请。</p>
        <ul v-else><li v-for="item in items" :key="item.requestId"><div><strong>{{item.projectName}}</strong><p>{{item.reason}}</p></div>
          <el-button size="small" @click="select(item)">处理</el-button></li></ul>
      </template>
    </div>
  </el-dialog>
</template>

<style scoped>
.confirmations{min-height:4rem;overflow-wrap:anywhere}
ul{list-style:none;padding:0}
li{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.6rem 0;border-top:1px solid var(--el-border-color)}
p,h3{margin:.35rem 0}
label{display:grid;gap:.35rem;margin:.6rem 0}
textarea{padding:.45rem;border:1px solid var(--el-border-color);border-radius:4px;color:inherit;background:var(--el-bg-color);font:inherit}
.actions{display:flex;justify-content:flex-end;gap:.35rem;margin-top:.4rem}
</style>
