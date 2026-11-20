<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {RefreshCw} from 'lucide-vue-next'
const props=defineProps<{projectId:string;api:(p:string)=>Promise<any>}>()
type Metrics={initialCount:number;changedInitialCount:number;activeCount:number;frozenCount:number;confirmedWeight:number;activeWeight:number;confirmedP0:number;activeP0:number;satisfaction?:string;changeRate?:string;gaps:string[]}
const data=ref<Metrics|null>(null),busy=ref(false),error=ref(''),gaps:Record<string,string>={INITIAL_BASELINE_MISSING:'尚无初始签署基线',NO_ACTIVE_REQUIREMENTS:'无有效需求',BASELINE_CHAIN_INVALID:'基线版本不一致',CONFIRMATION_RECEIPT_MISSING:'缺少客户确认回执'}
function pct(x?:string){return x===undefined?'不可用':new Intl.NumberFormat('zh-CN',{style:'percent',maximumFractionDigits:2}).format(Number(x))}
async function load(){busy.value=true;error.value='';try{data.value=(await props.api(`projects/${props.projectId}/requirement-metrics`)).data}catch(e){data.value=null;error.value=String(e)}finally{busy.value=false}}
onMounted(load)
</script>
<template><section aria-label="需求指标" v-loading="busy"><div class="toolbar"><h3>需求指标</h3><el-button aria-label="刷新需求指标" title="刷新需求指标" :disabled="busy" @click="load"><RefreshCw :size="15"/></el-button></div><el-alert v-if="error" type="error" :title="error" :closable="false"/><template v-if="data"><dl><dt>加权满足率</dt><dd>{{pct(data.satisfaction)}}（{{data.confirmedWeight}} / {{data.activeWeight}}）</dd><dt>初始需求变更率</dt><dd>{{pct(data.changeRate)}}（{{data.changedInitialCount}} / {{data.initialCount}}）</dd><dt>P0 已确认 / 有效</dt><dd>{{data.confirmedP0}} / {{data.activeP0}}</dd><dt>待审冻结 / 有效需求</dt><dd>{{data.frozenCount}} / {{data.activeCount}}</dd></dl><p v-if="data.gaps.length">{{data.gaps.map(g=>gaps[g]??g).join('；')}}</p></template></section></template>
<style scoped>section{border-top:1px solid #d5ded9;margin-top:1rem}dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:.4rem 1rem}dd{margin:0;font-variant-numeric:tabular-nums}</style>
