<script setup lang="ts">
import {ref,watch} from 'vue'
import {Bell,RefreshCw} from 'lucide-vue-next'
import WeeklyHealthOverview from './WeeklyHealthOverview.vue'
type Alert={alertId:string;projectCode:string;ruleId:string;severity:string;status:string;milestoneName?:string;plannedDate?:string;forecastDate?:string;reason?:string;sourceRefs:string[];notifiedAt?:string;businessPeriod?:string;dueAt?:string;taskTitle?:string}
const props=defineProps<{api:(p:string)=>Promise<any>}>()
const open=ref(false),busy=ref(false),error=ref(''),items=ref<Alert[]>([]),page=ref(1),total=ref(0)
const month=ref(''),rule=ref(''),view=ref('rules')
let seq=0
async function load(n=1){const id=++seq;busy.value=true;error.value='';try{const params=new URLSearchParams({page:String(n)});if(month.value)params.set('month',month.value);if(rule.value)params.set('ruleId',rule.value);const {data}=await props.api(`analytics/health-alerts?${params}`);if(id===seq){items.value=data.items;total.value=data.total;page.value=n}}catch(e){if(id===seq){items.value=[];total.value=0;error.value=String(e)}}finally{if(id===seq)busy.value=false}}
watch(view,tab=>{if(tab==='rules'&&open.value)void load()})
</script>
<template><el-button @click="open=true;if(view==='rules')load()"><Bell :size="15"/>预警中心</el-button><el-drawer v-model="open" title="预警中心" size="min(900px,100vw)" destroy-on-close>
<el-tabs v-model="view"><el-tab-pane name="rules" label="规则预警">
<div class="toolbar"><span>授权项目预警 · {{total}}</span><el-button aria-label="刷新预警" title="刷新预警" :disabled="busy" @click="load(page)"><RefreshCw :size="15"/></el-button></div>
<div class="filter"><label>月报归属月份<input type="month" v-model="month" @change="load()"/></label><label>规则<select v-model="rule" @change="load()"><option value="">全部</option><option v-for="code in ['LR-02','LR-03','LR-04','LR-05','LR-06','LR-08']" :key="code">{{code}}</option></select></label></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/>
<el-table :data="items" v-loading="busy" row-key="alertId" empty-text="暂无授权项目预警">
  <el-table-column type="expand"><template #default="{row}"><dl>
    <template v-if="row.ruleId==='LR-08'"><dt>复盘到期日</dt><dd>{{row.businessPeriod}}</dd></template>
    <template v-else-if="row.dueAt"><dt>补强期限（北京时间）</dt><dd>{{new Date(row.dueAt).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}}</dd></template>
    <template v-else-if="row.plannedDate || row.forecastDate"><dt>计划日期 / 预测日期</dt><dd>{{row.plannedDate??'—'}} / {{row.forecastDate??'—'}}</dd></template>
    <dt>原因</dt><dd>{{row.reason??'—'}}</dd><dt>触发来源</dt><dd>{{row.sourceRefs.join('，')}}</dd>
    <dt>站内通知</dt><dd>{{row.notifiedAt?new Date(row.notifiedAt).toLocaleString('zh-CN'):row.status==='resolved'?'无待办':'待投递'}}</dd>
  </dl></template></el-table-column>
  <el-table-column prop="projectCode" label="项目编号" min-width="110"/>
  <el-table-column prop="ruleId" label="规则" width="85"/>
  <el-table-column label="关联事项" min-width="130"><template #default="{row}">{{row.taskTitle??row.milestoneName??'—'}}</template></el-table-column>
  <el-table-column label="级别" width="88"><template #default="{row}">{{row.severity==='yellow'?'黄色':row.severity==='red'?'红色':row.severity}}</template></el-table-column>
  <el-table-column label="状态" width="90"><template #default="{row}">{{row.status==='open'?'未解决':row.status==='resolved'?'已解决':row.status}}</template></el-table-column>
</el-table>
<el-pagination v-if="total>20" :current-page="page" :total="total" :page-size="20" :disabled="busy" layout="prev,pager,next" @current-change="load"/>
</el-tab-pane><el-tab-pane name="weekly" label="每周三线" lazy><WeeklyHealthOverview v-if="view==='weekly'" :api="api"/></el-tab-pane></el-tabs></el-drawer></template>
<style scoped>dl{margin:.5rem 1rem;overflow-wrap:anywhere}dd{margin:0 0 .5rem}.filter{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:end;margin:.5rem 0}</style>
