<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref} from 'vue'
import {Search} from 'lucide-vue-next'
import RequirementHistory from './RequirementHistory.vue'

type Requirement={requirementId:string;desc:string}
const props=defineProps<{projectId:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const choices=ref<Requirement[]>([]),selectedId=ref(''),query=ref(''),page=ref(1),total=ref(0)
const busy=ref(false),error=ref('')
const selected=computed(()=>choices.value.find(item=>item.requirementId===selectedId.value))
const pageSize=10
let keyword='',request:AbortController|undefined

async function read(n=1){
  request?.abort()
  const next=new AbortController()
  request=next
  selectedId.value=''
  choices.value=[]
  busy.value=true
  error.value=''
  const filters=new URLSearchParams({page:String(n),pageSize:String(pageSize),'filters[keyword]':keyword})
  try{
    const {data}=await props.api(`projects/${props.projectId}/requirements?${filters}`,{signal:next.signal})
    if(request!==next)return
    page.value=n
    choices.value=data.items
    total.value=data.total
  }catch(e){
    if(request===next&&!next.signal.aborted)error.value=e instanceof Error?e.message:'暂时无法读取项目需求。'
  }finally{
    if(request===next)busy.value=false
  }
}

function search(){keyword=query.value.trim();void read()}
onMounted(()=>void read())
onBeforeUnmount(()=>request?.abort())
</script>

<template>
  <section class="panel history-picker" aria-label="需求变更历史查询" :aria-busy="busy">
    <h3>需求变更历史</h3>
    <form class="history-controls" @submit.prevent="search">
      <label>需求关键词<input v-model="query" maxlength="160" autocomplete="off"></label>
      <el-button native-type="submit" :loading="busy"><Search :size="15"/>查询需求</el-button>
    </form>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <p v-else-if="busy" role="status">正在读取项目需求…</p>
    <p v-else-if="!choices.length">当前条件下没有匹配的需求。</p>
    <label v-else>选择需求<select v-model="selectedId">
      <option value="">请选择当前项目需求</option>
      <option v-for="item in choices" :key="item.requirementId" :value="item.requirementId">{{item.desc}} · {{item.requirementId}}</option>
    </select></label>
    <el-pagination v-if="!error&&total>pageSize" :current-page="page" :page-size="pageSize" :total="total" :disabled="busy"
      layout="prev, pager, next" @current-change="read"/>
    <div v-if="selected" class="history-target">
      <p>{{selected.desc}}</p>
      <p>需求编号 <code>{{selected.requirementId}}</code></p>
      <RequirementHistory :key="selected.requirementId" :api="api" :id="selected.requirementId" show-label/>
    </div>
  </section>
</template>

<style scoped>
.history-picker {display:grid;min-width:0;gap:12px}
.history-picker h3,.history-picker p {margin:0}
.history-controls {display:flex;align-items:end;gap:8px;flex-wrap:wrap}
.history-controls label {flex:0 1 20rem;min-width:0}
.history-picker select {max-width:100%}
.history-target {min-width:0;overflow-wrap:anywhere}
.history-target p + p {color:var(--pmo-muted);margin-top:4px}
</style>
