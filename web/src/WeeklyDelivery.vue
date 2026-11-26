<script setup lang="ts">
import {ref, watch} from 'vue'
import {RefreshCw, Send} from 'lucide-vue-next'

type Recipient = {authorizationId:string;displayName:string;expiresAt:string;channel:'synthetic_inbox'}
type State = 'pending'|'submitted'|'delivered'|'blocked'|'unknown'|'failed'
type Delivery = {deliveryId:string;reportVersion:number;reportRevision:number;attachmentHash:string;
  recipient:Recipient;status:State;queuedAt:string;channelReceivedAt?:string;providerMessageId?:string;errorCode?:string}
type View = {recipients:Recipient[];deliveries:Delivery[];channelReady:boolean}
const props=defineProps<{reportId:string;reportVersion:number;revision:number;reportHash:string;
  api:(path:string,options?:RequestInit)=>Promise<any>}>()
const labels:Record<State,string>={pending:'待送达',submitted:'渠道已接收',delivered:'隔离收件箱已确认',
  blocked:'已阻止',unknown:'结果未知，待核查',failed:'发送失败'}
const problems:Record<string,string>={POLICY_PENDING:'正式周报送达政策待签核。',DELIVERY_CHANNEL_NOT_CONFIGURED:'真实外部渠道尚未配置。',
  RECIPIENT_AUTHORIZATION_INVALID:'收件授权已过期、撤销或超出当前项目，请刷新。',
  DELIVERY_REQUIRES_PUBLISHED_WEEKLY:'此周报已失效或尚未正式发布。',DELIVERY_CONTENT_CHANGED:'附件内容已变化，请重新获取周报原件。',
  DELIVERY_RECIPIENT_ALREADY_QUEUED:'该收件人已登记此版本，请查看回执。',
  DELIVERY_REQUESTER_NO_LONGER_AUTHORIZED:'申请人已失去当前项目权限。',DELIVERY_DEAD_LETTER:'已达到自动重试上限。',
  DELIVERY_RECEIPT_MISMATCH:'渠道回执与已授权附件不一致，需核查。'}
const info=ref<View>(), chosen=ref(''), documentHash=ref(''), loading=ref(false), busy=ref(false), dialog=ref(false)
const error=ref(''), notice=ref('')
let attempt:{signature:string;key:string}|undefined, sequence=0
function problem(e:unknown){
  const message=e instanceof Error?e.message:String(e)
  return Object.entries(problems).find(([code])=>message.includes(code))?.[1]??message
}
function time(at:string){return new Date(at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
async function load(){
  const current=++sequence
  loading.value=true;error.value=''
  try{
    const {data}=await props.api(`reports/${props.reportId}/delivery`)
    if(current===sequence)info.value=data
  }catch(e){if(current===sequence)error.value=problem(e)}
  finally{if(current===sequence)loading.value=false}
}
async function review(){
  const current=sequence
  loading.value=true;error.value=''
  try{
    const {data}=await props.api(`reports/${props.reportId}/weekly-export`)
    if(current===sequence){documentHash.value=data.sha256;documentHash.value&& (dialog.value=true)}
  }catch(e){if(current===sequence)error.value=problem(e)}
  finally{if(current===sequence)loading.value=false}
}
async function queue(){
  if(!chosen.value||!documentHash.value||busy.value)return
  const current=sequence
  const body=JSON.stringify({authorizationId:chosen.value,reportHash:props.reportHash,attachmentHash:documentHash.value})
  const signature=JSON.stringify([props.reportId,props.revision,body])
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  busy.value=true;error.value='';notice.value=''
  try{
    await props.api(`reports/${props.reportId}/delivery`,{method:'POST',body,
      headers:{'If-Match':String(props.revision),'Idempotency-Key':attempt.key}})
    if(current===sequence){dialog.value=false;chosen.value='';attempt=undefined;notice.value='已登记隔离发送，回执可刷新查看。';void load()}
  }catch(e){if(current===sequence)error.value=problem(e)}
  finally{busy.value=false}
}
watch(()=>[props.reportId,props.revision],()=>{
  info.value=undefined;chosen.value='';dialog.value=false;attempt=undefined;notice.value='';void load()
},{immediate:true})
</script>

<template>
  <section class="delivery-panel" aria-label="正式周报送达" :aria-busy="loading||busy">
    <div class="delivery-toolbar">
      <h4>正式周报送达</h4>
      <el-button text aria-label="刷新送达回执" :disabled="loading||busy" @click="load"><RefreshCw :size="15"/></el-button>
    </div>
    <el-alert v-if="error&&!dialog" type="error" :closable="false" :title="error"/>
    <p v-if="notice" role="status">{{notice}}</p>
    <template v-if="info">
      <p v-if="!info.channelReady">发送渠道当前不可用，外部实收待验收。</p>
      <p v-else-if="!info.recipients.length&&!info.deliveries.length">当前没有有效的隔离收件授权。</p>
      <el-button v-if="info.recipients.length" :disabled="loading||busy" @click="review"><Send :size="15"/>登记送达</el-button>
      <el-table v-if="info.deliveries.length" :data="info.deliveries" row-key="deliveryId" aria-label="送达回执">
        <el-table-column label="授权收件人" prop="recipient.displayName" min-width="150"/>
        <el-table-column label="发布版本" min-width="120"><template #default="{row}">v{{row.reportVersion}} · 修订 {{row.reportRevision}}</template></el-table-column>
        <el-table-column label="送达状态" min-width="155"><template #default="{row}">
          <el-tag size="small" :type="row.status==='delivered'?'success':row.status==='pending'?'info':'warning'">
            {{labels[row.status as State]}}
          </el-tag>
          <div v-if="row.errorCode">{{problems[row.errorCode]??row.errorCode}}</div>
        </template></el-table-column>
        <el-table-column label="渠道回执" min-width="200"><template #default="{row}">
          <details v-if="row.providerMessageId">
            <summary>{{row.channelReceivedAt?time(row.channelReceivedAt):'已获得渠道编号'}}</summary>
            <div>消息 <code>{{row.providerMessageId}}</code></div><div>附件 <code>{{row.attachmentHash}}</code></div>
          </details>
          <span v-else>待核验</span>
        </template></el-table-column>
        <el-table-column label="实际收件证据" min-width="150"><template #default>待外部验收</template></el-table-column>
      </el-table>
    </template>
  </section>
  <el-dialog v-model="dialog" title="确认周报送达" width="min(92vw, 560px)" :close-on-click-modal="false"
    :show-close="!busy" :close-on-press-escape="!busy">
    <el-alert v-if="error" type="error" :closable="false" :title="error"/>
    <p>确认登记正式周报 v{{reportVersion}} · 修订 {{revision}}。渠道为隔离收件箱。</p>
    <p class="delivery-hash">附件摘要 <code>{{documentHash}}</code></p>
    <label>已授权收件人 <select v-model="chosen" :disabled="busy">
      <option disabled value="">请选择</option>
      <option v-for="recipient in info?.recipients??[]" :key="recipient.authorizationId" :value="recipient.authorizationId">
        {{recipient.displayName}} · {{time(recipient.expiresAt)}} 到期
      </option>
    </select></label>
    <template #footer>
      <el-button :disabled="busy" @click="dialog=false">取消</el-button>
      <el-button type="primary" :disabled="!chosen" :loading="busy" @click="queue">确认登记</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.delivery-panel{margin-top:14px;padding:12px 16px;border:1px solid var(--el-border-color-light);border-radius:7px}
.delivery-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px}
.delivery-toolbar h4{margin:0}.delivery-panel code,.delivery-hash{overflow-wrap:anywhere}
</style>
