<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Plus,RefreshCw} from 'lucide-vue-next'

type Kind='customer'|'partner'|'internal'
type Contact={stakeholderId:string;name:string;type:Kind;expectation?:string;influence?:string}
const labels:Record<Kind,string>={customer:'客户',partner:'合作方',internal:'内部'}
const props=defineProps<{projectId:string;stage:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/stakeholders`, pageSize=20
const page=ref(1),total=ref(0),items=ref<Contact[]>([]),error=ref(''),loading=ref(false),saving=ref(false),editing=ref(false)
const name=ref(''),type=ref<Kind>('customer'),influence=ref(''),expectation=ref('')
let key='',signature='',serial=0
async function load(next=page.value){
  const n=++serial; loading.value=true; error.value=''
  try{const {data}=await props.api(`${path}?page=${next}&pageSize=${pageSize}`)
    if(n===serial){items.value=data.items;total.value=data.total;page.value=next}
  }catch(e){if(n===serial)error.value=e instanceof Error?e.message:'读取失败'}finally{if(n===serial)loading.value=false}
}
function open(){name.value='';type.value='customer';influence.value='';expectation.value='';key='';editing.value=true}
async function save(){
  saving.value=true;error.value=''
  try{const body=JSON.stringify({name:name.value.trim(),stakeholderType:type.value,influence:influence.value.trim(),expectation:expectation.value.trim()})
    if(!key||body!==signature){key=crypto.randomUUID();signature=body}
    await props.api(path,{method:'POST',headers:{'Idempotency-Key':key},body});editing.value=false;await load(1)
  }catch(e){error.value=e instanceof Error?e.message:'保存失败'}finally{saving.value=false}
}
onMounted(()=>load())
</script>
<template>
  <div class="toolbar"><span>项目联系人 · {{total}} 人</span>
    <el-button v-if="['drafting','designing'].includes(stage)" :disabled="editing" @click="open"><Plus :size="15"/>登记联系人</el-button>
    <el-button aria-label="刷新联系人" title="刷新联系人" @click="load()"><RefreshCw :size="15"/></el-button>
  </div>
  <el-alert v-if="error" type="error" :title="error" :closable="false"/>
  <form v-if="editing" @submit.prevent="save">
    <label>联系人姓名<input v-model="name" maxlength="160" required></label>
    <label>类别<select v-model="type" aria-label="联系人类别"><option v-for="(text,value) in labels" :key="value" :value="value">{{text}}</option></select></label>
    <label>影响力<input v-model="influence" maxlength="100"></label>
    <label>期望<textarea v-model="expectation" maxlength="2000"/></label>
    <div class="actions"><el-button :disabled="saving" @click="editing=false">取消</el-button><el-button type="primary" native-type="submit" :loading="saving">保存联系人</el-button></div>
  </form>
  <el-table :data="items" row-key="stakeholderId" v-loading="loading" empty-text="暂无联系人">
    <el-table-column prop="name" label="姓名" min-width="100"/>
    <el-table-column label="类别" width="80"><template #default="{row}">{{labels[(row as Contact).type]}}</template></el-table-column>
    <el-table-column prop="influence" label="影响力"/><el-table-column prop="expectation" label="期望" min-width="150"/>
  </el-table>
  <el-pagination v-if="total>pageSize" :current-page="page" :total="total" :page-size="pageSize" layout="prev,pager,next" @current-change="load"/>
</template>
<style scoped>form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(210px,100%),1fr));gap:12px;padding:14px 0}.actions{grid-column:1/-1}textarea{resize:vertical;min-height:3rem}</style>
