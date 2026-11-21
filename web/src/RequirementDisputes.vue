<script setup lang="ts">
import {ref} from 'vue'
import {MessageSquareWarning, Undo2} from 'lucide-vue-next'

interface Dispute {
  disputeId:string; baselineVersion:number; requirementRevision:number; revision:number;
  customerId:string; reason:string; openedAt:string; withdrawnAt?:string; withdrawalReason?:string
}
interface Page {
  items:Dispute[]; baselineVersion:number; requirementRevision:number; canOpen:boolean;
  withdrawableIds:string[]; page:number; total:number
}
const props=defineProps<{api:(path:string, options?:RequestInit)=>Promise<any>;id:string;description:string}>()
const emit=defineEmits<{saved:[]}>()
const path=`requirements/${props.id}/disputes`
const open=ref(false), busy=ref(false), error=ref(''), history=ref<Page|null>(null), reason=ref('')
const edit=ref<{id?:string;revision:number}|null>(null)
let key='', signature=''

async function load(page=1) {
  open.value=true; busy.value=true; error.value=''; edit.value=null
  try {history.value=(await props.api(`${path}?page=${page}`)).data}
  catch (e) {history.value=null; error.value=String(e)}
  finally {busy.value=false}
}
function begin(revision:number,id?:string) {
  edit.value={id,revision}; reason.value=''; key=''; signature=''; error.value=''
}
async function submit() {
  const editMode=edit.value
  if (!editMode) return
  const body=JSON.stringify({reason:reason.value.trim()})
  if (!key||body!==signature) {signature=body; key=crypto.randomUUID()}
  busy.value=true; error.value=''
  try {
    await props.api(editMode.id?`${path}/${editMode.id}/withdraw`:path,{method:'POST',
      headers:{'If-Match':String(editMode.revision),'Idempotency-Key':key},body})
    await load(); emit('saved')
  } catch (e) {error.value=String(e)}
  finally {busy.value=false}
}
</script>

<template>
  <el-button text title="客户争议" @click="load()"><MessageSquareWarning :size="16"/>客户争议</el-button>
  <el-dialog v-model="open" append-to-body title="客户争议" width="min(620px,94vw)" :close-on-click-modal="false">
    <p class="description">{{description}}</p>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <div v-loading="busy" class="disputes">
      <template v-if="history">
        <p v-if="!history.items.length&&!error">没有正式客户争议</p>
        <article v-for="dispute in history.items" :key="dispute.disputeId">
          <div class="toolbar">
            <strong>{{dispute.withdrawnAt?'已撤回':dispute.baselineVersion===history.baselineVersion?'未解决':'旧基线争议'}}</strong>
            <span>基线 V{{dispute.baselineVersion}} · 需求版本 {{dispute.requirementRevision}}</span>
          </div>
          <p class="statement">{{dispute.reason}}</p>
          <p class="source"><time>{{new Date(dispute.openedAt).toLocaleString('zh-CN')}}</time> · 客户 {{dispute.customerId}}</p>
          <p v-if="dispute.withdrawnAt" class="statement">{{new Date(dispute.withdrawnAt).toLocaleString('zh-CN')}} 撤回：{{dispute.withdrawalReason}}</p>
          <el-button v-if="!edit && history.withdrawableIds.includes(dispute.disputeId)" text @click="begin(dispute.revision,dispute.disputeId)">
            <Undo2 :size="16"/>撤回此争议
          </el-button>
        </article>
        <el-pagination v-if="history.total>10" :page-size="10" :total="history.total" :current-page="history.page"
          :disabled="busy" layout="prev,pager,next" @current-change="load"/>
        <el-button v-if="history.canOpen&&!edit" type="primary" @click="begin(history.requirementRevision)">
          <MessageSquareWarning :size="16"/>提出正式争议
        </el-button>
      </template>
      <form v-if="edit" @submit.prevent="submit">
        <label>{{edit.id?'撤回说明':'争议理由'}}<textarea v-model="reason" required minlength="4" maxlength="2000" rows="3"/></label>
        <div class="actions">
          <el-button :disabled="busy" @click="edit=null">取消</el-button>
          <el-button type="primary" native-type="submit" :loading="busy" :disabled="reason.trim().length<4">
            {{edit.id?'确认撤回':'提交争议'}}
          </el-button>
        </div>
      </form>
    </div>
  </el-dialog>
</template>

<style scoped>
.description,.statement,.source { overflow-wrap:anywhere; white-space:pre-wrap }
.description { margin-top:0 }
.disputes { min-height:3rem; display:grid; gap:.75rem }
article { border-bottom:1px solid #d5ded9; padding-bottom:.5rem }
.toolbar { flex-wrap:wrap; gap:.5rem }
.source,.toolbar span { color:#516459; font-size:.85rem }
</style>
