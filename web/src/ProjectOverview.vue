<script setup lang="ts">
import {onBeforeUnmount,ref,watch} from 'vue'
import {ArrowRight,Info} from 'lucide-vue-next'

type StageReceipt={fromStage:string;toStage:string;fromRevision:number;toRevision:number;transitionedAt:string;requestId:string;
  reviewId?:string;approvalRequestId?:string}
const activityNames={risk_created:'风险已登记',change_requested:'变更已提交',change_applied:'变更已生效',change_rejected:'变更已拒绝'}
type Activity={kind:keyof typeof activityNames;resourceId:string;occurredAt:string}
type Indicators={asOf:string;health:'green'|'yellow'|'red'|'untrusted'|'unavailable';publication:'official'|'preview'|'withheld'|'unavailable';
  timeLine?:'green'|'yellow'|'red';moneyVarianceRate?:string;effortVarianceRate?:string;requirementSatisfactionRate?:string}
type Identity={projectId:string;code:string;name:string;stage:string;revision:number}
type OverdueCondition={taskId:string;title:string;dueAt:string}
type RuleNotification={alertId:string;ruleId:'LR-04'|'LR-08';status:'open'|'resolved';deliveredAt:string}
type Detail={project:Identity;materialsVisible:boolean;indicators?:Indicators;
  charterSummary?:{isFrozen:boolean;revision:number;valueProp?:string;targetClient?:string;roughCost?:string;roughEffort?:string;roughDurationDays?:number};
  recentTransitions:StageReceipt[];recentActivity?:Activity[];overdueConditions?:OverdueCondition[];overdueConditionCount?:number;
  ruleNotifications?:RuleNotification[]}
const props=defineProps<{projectId:string;active:boolean;stages:Record<string,string>;api:(path:string)=>Promise<any>}>()
const emit=defineEmits<{projectRead:[project:Identity];navigate:[tab:string];openChange:[changeId:string]}>()
const data=ref<Detail|null>(null),loading=ref(false),error=ref(''),refs=ref<string[]>([])
const colors={green:'绿',yellow:'黄',red:'红',untrusted:'数据不可信',unavailable:'不可用'}
const tags={green:'success',yellow:'warning',red:'danger',untrusted:'warning',unavailable:'info'} as const
const publication={official:'正式快照',preview:'实时预览',withheld:'暂停正式发布',unavailable:'未处于健康监控阶段'}
const ruleMessages={'LR-04':'项目成本超过规则阈值','LR-08':'结项后的复盘或教训未按期完成'}
let sequence=0

