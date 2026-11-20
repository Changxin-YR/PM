<script setup lang="ts">
import {nextTick,ref} from 'vue'
import {ArrowRight,Inbox} from 'lucide-vue-next'
import RiskResolution from './RiskResolution.vue'

const props=defineProps<{api:(path:string,options?:RequestInit)=>Promise<any>}>()
const uuid=/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i
const expanded=ref(false),open=ref(false),invalid=ref(false),input=ref<HTMLInputElement|null>(null),code=ref('')
const request=ref<{riskId:string;acceptanceId:string}|null>(null)

function toggle(){
  expanded.value=!expanded.value
  if(expanded.value)void nextTick(()=>input.value?.focus())
  else{invalid.value=false;code.value=''}
}
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
  <div class="incoming-risk" :class="{expanded}">
    <el-button :aria-expanded="expanded" @click="toggle"><Inbox :size="15"/>风险接收</el-button>
    <form v-if="expanded" class="lookup" aria-label="风险接收入口" @submit.prevent="lookup">
      <label>接收请求编号<input ref="input" v-model="code" :aria-invalid="invalid" autocomplete="off" autocapitalize="off"
        spellcheck="false" required maxlength="96" @input="invalid=false"/></label>
      <el-button type="primary" native-type="submit" :disabled="open"><ArrowRight :size="15"/>打开接收请求</el-button>
      <el-alert v-if="invalid" class="lookup-error" type="error" title="接收请求编号格式无效。" :closable="false" show-icon/>
    </form>
    <RiskResolution v-if="request" :key="`${request.riskId}:${request.acceptanceId}`" v-model="open"
      :risk-id="request.riskId" :acceptance-id="request.acceptanceId" :api="props.api"/>
  </div>
</template>

<style scoped>
.incoming-risk{display:inline-block;vertical-align:middle;max-width:100%}
.incoming-risk.expanded{display:block}
.lookup{display:flex;align-items:end;flex-wrap:wrap;gap:.55rem;margin:.6rem 0}
.lookup label{flex:1 1 15rem;min-width:0;max-width:34rem}
.lookup-error{flex-basis:100%;margin:0}
</style>
