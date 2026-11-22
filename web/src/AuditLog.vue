<script setup lang="ts">
import {ref} from 'vue'
import {ArrowLeft,Eye,ListChecks,Search} from 'lucide-vue-next'
type Entry={auditId:string;createdAt:string;requestId:string;actorType:'human'|'agent';actorId:string;action:string;result:'success'|'denied'|'failed';
  errorCode?:string;delegatedBy?:string;projectId?:string;decisionId?:string;ruleVersion?:string;sourceRefs?:string[];previousHash?:string;responseHash?:string}
const props=defineProps<{api:(path:string)=>Promise<any>}>()
const labels={success:'成功',denied:'拒绝',failed:'失败'}
const show=ref(false),items=ref<Entry[]>([]),rid=ref(''),outcome=ref(''),error=ref(''),busy=ref(false),page=ref(1),total=ref(0)
const selected=ref<Entry|null>(null)
let seq=0,filter=''
function search(){filter=new URLSearchParams([...(rid.value.trim()?[['requestId',rid.value.trim()]]:[]),...(outcome.value?[['result',outcome.value]]:[])]).toString();void load(1)}
async function load(n:number){const id=++seq;busy.value=true;error.value='';selected.value=null;try{
  const {data}=await props.api(`admin/audits?page=${n}&${filter}`);if(id===seq){items.value=data.items;total.value=data.total;page.value=n}
}catch(e){if(id===seq){error.value=e instanceof Error?e.message:'读取失败';items.value=[];total.value=0}}finally{if(id===seq)busy.value=false}}
function open(){rid.value='';outcome.value='';show.value=true;search()}
</script>
<template><el-button @click="open"><ListChecks :size="16"/>审计记录</el-button>
<el-drawer v-model="show" title="组织审计记录" size="min(960px,100vw)" destroy-on-close>
  <section v-if="selected" aria-label="审计详情">
    <el-button :icon="ArrowLeft" @click="selected=null">返回审计记录</el-button>
    <h3>{{selected.action}} · {{labels[selected.result]}}</h3>
    <dl class="audit-details">
      <dt>时间</dt><dd>{{new Date(selected.createdAt).toLocaleString('zh-CN')}}</dd>
      <dt>请求编号</dt><dd><code>{{selected.requestId}}</code></dd>
      <dt>{{selected.actorType==='human'?'操作者账号':'Agent 凭据编号'}}</dt><dd><code>{{selected.actorId}}</code></dd>
      <template v-if="selected.delegatedBy"><dt>委托账号</dt><dd><code>{{selected.delegatedBy}}</code></dd></template>
      <template v-if="selected.projectId"><dt>项目编号</dt><dd><code>{{selected.projectId}}</code></dd></template>
      <template v-if="selected.decisionId"><dt>裁决编号</dt><dd><code>{{selected.decisionId}}</code></dd></template>
      <template v-if="selected.errorCode"><dt>错误码</dt><dd><code>{{selected.errorCode}}</code></dd></template>
      <template v-if="selected.ruleVersion"><dt>规则版本</dt><dd><code>{{selected.ruleVersion}}</code></dd></template>
      <template v-if="selected.previousHash"><dt>变更前摘要</dt><dd aria-label="变更前摘要"><code>{{selected.previousHash}}</code></dd></template>
      <template v-if="selected.responseHash"><dt>响应摘要</dt><dd aria-label="响应摘要"><code>{{selected.responseHash}}</code></dd></template>
      <template v-if="selected.sourceRefs?.length"><dt>来源</dt><dd><ul>
        <li v-for="source in selected.sourceRefs" :key="source"><code>{{source}}</code></li>
      </ul></dd></template>
    </dl>
  </section>
  <template v-else>
  <form class="toolbar" @submit.prevent="search"><label>请求编号<input v-model="rid" maxlength="36"></label>
    <label>结果<select v-model="outcome" aria-label="审计结果"><option value="">全部</option><option v-for="(text,value) in labels" :key="value" :value="value">{{text}}</option></select></label>
    <el-button native-type="submit"><Search :size="15"/>查询记录</el-button></form>
  <el-alert v-if="error" type="error" :title="error" :closable="false"/>
  <el-table v-loading="busy" :data="items" row-key="auditId" empty-text="没有匹配的记录">
    <el-table-column width="56"><template #default="{row}">
      <el-tooltip content="查看审计详情"><el-button text :icon="Eye" aria-label="查看审计详情" @click="selected=row"/></el-tooltip>
    </template></el-table-column>
    <el-table-column label="发生时间" min-width="150"><template #default="{row}">{{new Date(row.createdAt).toLocaleString('zh-CN')}}</template></el-table-column>
    <el-table-column prop="action" label="操作" min-width="170"/><el-table-column label="结果" width="75"><template #default="{row}">{{labels[(row as Entry).result]}}</template></el-table-column>
    <el-table-column label="操作者" width="85"><template #default="{row}">{{row.actorType==='human'?'人工':'Agent'}}</template></el-table-column>
    <el-table-column prop="errorCode" label="错误码" min-width="135"/>
    <el-table-column label="请求 / 裁决编号" min-width="290"><template #default="{row}">
      <div>{{row.requestId}}</div><small v-if="row.decisionId">裁决：{{row.decisionId}}</small>
    </template></el-table-column>
  </el-table><el-pagination :current-page="page" :total="total" :page-size="20" layout="total,prev,pager,next" @current-change="load"/>
  </template>
</el-drawer></template>

<style scoped>
.audit-details{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:8px 16px;font-size:14px}
.audit-details dt{color:var(--el-text-color-secondary)}
.audit-details dd{margin:0;overflow-wrap:anywhere}
.audit-details ul{margin:0;padding-left:18px}
</style>
