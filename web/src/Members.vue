<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {RefreshCw} from 'lucide-vue-next'
type Member={personId:string;name:string;role:string;acceptedAt:string}
const props=defineProps<{projectId:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const members=ref<Member[]>([]),page=ref(1),total=ref(0),loading=ref(false),error=ref('')
const roles:Record<string,string>={initiator:'草稿发起人',sponsor:'Sponsor',pm:'项目经理',reviewer:'评审人',client_representative:'客户代表'}
let seq=0
async function load(next=page.value){
 const n=++seq;loading.value=true;error.value=''
 try{const {data}=await props.api(`projects/${props.projectId}/members?page=${next}`)
   if(n===seq){members.value=data.items;total.value=data.total;page.value=next}
 }catch(e){if(n===seq)error.value=e instanceof Error?e.message:'读取失败'}finally{if(n===seq)loading.value=false}
}
onMounted(()=>load())
</script>
<template><section class="panel member-panel"><div class="toolbar"><span>有效成员角色 · {{total}}</span><el-button title="刷新项目成员" aria-label="刷新项目成员" @click="load()"><RefreshCw :size="15"/></el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/>
<el-table :data="members" v-loading="loading" empty-text="暂无有效成员"><el-table-column prop="name" label="姓名"/><el-table-column label="项目角色"><template #default="{row}">{{roles[row.role]??row.role}}</template></el-table-column><el-table-column label="生效日期"><template #default="{row}">{{new Date(row.acceptedAt).toLocaleDateString('zh-CN')}}</template></el-table-column></el-table>
<el-pagination v-if="total>20" :current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="load"/></section></template>
