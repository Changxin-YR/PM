<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Pencil,RefreshCw,Save} from 'lucide-vue-next'
import CostReconciliation from './CostReconciliation.vue'
import Postings from './Postings.vue'
type Kind='money'|'effort'
type Measure={budget:string;actual:string;forecastRemaining:string;projectedTotal:string;varianceRate?:string;color:string}
type Summary={money:Measure;effort:Measure;plan:{revision:number;moneyBudget?:string;effortBudget?:string};canSaveBudget:boolean;canSaveForecast:boolean}
const props=defineProps<{projectId:string;api:(p:string,o?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/cost`,kinds:Kind[]=['money','effort'],labels={money:'金额（元）',effort:'人天'},colors:Record<string,string>={untrusted:'数据不可信',green:'绿',yellow:'黄',red:'红'}
const data=ref<Summary|null>(null),busy=ref(false),error=ref(''),notice=ref(''),kind=ref<Kind>('money'),action=ref<''|'budget'|'forecast'>(''),value=ref(''),reason=ref('')
let key='',signature='',revision=0
async function load(){data.value=(await props.api(path+'s')).data}
async function refresh(){busy.value=true;error.value='';try{await load()}catch(e){data.value=null;error.value=String(e)}finally{busy.value=false}}
function open(k:Kind,a:'budget'|'forecast'){if(!data.value)return;action.value=a;kind.value=k;revision=data.value.plan.revision;key='';notice.value='';error.value='';reason.value='';value.value=a==='budget'?(data.value.plan[k==='money'?'moneyBudget':'effortBudget']??''):data.value[k].forecastRemaining}
async function save(){busy.value=true;error.value='';try{const budget=action.value==='budget',body=JSON.stringify({type:kind.value,...(budget?{budget:value.value}:{forecastRemaining:value.value,reason:reason.value.trim()})}),sig=action.value+body+revision
 if(!key||sig!==signature){key=crypto.randomUUID();signature=sig}
 await props.api(path+(budget?'-budgets':'-forecasts'),{method:'PUT',headers:{'If-Match':String(revision),'Idempotency-Key':key},body});action.value='';await load();notice.value='已保存'
 }catch(e){error.value=String(e)}finally{busy.value=false}}
onMounted(refresh)
</script>
<template><div class="toolbar"><span>成本与人天</span><el-button aria-label="刷新成本" title="刷新成本" :disabled="busy||!!action" @click="refresh"><RefreshCw :size="15"/></el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/><span role="status">{{notice}}</span>
<div class="columns" v-if="data"><section v-for="k in kinds" :key="k" :aria-label="labels[k]"><h3>{{labels[k]}}</h3><dl>
<dt>获批预算</dt><dd>{{data[k].budget}}</dd><dt>核验实际</dt><dd>{{data[k].actual}}</dd><dt>剩余预测</dt><dd>{{data[k].forecastRemaining}}</dd><dt>预计总额</dt><dd>{{data[k].projectedTotal}}</dd><dt>偏差率</dt><dd>{{data[k].varianceRate===undefined||data[k].varianceRate==='UNAVAILABLE'?'不可用':(Number(data[k].varianceRate)*100).toFixed(2)+'%'}}</dd><dt>成本线</dt><dd>{{colors[data[k].color]}}</dd><dt>预算草稿</dt><dd>{{data.plan[k==='money'?'moneyBudget':'effortBudget']??'未填写'}}</dd></dl>
<el-button v-if="data.canSaveBudget" :disabled="busy||!!action" @click="open(k,'budget')"><Pencil :size="14"/>修改预算草稿</el-button><el-button v-if="data.canSaveForecast" :disabled="busy||!!action" @click="open(k,'forecast')"><Pencil :size="14"/>修改预测</el-button></section></div>
<form v-if="action" @submit.prevent="save"><label>{{labels[kind]}} · {{action==='budget'?'预算草稿':'剩余预测'}}<input v-model="value" inputmode="decimal" pattern="(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?" required></label>
<label v-if="action==='forecast'">预测原因<textarea v-model="reason" minlength="3" maxlength="2000" required/></label><div class="actions"><el-button :disabled="busy" @click="action=''">取消</el-button><el-button type="primary" native-type="submit" :loading="busy"><Save :size="15"/>保存成本</el-button></div></form>
<CostReconciliation :project-id="projectId" :api="api" :costs-busy="busy||!!action"/>
<Postings :project-id="projectId" :api="api" @changed="refresh"/></template>
<style scoped>.columns{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr));gap:1.25rem}dl{display:grid;grid-template-columns:auto 1fr;gap:.45rem}dd{text-align:right;font-variant-numeric:tabular-nums;overflow-wrap:anywhere;margin:0}form{display:grid;gap:.75rem;margin-top:1rem}</style>
