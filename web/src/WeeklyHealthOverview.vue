<script setup lang="ts">
import {onMounted, ref} from 'vue'
import {RefreshCw} from 'lucide-vue-next'

type Lamp='green'|'yellow'|'red'|'untrusted'
type Cause={line:'time'|'money'|'effort'|'quality';color:Lamp;code:string;name?:string;plannedDate?:string;
  forecastDate?:string;delayDays?:number;varianceRate?:string;sourceRefs:string[]}
type Sample={snapshotId:string;asOf:string;health:Lamp;publication:'official'|'preview'|'withheld';ruleVersion:string;
  timeLine?:Lamp;costLine?:Lamp;qualityLine?:Lamp;causes:Cause[];omittedCauses:number;gaps:string[];sourceRefs:string[];
  ruleEvents:{alertId:string;ruleId:string;sourceRefs:string[]}[]}
type Row={projectId:string;projectCode:string;projectName:string;latest:Sample;earlierSignal?:Sample}
type Page={weekStart:string;weekEnd:string;asOf:string;sampledProjects:number;items:Row[];total:number;page:number;pageSize:number}
const props=defineProps<{api:(p:string)=>Promise<{data:Page}>}>()
const week=ref(''), result=ref<Page|null>(null), busy=ref(false), error=ref('')
const colors:Record<Lamp,string>={green:'绿',yellow:'黄',red:'红',untrusted:'不可信'}
const tags:Record<Lamp,'success'|'warning'|'danger'|'info'>={green:'success',yellow:'warning',red:'danger',untrusted:'info'}
const labels={time:'时间',money:'金额',effort:'人天',quality:'质量'}
const reasons:Record<string,string>={MILESTONE_DRIFT:'关键里程碑预测延期',MILESTONE_OVERDUE:'关键里程碑已逾期',COST_VARIANCE:'成本预测偏差',
  CUSTOMER_DISPUTE:'有效客户争议',P0_UNCONFIRMED:'P0 尚未确认',REQUIREMENT_LATE:'需求确认滞后',
  LR06_TWO_WEEKS_MISSED:'连续两期缺少周报',LR06_CLAIM_WITHOUT_EVIDENCE:'申报里程碑进展缺少佐证',
  WEEKLY_PM_CONFIRMATION_PENDING:'应交周报待 PM 确认'}
const publication={official:'已发布',preview:'预览',withheld:'暂停发布'}
const time=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
function samples(row:Row) {
  return [{label:'周内最近采样',sample:row.latest},...(row.earlierSignal?[{label:'周内早先黄红记录',sample:row.earlierSignal}]:[])]
}
let sequence=0
async function load(page=1, useCutoff=false){
  const seq=++sequence
  const query=new URLSearchParams({page:String(page)})
  if(week.value)query.set('weekStart',week.value)
  if(useCutoff&&result.value)query.set('asOf',result.value.asOf)
  busy.value=true;error.value=''
  try{
    const {data}=await props.api(`analytics/weekly-health?${query}`)
    if(seq===sequence){result.value=data;week.value=data.weekStart}
  }catch(e){if(seq===sequence){result.value=null;error.value=e instanceof Error?e.message:String(e)}}
  finally{if(seq===sequence)busy.value=false}
}
function paging(page:number){void load(page,true)}
onMounted(()=>void load())
</script>

