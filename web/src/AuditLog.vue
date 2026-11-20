<script setup lang="ts">
import {ref} from 'vue'
import {ListChecks,Search} from 'lucide-vue-next'
type Entry={auditId:string;createdAt:string;requestId:string;actorType:'human'|'agent';actorId:string;action:string;result:'success'|'denied'|'failed';errorCode?:string}
const props=defineProps<{api:(path:string)=>Promise<any>}>()
const labels={success:'成功',denied:'拒绝',failed:'失败'}
const show=ref(false),items=ref<Entry[]>([]),rid=ref(''),outcome=ref(''),error=ref(''),busy=ref(false),page=ref(1),total=ref(0)
let seq=0,filter=''
function search(){filter=new URLSearchParams([...(rid.value.trim()?[['requestId',rid.value.trim()]]:[]),...(outcome.value?[['result',outcome.value]]:[])]).toString();void load(1)}
async function load(n:number){const id=++seq;busy.value=true;error.value='';try{
  const {data}=await props.api(`admin/audits?page=${n}&${filter}`);if(id===seq){items.value=data.items;total.value=data.total;page.value=n}
}catch(e){if(id===seq){error.value=e instanceof Error?e.message:'读取失败';items.value=[];total.value=0}}finally{if(id===seq)busy.value=false}}
function open(){rid.value='';outcome.value='';show.value=true;search()}
</script>
<template><el-button @click="open"><ListChecks :size="16"/>审计记录</el-button>
<el-drawer v-model="show" title="组织审计记录" size="min(960px,100vw)" destroy-on-close>
  <form class="toolbar" @submit.prevent="search"><label>请求编号<input v-model="rid" maxlength="36"></label>
    <label>结果<select v-model="outcome" aria-label="审计结果"><option value="">全部</option><option v-for="(text,value) in labels" :key="value" :value="value">{{text}}</option></select></label>
    <el-button native-type="submit"><Search :size="15"/>查询记录</el-button></form>
  <el-alert v-if="error" type="error" :title="error" :closable="false"/>
  <el-table v-loading="busy" :data="items" row-key="auditId" empty-text="没有匹配的记录">
    <el-table-column label="发生时间" min-width="150"><template #default="{row}">{{new Date(row.createdAt).toLocaleString('zh-CN')}}</template></el-table-column>
    <el-table-column prop="action" label="操作" min-width="170"/><el-table-column label="结果" width="75"><template #default="{row}">{{labels[(row as Entry).result]}}</template></el-table-column>
    <el-table-column label="操作者" width="85"><template #default="{row}">{{row.actorType==='human'?'人工':'Agent'}}</template></el-table-column>
    <el-table-column prop="errorCode" label="错误码" min-width="135"/><el-table-column prop="requestId" label="请求编号" min-width="290"/>
  </el-table><el-pagination :current-page="page" :total="total" :page-size="20" layout="total,prev,pager,next" @current-change="load"/>
</el-drawer></template>
