<script setup lang="ts">
import {onMounted,ref} from 'vue'

type Posting={postingId:string;period:string;sourceRef:string;amountSigned:string;type:'money'|'effort';status:string}
const props=defineProps<{projectId:string;disabled:boolean;api:(path:string)=>Promise<any>}>()
const selected=defineModel<string>({required:true})
const rows=ref<Posting[]>([]),page=ref(1),total=ref(0),loading=ref(true),error=ref(''),manual=ref(false)
const pageSize=20
async function load(n:number){
  loading.value=true
  error.value=''
  try{
    const {data}=await props.api(`projects/${props.projectId}/cost-postings?page=${n}&pageSize=${pageSize}`)
    rows.value=data.items
    page.value=data.page
    total.value=data.total
    if(!rows.value.some(row=>row.postingId===selected.value))selected.value=''
  }catch(err){
    rows.value=[]
    selected.value=''
    error.value=err instanceof Error?err.message:'无法读取本项目的成本凭证。'
  }finally{loading.value=false}
}
function changeMode(){selected.value='';manual.value=!manual.value}
onMounted(()=>load(1))
</script>
<template>
  <div class="posting-picker">
    <label v-if="manual">关联资源编号<input v-model.trim="selected" required :disabled="disabled"/></label>
    <template v-else>
      <label>关联成本凭证<select v-model="selected" required :disabled="disabled||loading||!!error">
        <option disabled value="">{{loading?'正在读取凭证…':error?'凭证读取失败':rows.length?'请选择本项目的凭证':'本项目暂无可选凭证'}}</option>
        <option v-for="row in rows" :key="row.postingId" :value="row.postingId">{{row.period}} · {{row.sourceRef}} · {{row.amountSigned}}{{row.type==='money'?'元':'人天'}} · {{row.status==='verified'?'已核验':'待核验'}} · {{row.postingId.slice(0,8)}}</option>
      </select></label>
      <p v-if="error" role="alert">{{error}}</p>
      <div class="actions">
        <el-pagination v-if="total>pageSize" small layout="prev,pager,next" :current-page="page" :page-size="pageSize" :total="total" :disabled="disabled||loading" @current-change="load"/>
        <el-button size="small" :disabled="disabled||loading" @click="load(page)">重读凭证</el-button>
      </div>
    </template>
    <el-button text size="small" :disabled="disabled||loading" @click="changeMode">{{manual?'从项目凭证选择':'使用已知凭证编号'}}</el-button>
  </div>
</template>
