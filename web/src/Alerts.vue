<script setup lang="ts">
import {ref,watch} from 'vue'
import {RefreshCw} from 'lucide-vue-next'
import WeeklyHealthOverview from './WeeklyHealthOverview.vue'
type Alert={alertId:string;projectCode:string;ruleId:string;severity:string;status:string;asOf:string;milestoneName?:string;plannedDate?:string;forecastDate?:string;reason?:string;sourceRefs:string[];notifiedAt?:string;businessPeriod?:string;dueAt?:string;taskTitle?:string}
type AlertPage={items:Alert[];page:number;pageSize:number;total:number;asOf:string}
const props=defineProps<{active?:boolean;api:(p:string)=>Promise<any>}>()
const busy=ref(false),error=ref(''),items=ref<Alert[]>([]),page=ref(1),total=ref(0)
const month=ref(''),rule=ref(''),view=ref('rules'),asOf=ref('')
const formatTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
let seq=0
async function load(n=1){
  const id=++seq,params=new URLSearchParams({page:String(n)})
  busy.value=true;error.value=''
  if(month.value)params.set('month',month.value)
  if(rule.value)params.set('ruleId',rule.value)
  try{
    const {data}:{data:AlertPage}=await props.api(`analytics/health-alerts?${params}`)
    if(id===seq){items.value=data.items;total.value=data.total;page.value=data.page;asOf.value=data.asOf}
  }catch(e){if(id===seq){items.value=[];total.value=0;asOf.value='';error.value=String(e)}}
  finally{if(id===seq)busy.value=false}
}
watch([()=>props.active,view],([active,tab])=>{if(active&&tab==='rules')void load()},{immediate:true})
</script>
<template>
<section class="panel" aria-label="预警中心">
<el-tabs v-model="view"><el-tab-pane name="rules" label="规则预警">
<div class="toolbar alert-toolbar"><span v-if="!error&&!busy">授权项目预警 · {{total}}</span><el-button aria-label="刷新预警" title="刷新预警" :disabled="busy" @click="load(page)"><RefreshCw :size="15"/> 刷新</el-button>
  <span v-if="asOf&&!busy">列表读取（北京时间）<time aria-label="列表读取时间" :datetime="asOf">{{formatTime(asOf)}}</time></span>
</div>
<div class="filter"><label>月报归属月份<input type="month" v-model="month" @change="load()"/></label><label>规则<select v-model="rule" @change="load()"><option value="">全部</option><option v-for="code in ['LR-02','LR-03','LR-04','LR-05','LR-06','LR-08']" :key="code">{{code}}</option></select></label></div>
<el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
<el-table v-else :data="items" v-loading="busy" row-key="alertId" :empty-text="busy?'正在读取…':'暂无授权项目预警'">
  <el-table-column type="expand"><template #default="{row}"><dl>
    <template v-if="row.ruleId==='LR-08'"><dt>复盘到期日</dt><dd>{{row.businessPeriod}}</dd></template>
    <template v-else-if="row.dueAt"><dt>补强期限（北京时间）</dt><dd>{{new Date(row.dueAt).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}}</dd></template>
    <template v-else-if="row.plannedDate || row.forecastDate"><dt>计划日期 / 预测日期</dt><dd>{{row.plannedDate??'—'}} / {{row.forecastDate??'—'}}</dd></template>
    <dt>原因</dt><dd>{{row.reason??'—'}}</dd><dt>触发来源</dt><dd>{{row.sourceRefs.join('，')}}</dd>
    <dt>事件更新（北京时间）</dt><dd><time aria-label="事件更新时间" :datetime="row.asOf">{{formatTime(row.asOf)}}</time></dd>
    <dt>站内通知</dt><dd>{{row.notifiedAt?new Date(row.notifiedAt).toLocaleString('zh-CN'):row.status==='resolved'?'无待办':'待投递'}}</dd>
  </dl></template></el-table-column>
  <el-table-column prop="projectCode" label="项目编号" min-width="110"/>
  <el-table-column prop="ruleId" label="规则" width="85"/>
  <el-table-column label="关联事项" min-width="130"><template #default="{row}">{{row.taskTitle??row.milestoneName??'—'}}</template></el-table-column>
  <el-table-column label="级别" width="88"><template #default="{row}"><el-tag :type="row.severity==='red'?'danger':row.severity==='yellow'?'warning':'info'" effect="light">{{row.severity==='yellow'?'黄色':row.severity==='red'?'红色':row.severity}}</el-tag></template></el-table-column>
  <el-table-column label="状态" width="90"><template #default="{row}">{{row.status==='open'?'未解决':row.status==='resolved'?'已解决':row.status}}</template></el-table-column>
</el-table>
<el-pagination v-if="total>20" :current-page="page" :total="total" :page-size="20" :disabled="busy" layout="prev,pager,next" @current-change="load"/>
</el-tab-pane><el-tab-pane name="weekly" label="每周三线" lazy><WeeklyHealthOverview v-if="active&&view==='weekly'" :api="api"/></el-tab-pane></el-tabs>
</section>
</template>
<style scoped>
dl{margin:8px 16px;overflow-wrap:anywhere}
dd{margin:0 0 8px}
.alert-toolbar{flex-wrap:wrap}
.filter{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:end;margin:0 0 16px}
.filter select{min-width:108px}
</style>
