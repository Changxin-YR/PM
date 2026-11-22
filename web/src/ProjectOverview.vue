<script setup lang="ts">
import {onBeforeUnmount,ref,watch} from 'vue'
import {ArrowRight,RefreshCw} from 'lucide-vue-next'

type StageReceipt={fromStage:string;toStage:string;fromRevision:number;toRevision:number;transitionedAt:string;requestId:string;
  reviewId?:string;approvalRequestId?:string}
type Indicators={asOf:string;health:'green'|'yellow'|'red'|'untrusted'|'unavailable';publication:'official'|'preview'|'withheld'|'unavailable';
  timeLine?:'green'|'yellow'|'red';moneyVarianceRate?:string;effortVarianceRate?:string;requirementSatisfactionRate?:string}
type Detail={project:{projectId:string;code:string;stage:string;revision:number};materialsVisible:boolean;indicators?:Indicators;
  charterSummary?:{isFrozen:boolean;revision:number;valueProp?:string;targetClient?:string;roughCost?:string;roughEffort?:string;roughDurationDays?:number};
  recentTransitions:StageReceipt[]}
const props=defineProps<{projectId:string;active:boolean;stages:Record<string,string>;api:(path:string)=>Promise<any>}>()
const emit=defineEmits<{stageRead:[projectId:string,stage:string,revision:number];navigate:[tab:string]}>()
const data=ref<Detail|null>(null),loading=ref(false),error=ref(''),refs=ref<string[]>([])
const colors={green:'绿',yellow:'黄',red:'红',untrusted:'数据不可信',unavailable:'不可用'}
const tags={green:'success',yellow:'warning',red:'danger',untrusted:'warning',unavailable:'info'} as const
const publication={official:'正式快照',preview:'实时预览',withheld:'暂停正式发布',unavailable:'未处于健康监控阶段'}
let sequence=0

function time(value:string){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function ratio(value?:string){return !value||value==='UNAVAILABLE'?'不可用':`${(Number(value)*100).toFixed(2)}%`}
async function read(){
  const id=++sequence;loading.value=true;error.value=''
  try{
    const response:{data:Detail;meta:{sourceRefs:string[]}}=await props.api(`project-overview/${props.projectId}`)
    if(id!==sequence)return
    data.value=response.data;refs.value=response.meta.sourceRefs
    const {project}=response.data
    emit('stageRead',project.projectId,project.stage,project.revision)
  }catch(e){if(id===sequence){data.value=null;refs.value=[];error.value=e instanceof TypeError?'无法连接服务。':e instanceof Error?e.message:'读取失败。'}}
  finally{if(id===sequence)loading.value=false}
}
watch(()=>props.active,active=>{if(active)void read()},{immediate:true})
onBeforeUnmount(()=>{sequence++})
</script>

<template>
  <section aria-label="项目概览" :aria-busy="loading">
    <div class="overview-heading">
      <div v-if="data" class="meta"><strong>{{data.project.code}}</strong><el-tag size="small" type="info" effect="plain">{{stages[data.project.stage]??data.project.stage}}</el-tag><span>版本 {{data.project.revision}}</span></div>
      <span v-else>{{loading?'读取项目概览…':'项目概览'}}</span>
      <el-button aria-label="刷新项目概览" title="刷新项目概览" :disabled="loading" @click="read"><RefreshCw :size="15"/></el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <p v-else-if="data&&!data.materialsVisible" class="muted">当前账号仅可查看项目基本信息。</p>
    <template v-else-if="data">
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
      <section v-if="data.indicators" class="overview-section" aria-label="当前四指标">
        <div class="overview-heading"><h3>当前四指标</h3><el-button text @click="emit('navigate','health')">三线健康 <ArrowRight :size="15"/></el-button></div>
        <div class="meta"><el-tag :type="tags[data.indicators.health]" size="small" effect="plain">{{colors[data.indicators.health]}}</el-tag>
          <span>{{publication[data.indicators.publication]}}</span><time :datetime="data.indicators.asOf">{{time(data.indicators.asOf)}} 北京时间</time></div>
        <dl class="overview-metrics">
          <div><dt>时间线</dt><dd>{{data.indicators.timeLine?colors[data.indicators.timeLine]:'不可用'}}</dd></div>
          <div><dt>金额偏差</dt><dd>{{ratio(data.indicators.moneyVarianceRate)}}</dd></div>
          <div><dt>人天偏差</dt><dd>{{ratio(data.indicators.effortVarianceRate)}}</dd></div>
          <div><dt>需求满足率</dt><dd>{{ratio(data.indicators.requirementSatisfactionRate)}}</dd></div>
        </dl>
      </section>
      <section class="overview-section" aria-label="最近阶段活动">
        <div class="overview-heading"><h3>最近阶段活动</h3><el-button text @click="emit('navigate','lifecycle')">阶段流转 <ArrowRight :size="15"/></el-button></div>
        <p v-if="!data.recentTransitions.length" class="muted">暂无已提交的阶段流转。</p>
        <ol v-else class="edges">
          <li v-for="item in data.recentTransitions" :key="item.requestId">
            <span>{{stages[item.fromStage]??item.fromStage}} <ArrowRight :size="13" aria-hidden="true"/> {{stages[item.toStage]??item.toStage}}</span>
            <span class="muted">版本 {{item.fromRevision}} → {{item.toRevision}} · <time :datetime="item.transitionedAt">{{time(item.transitionedAt)}}</time></span>
          </li>
        </ol>
      </section>
      <details class="overview-section sources"><summary>数据来源</summary><ul><li v-for="source in refs" :key="source"><code>{{source}}</code></li></ul></details>
    </template>
  </section>
</template>

<style scoped>
.overview-heading{display:flex;align-items:center;justify-content:space-between;gap:.5rem;min-height:32px}.overview-heading strong{color:#24362c;overflow-wrap:anywhere}
.overview-section{border-top:1px solid #dce2df;padding:.8rem 0}.overview-section h3{font-size:1rem;margin:.15rem 0 .55rem}
.meta{display:flex;align-items:center;min-width:0;flex-wrap:wrap;gap:.3rem .6rem;color:#516069;font-size:.82rem}.muted{color:#516069;font-size:.84rem;margin:.35rem 0}
.overview-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:.75rem;margin:.7rem 0}.overview-metrics div{border-left:2px solid #d7e2df;padding:0 .55rem;min-width:0}
dt{font-size:.8rem;color:#516069}dd{margin:.25rem 0;font-weight:600;overflow-wrap:anywhere}.purpose,.sources{overflow-wrap:anywhere}.purpose{white-space:pre-wrap}
.edges{margin:.4rem 0;padding-left:1.15rem}.edges li{padding:.25rem 0}.edges li svg{vertical-align:middle}.edges li .muted{display:block}.sources summary{cursor:pointer}
</style>
