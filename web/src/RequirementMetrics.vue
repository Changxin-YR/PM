<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {RefreshCw} from 'lucide-vue-next'
const props=defineProps<{projectId:string;api:(p:string)=>Promise<any>}>()
type Metrics={initialCount:number;changedInitialCount:number;activeCount:number;frozenCount:number;confirmedWeight:number;activeWeight:number;confirmedP0:number;activeP0:number;satisfaction?:string;changeRate?:string;gaps:string[]}
const data=ref<Metrics|null>(null),busy=ref(false),error=ref(''),gaps:Record<string,string>={INITIAL_BASELINE_MISSING:'尚无初始签署基线',NO_ACTIVE_REQUIREMENTS:'无有效需求',BASELINE_CHAIN_INVALID:'基线版本不一致',CONFIRMATION_RECEIPT_MISSING:'缺少客户确认回执'}
function pct(x?:string){return x===undefined||x==='UNAVAILABLE'?'不可用':new Intl.NumberFormat('zh-CN',{style:'percent',maximumFractionDigits:2}).format(Number(x))}
async function load(){busy.value=true;error.value='';try{data.value=(await props.api(`projects/${props.projectId}/requirement-metrics`)).data}catch(e){data.value=null;error.value=String(e)}finally{busy.value=false}}
onMounted(load)
</script>
<template>
  <section class="panel" aria-label="需求指标" v-loading="busy">
    <div class="toolbar metrics-toolbar"><h3>需求指标</h3><el-button aria-label="刷新需求指标" title="刷新需求指标" :disabled="busy" @click="load"><RefreshCw :size="15"/></el-button></div>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <template v-if="data">
      <dl class="metrics">
        <div><dt>加权满足率</dt><dd>{{pct(data.satisfaction)}}<small>（{{data.confirmedWeight}} / {{data.activeWeight}}）</small></dd></div>
        <div><dt>初始需求变更率</dt><dd>{{pct(data.changeRate)}}<small>（{{data.changedInitialCount}} / {{data.initialCount}}）</small></dd></div>
        <div><dt>P0 已确认 / 有效</dt><dd>{{data.confirmedP0}} / {{data.activeP0}}</dd></div>
        <div><dt>待审冻结 / 有效需求</dt><dd>{{data.frozenCount}} / {{data.activeCount}}</dd></div>
      </dl>
      <p v-if="data.gaps.length" class="metrics-gaps">{{data.gaps.map(g=>gaps[g]??g).join('；')}}</p>
    </template>
  </section>
</template>
<style scoped>
.metrics-toolbar{justify-content:space-between}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr));gap:8px 16px;margin:0}
.metrics div{min-width:0;border-inline-start:1px solid var(--pmo-line);padding-inline-start:12px}
.metrics dt,.metrics-gaps{color:var(--pmo-muted);font-size:13px}
.metrics dd{margin:0;font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.metrics small{font-weight:400}
.metrics-gaps{margin:12px 0 0}
</style>
