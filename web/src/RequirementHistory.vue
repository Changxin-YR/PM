<script setup lang="ts">
import {ref} from 'vue'
import {History} from 'lucide-vue-next'
const props=defineProps<{api:(path:string,options?:RequestInit)=>Promise<any>;id:string;showLabel?:boolean}>()
interface Change{changeId:string;revision:number;baseRevision:number;status:string;reason:string;before:unknown;after:unknown;legacySnapshot?:true;createdAt:string;approvedBy?:string;requestedBy:string;decidedBy?:string;decisionReason?:string;decidedAt?:string;effectiveAt?:string}
const open=ref(false),busy=ref(false),error=ref(''),items=ref<Change[]>([]),page=ref(1),total=ref(0)
const asOf=ref(''),sourceRefs=ref<string[]>([])
const labels:Record<string,string>={draft:'草稿',pending:'待审批',approved:'已批准',rejected:'已拒绝',applied:'已生效',withdrawn:'已撤回'}
function time(value:string){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
async function load(n:number){
  open.value=true;busy.value=true;error.value='';items.value=[];asOf.value='';sourceRefs.value=[]
  try{
    const {data,meta}=await props.api(`requirements/${props.id}/changes?page=${n}&pageSize=10`)
    items.value=data.items;page.value=n;total.value=data.total;asOf.value=data.asOf;sourceRefs.value=meta.sourceRefs
  }catch(e){error.value=e instanceof Error?e.message:'暂时无法读取需求变更历史。'}
  finally{busy.value=false}
}
</script>
<template>
  <el-button :text="!showLabel" aria-label="需求变更历史" title="需求变更历史" @click="load(1)">
    <History :size="16" aria-hidden="true"/><span v-if="showLabel">需求变更历史</span>
  </el-button>
  <el-dialog v-model="open" append-to-body title="需求变更历史" width="min(620px,95vw)">
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <p v-if="asOf">截至 <time :datetime="asOf">{{time(asOf)}} 北京时间</time> · 共 {{total}} 条</p>
    <div v-loading="busy">
      <p v-if="!items.length&&!busy&&!error">暂无变更记录</p>
      <article v-for="i in items" :key="i.changeId">
        <strong>{{labels[i.status]??i.status}} · {{i.reason}}</strong>
        <p v-if="i.legacySnapshot">旧快照 · 非现行格式</p>
        <p>申请时间 <time :datetime="i.createdAt">{{time(i.createdAt)}}</time> · 基准版本 {{i.baseRevision}} · 记录版本 {{i.revision}}</p>
        <dl>
          <dt>修改前</dt><dd><pre>{{JSON.stringify(i.before,null,2)}}</pre></dd>
          <dt>修改后</dt><dd><pre>{{JSON.stringify(i.after,null,2)}}</pre></dd>
          <dt>申请人</dt><dd>{{i.requestedBy}}</dd>
          <dt>审批人</dt><dd>{{i.approvedBy??'—'}}</dd>
          <template v-if="i.decidedBy">
            <dt>裁决人</dt><dd>{{i.decidedBy}}</dd>
            <dt>裁决理由</dt><dd>{{i.decisionReason}}</dd>
          </template>
          <template v-if="i.decidedAt"><dt>裁决时间</dt><dd><time :datetime="i.decidedAt">{{time(i.decidedAt)}}</time></dd></template>
          <dt>生效时间</dt><dd><time v-if="i.effectiveAt" :datetime="i.effectiveAt">{{time(i.effectiveAt)}}</time><span v-else>—</span></dd>
        </dl>
      </article>
    </div>
    <details v-if="sourceRefs.length"><summary>本页来源</summary><ul><li v-for="source in sourceRefs" :key="source"><code>{{source}}</code></li></ul></details>
    <el-pagination v-if="!error&&total>10" :current-page="page" :page-size="10" :total="total" :disabled="busy" layout="prev,pager,next" @current-change="load"/>
  </el-dialog>
</template>
<style scoped>
article {padding:12px 0;border-bottom:1px solid var(--pmo-line)}
p {font-size:13px;color:var(--pmo-secondary)}
dl {display:grid;grid-template-columns:5rem minmax(0,1fr);gap:8px}
dd,pre {margin:0;overflow-wrap:anywhere;white-space:pre-wrap}
details {margin-top:12px;overflow-wrap:anywhere}
</style>
