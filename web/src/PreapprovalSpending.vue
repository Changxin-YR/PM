<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {RefreshCw} from 'lucide-vue-next'
type Row={postingId:string;projectCode:string;amountSigned:string;type:'money'|'effort';occurredAt:string;approvedAt?:string;sourceRef:string}
const props=defineProps<{api:(p:string,o?:RequestInit)=>Promise<any>}>()
const busy=ref(false),error=ref(''),rows=ref<Row[]>([]),total=ref(0),page=ref(1)
let sequence=0
async function load(n:number){const id=++sequence;busy.value=true;error.value='';try{const {data}=await props.api(`analytics/preapproval-spending?page=${n}`);if(id===sequence){rows.value=data.items;total.value=data.total;page.value=n}}catch(e){if(id===sequence){rows.value=[];total.value=0;error.value=String(e)}}finally{if(id===sequence)busy.value=false}}
onMounted(()=>void load(1))
</script>
<template>
<section class="panel" aria-label="立项前支出">
<div class="toolbar"><span v-if="!error&&!busy">已核验流水 · {{total}}</span><el-button title="刷新支出" aria-label="刷新支出" :disabled="busy" @click="load(page)"><RefreshCw :size="15"/> 刷新</el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/><el-table v-else :data="rows" v-loading="busy" row-key="postingId" :empty-text="busy?'正在读取…':'没有符合条件的已核验流水'"><el-table-column prop="projectCode" label="项目编号" min-width="110"/><el-table-column prop="sourceRef" label="凭证来源" min-width="140"/><el-table-column label="数额" min-width="100"><template #default="{row}">{{row.amountSigned}} {{row.type==='money'?'元':'人天'}}</template></el-table-column>
<el-table-column label="实际发生时间" min-width="160"><template #default="{row}">{{new Date(row.occurredAt).toLocaleString('zh-CN')}}</template></el-table-column><el-table-column label="立项通过时间" min-width="160"><template #default="{row}">{{row.approvedAt?new Date(row.approvedAt).toLocaleString('zh-CN'):'尚未通过'}}</template></el-table-column></el-table>
<el-pagination v-if="total>20" :disabled="busy" :total="total" :page-size="20" :current-page="page" layout="prev,pager,next" @current-change="load"/>
</section>
</template>
