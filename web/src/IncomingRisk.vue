<script setup lang="ts">
import {nextTick,ref,watch} from 'vue'
import {ArrowRight} from 'lucide-vue-next'
import RiskResolution from './RiskResolution.vue'

const props=defineProps<{active?:boolean;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const uuid=/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i
const open=ref(false),invalid=ref(false),input=ref<HTMLInputElement|null>(null),code=ref('')
const request=ref<{riskId:string;acceptanceId:string}|null>(null)

watch(()=>props.active,active=>{
  if(active&&!open.value)void nextTick(()=>input.value?.focus())
  if(!active&&!open.value){invalid.value=false;code.value=''}
},{immediate:true})
function lookup(){
  if(open.value)return
  const [prefix,riskId,acceptanceId,...extra]=code.value.trim().toLowerCase().split(':')
  if(prefix!=='risk-request'||!riskId||!acceptanceId||!uuid.test(riskId)||!uuid.test(acceptanceId)||extra.length){
    invalid.value=true;input.value?.focus();return
  }
  request.value={riskId,acceptanceId};invalid.value=false;code.value='';open.value=true
}
</script>

<template>
  <section class="panel incoming-risk" aria-label="风险接收">
    <form class="lookup" aria-label="风险接收入口" @submit.prevent="lookup">
      <label>接收请求编号<input ref="input" v-model="code" :aria-invalid="invalid" aria-describedby="incoming-risk-help" autocomplete="off" autocapitalize="off"
        spellcheck="false" required maxlength="96" @input="invalid=false"/></label>
      <el-button type="primary" native-type="submit" :disabled="open"><ArrowRight :size="15"/>打开接收请求</el-button>
      <p id="incoming-risk-help" class="lookup-help">风险评审人发起移交后，会在风险处置记录或授权通知中取得接收请求编号。请向发起人索取完整编号后粘贴到这里。当前页面按编号打开请求，并保留原有权限核验。</p>
      <el-alert v-if="invalid" class="lookup-error" type="error" title="接收请求编号格式无效，请粘贴完整编号。" :closable="false" show-icon/>
    </form>
    <RiskResolution v-if="request" :key="`${request.riskId}:${request.acceptanceId}`" v-model="open"
      :risk-id="request.riskId" :acceptance-id="request.acceptanceId" :api="props.api"/>
  </section>
</template>

<style scoped>
.lookup{display:flex;align-items:end;flex-wrap:wrap;gap:12px}
.lookup label{flex:1 1 15rem;min-width:0;max-width:34rem}
.lookup-help{flex-basis:100%;max-width:52rem;margin:0;color:var(--pmo-muted);font-size:13px;line-height:1.55}
.lookup-error{flex-basis:100%;margin:0}
</style>