<template>
  <section aria-label="每周三线" v-loading="busy" :aria-busy="busy">
    <div class="toolbar filters">
      <label>周起始日（周一）<input type="date" v-model="week" @change="load()"/></label>
      <el-button @click="week='';load()" :disabled="busy">本周</el-button>
      <el-button aria-label="刷新周采样" title="刷新周采样" @click="load()" :disabled="busy"><RefreshCw :size="15"/></el-button>
    </div>
    <el-alert v-if="error" type="error" :closable="false" :title="error"/>
    <template v-if="result">
      <div class="period">
        <strong>{{result.weekStart}} 至 {{result.weekEnd}}</strong>
        <span>北京时间 · 已采样 {{result.sampledProjects}} 项 · 黄红或不可发布 {{result.total}} 项</span>
      </div>
      <el-empty v-if="!result.items.length" :description="result.sampledProjects?'本周已采样项目无黄红或不可发布记录':'本周无授权项目采样'" :image-size="56"/>
      <ul v-else class="rows">
        <li v-for="row in result.items" :key="row.projectId" class="entry">
          <div class="row-head">
            <strong>{{row.projectCode}} · {{row.projectName}}</strong>
            <div class="lamps" aria-label="周内最近采样">
              <el-tag size="small" :type="tags[row.latest.health]">总体 {{colors[row.latest.health]}}</el-tag>
              <el-tag v-if="row.latest.timeLine" size="small" :type="tags[row.latest.timeLine]">时间 {{colors[row.latest.timeLine]}}</el-tag>
              <el-tag v-if="row.latest.costLine" size="small" :type="tags[row.latest.costLine]">成本 {{colors[row.latest.costLine]}}</el-tag>
              <el-tag v-if="row.latest.qualityLine" size="small" :type="tags[row.latest.qualityLine]">质量 {{colors[row.latest.qualityLine]}}</el-tag>
            </div>
          </div>
          <div class="sample-time">{{time(row.latest.asOf)}} · {{publication[row.latest.publication]}}</div>
          <details v-for="entry in samples(row)" :key="entry.sample.snapshotId">
            <summary>{{entry.label}} · 原因和出处</summary>
            <div class="sample-time" v-if="entry.sample!==row.latest">{{time(entry.sample.asOf)}} · 总体 {{colors[entry.sample.health]}}</div>
            <ul v-if="entry.sample.causes.length||entry.sample.gaps.length" class="causes">
              <li v-for="(cause,i) in entry.sample.causes" :key="i">
                <el-tag size="small" :type="tags[cause.color]">{{labels[cause.line]}}</el-tag>
                {{reasons[cause.code]??cause.code}}<strong v-if="cause.name"> · {{cause.name}}</strong>
                <span v-if="cause.plannedDate"> · 计划 {{cause.plannedDate}}</span>
                <span v-if="cause.forecastDate"> · 预测 {{cause.forecastDate}}</span>
                <span v-if="cause.delayDays"> · 延后 {{cause.delayDays}} 天</span>
                <span v-if="cause.varianceRate"> · 偏差 {{cause.varianceRate==='UNAVAILABLE'?'不可用':`${(Number(cause.varianceRate)*100).toFixed(2)}%`}}</span>
                <small v-for="source in cause.sourceRefs" :key="source"><code>{{source}}</code></small>
              </li>
              <li v-if="entry.sample.omittedCauses">另有 {{entry.sample.omittedCauses}} 项，见项目快照。</li>
              <li v-for="gap in entry.sample.gaps" :key="gap">{{reasons[gap]??gap}}</li>
            </ul>
            <p class="references">快照 <code>{{entry.sample.snapshotId}}</code> · {{entry.sample.ruleVersion}}</p>
            <p class="references" v-for="rule in entry.sample.ruleEvents" :key="rule.alertId">{{rule.ruleId}} · <code>{{rule.alertId}}</code></p>
          </details>
        </li>
      </ul>
      <el-pagination v-if="result.total>result.pageSize" :current-page="result.page" :page-size="result.pageSize" :total="result.total"
        :disabled="busy" layout="prev,pager,next" @current-change="paging"/>
    </template>
  </section>
</template>

<style scoped>
.filters{min-height:3rem;flex-wrap:wrap}.filters label{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}
.period,.row-head,.lamps{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap}
.period{justify-content:space-between;margin:.65rem 0;font-size:.875rem}.period span,.sample-time{color:#506057}
.rows,.causes{list-style:none;margin:0;padding:0}.entry{padding:.85rem .2rem;border-bottom:1px solid #dbe4de;overflow-wrap:anywhere}
.row-head{justify-content:space-between}.sample-time{margin:.35rem 0;font-size:.82rem}
details{margin-top:.55rem}summary{cursor:pointer;color:#285844;font-size:.875rem}.causes li,.references{margin:.35rem 0;font-size:.875rem}
small{display:block}.references code,.causes code{overflow-wrap:anywhere}.causes,.references{padding-inline-start:1rem}
</style>