function time(value:string){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function ratio(value?:string){return !value||value==='UNAVAILABLE'?'不可用':`${(Number(value)*100).toFixed(2)}%`}
async function read(){
  const id=++sequence;loading.value=true;error.value=''
  try{
    const response:{data:Detail;meta:{sourceRefs:string[]}}=await props.api(`project-overview/${props.projectId}`)
    if(id!==sequence)return
    data.value=response.data;refs.value=response.meta.sourceRefs
    emit('projectRead',response.data.project)
  }catch(e){if(id===sequence){data.value=null;refs.value=[];error.value=e instanceof TypeError?'无法连接服务。':e instanceof Error?e.message:'读取失败。'}}
  finally{if(id===sequence)loading.value=false}
}
watch(()=>props.active,active=>{if(active)void read()},{immediate:true})
onBeforeUnmount(()=>{sequence++})
defineExpose({refresh:read,loading})
</script>

<template>
  <section class="overview" aria-label="项目概览" :aria-busy="loading">
    <section v-if="data?.ruleNotifications?.length" class="overview-section" aria-label="我的项目提醒">
      <h3>我的项目提醒</h3>
      <ul class="edges">
        <li v-for="item in data.ruleNotifications" :key="item.alertId">
          <span>{{ruleMessages[item.ruleId]}} · {{item.ruleId}}</span>
          <el-tag class="rule-receipt" :type="item.status==='open'?'warning':'info'" effect="plain" size="small">{{item.status==='open'?'待处理':'已解除'}}</el-tag>
          <time class="muted" :datetime="item.deliveredAt">{{time(item.deliveredAt)}} 北京时间</time>
        </li>
      </ul>
    </section>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-else-if="loading&&!data" class="overview-section" role="status">
      <p class="muted">读取项目概览…</p><el-skeleton :rows="3" animated/>
    </div>
    <p v-else-if="data&&!data.materialsVisible" class="overview-message"><Info :size="16" aria-hidden="true"/>当前账号仅可查看项目基本信息。</p>
    <template v-else-if="data">
      <section v-if="data.indicators" class="overview-section" aria-label="当前四指标">
        <div class="overview-heading"><h3>当前四指标</h3><el-button text @click="emit('navigate','health')">三线健康 <ArrowRight :size="15"/></el-button></div>
        <div class="meta indicator-status" :data-health="data.indicators.health"><el-tag :type="tags[data.indicators.health]" size="small" effect="plain">{{colors[data.indicators.health]}}</el-tag>
          <span>{{publication[data.indicators.publication]}}</span><time :datetime="data.indicators.asOf">{{time(data.indicators.asOf)}} 北京时间</time></div>
        <dl class="overview-metrics indicators">
          <div><dt>时间线</dt><dd>{{data.indicators.timeLine?colors[data.indicators.timeLine]:'不可用'}}</dd></div>
          <div><dt>金额偏差</dt><dd>{{ratio(data.indicators.moneyVarianceRate)}}</dd></div>
          <div><dt>人天偏差</dt><dd>{{ratio(data.indicators.effortVarianceRate)}}</dd></div>
          <div><dt>需求满足率</dt><dd>{{ratio(data.indicators.requirementSatisfactionRate)}}</dd></div>
        </dl>
      </section>
      <section class="overview-section" aria-label="立项概要">
        <h3>立项概要</h3>
        <template v-if="data.charterSummary">
          <dl class="overview-metrics">
            <div><dt>目标客户</dt><dd>{{data.charterSummary.targetClient??'未填写'}}</dd></div>
            <div><dt>初估金额</dt><dd>{{data.charterSummary.roughCost!=null?`${data.charterSummary.roughCost} 元`:'未填写'}}</dd></div>
            <div><dt>初估人天</dt><dd>{{data.charterSummary.roughEffort??'未填写'}}</dd></div>
            <div><dt>初估工期</dt><dd>{{data.charterSummary.roughDurationDays!=null?`${data.charterSummary.roughDurationDays} 天`:'未填写'}}</dd></div>
          </dl>
          <p v-if="data.charterSummary.valueProp" class="purpose">{{data.charterSummary.valueProp}}</p>
          <p class="muted">立项卡版本 {{data.charterSummary.revision}} · {{data.charterSummary.isFrozen?'已冻结':'未冻结'}}</p>
        </template>
        <p v-else class="muted">尚未填写立项卡。</p>
      </section>
      <section v-if="data.overdueConditions?.length" class="overview-section" aria-label="落地补强逾期">
        <div class="overview-heading">
          <h3>落地补强逾期 <el-tag type="warning" effect="plain" size="small">{{data.overdueConditionCount}} 项</el-tag></h3>
          <el-button text @click="emit('navigate','tasks')">查看任务 <ArrowRight :size="15"/></el-button>
        </div>
        <ul class="edges">
          <li v-for="item in data.overdueConditions" :key="item.taskId">
            <span>{{item.title}}</span>
            <time class="muted" :datetime="item.dueAt">截止于 {{time(item.dueAt)}} 北京时间</time>
          </li>
        </ul>
        <p v-if="(data.overdueConditionCount??0)>data.overdueConditions.length" class="muted">显示最早到期的 {{data.overdueConditions.length}} 项。</p>
      </section>
      <div class="overview-activity">
      <section class="overview-section" aria-label="最近阶段活动">
        <div class="overview-heading"><h3>最近阶段活动</h3><el-button text @click="emit('navigate','lifecycle')">阶段流转 <ArrowRight :size="15"/></el-button></div>
        <p v-if="!data.recentTransitions.length" class="muted">暂无已提交的阶段流转。</p>
        <ol v-else class="edges timeline" role="list">
          <li v-for="item in data.recentTransitions" :key="item.requestId">
            <span>{{stages[item.fromStage]??item.fromStage}} <ArrowRight :size="13" aria-hidden="true"/> {{stages[item.toStage]??item.toStage}}</span>
            <span class="muted">版本 {{item.fromRevision}} → {{item.toRevision}} · <time :datetime="item.transitionedAt">{{time(item.transitionedAt)}}</time></span>
          </li>
        </ol>
      </section>
      <section class="overview-section" aria-label="最近台账活动">
        <h3>最近台账活动</h3>
        <p v-if="!data.recentActivity?.length" class="muted">暂无已提交的台账活动。</p>
        <ol v-else class="edges activity" role="list">
          <li v-for="item in data.recentActivity" :key="`${item.kind}:${item.resourceId}`">
            <div><span>{{activityNames[item.kind]}}</span><time class="muted" :datetime="item.occurredAt">{{time(item.occurredAt)}} 北京时间</time></div>
            <el-button v-if="item.kind==='risk_created'" text @click="emit('navigate','risks')">风险台账 <ArrowRight :size="14"/></el-button>
            <el-button v-else text @click="emit('openChange',item.resourceId)">查看变更 <ArrowRight :size="14"/></el-button>
          </li>
        </ol>
      </section>
      </div>
      <details class="overview-section sources"><summary>数据来源</summary><ul><li v-for="source in refs" :key="source"><code>{{source}}</code></li></ul></details>
    </template>
  </section>
</template>

<style scoped>
.overview {display:grid;gap:12px;min-width:0;container-type:inline-size}
.overview-heading {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;min-height:32px}
.overview-section {padding:16px;background:var(--pmo-surface);border:1px solid var(--pmo-line);border-radius:var(--pmo-radius-panel);min-width:0}
.overview-section h3 {font-size:16px;font-weight:600;margin:0}
.overview-section > h3 {margin-bottom:12px}
.meta {display:flex;align-items:center;min-width:0;flex-wrap:wrap;gap:4px 8px;color:var(--pmo-muted);font-size:13px}
.muted {color:var(--pmo-muted);font-size:13px;margin:4px 0}
.indicator-status,.overview-message {padding:8px 12px;background:var(--pmo-bg);border:1px solid var(--pmo-line);border-radius:var(--pmo-radius-control)}
.indicator-status[data-health="red"]{background:var(--el-color-danger-light-9);border-color:var(--el-color-danger-light-8);color:var(--el-color-danger)}
.indicator-status[data-health="yellow"],.indicator-status[data-health="untrusted"]{background:var(--el-color-warning-light-9);border-color:var(--el-color-warning-light-8);color:var(--el-color-warning)}
.overview-message {display:flex;align-items:center;gap:8px;color:var(--pmo-secondary);margin:0}
.indicator-status {margin-top:8px}
.overview-metrics {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:12px 0 4px}
.overview-metrics div + div {border-inline-start:1px solid var(--pmo-line);padding-inline-start:12px}
dt {font-size:13px;color:var(--pmo-secondary)}
dd {margin:4px 0 0;font-size:17px;font-weight:600;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.indicators dd {font-size:22px}
.purpose,.sources {overflow-wrap:anywhere}
.purpose {white-space:pre-wrap;margin:12px 0 4px}
.overview-activity {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}
.edges {margin:4px 0 0;padding-inline-start:24px}
.edges li {padding:8px 0;border-bottom:1px solid var(--pmo-line);overflow-wrap:anywhere}
.edges li::marker {color:var(--pmo-primary)}
.edges li:last-child {border-bottom:0;padding-bottom:0}
.edges li svg {vertical-align:middle}
.edges li .muted {display:block}
.timeline {border-inline-start:1px solid var(--pmo-line);margin-inline-start:6px;padding-inline-start:16px;list-style:none}
.timeline li {position:relative}
.timeline li::before {content:"";position:absolute;inset-inline-start:-21px;inset-block-start:15px;width:9px;height:9px;border:2px solid var(--pmo-primary);border-radius:50%;background:var(--pmo-surface)}
.rule-receipt {margin-inline-start:8px}
.sources summary {cursor:pointer;font-weight:600}
.activity {list-style:none;padding:0}
.activity li {display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:0 8px}
.activity li > div {min-width:0}
.activity button {flex:none}
@container (max-width:740px) {
  .overview-activity {grid-template-columns:1fr}
  .overview-metrics {grid-template-columns:repeat(2,minmax(0,1fr))}
  .overview-metrics div:nth-child(odd) {border-inline-start:0;padding-inline-start:0}
}
</style>
