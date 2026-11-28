<script setup lang="ts">
import {onBeforeUnmount, ref, watch} from 'vue'

type Kind = 'charter' | 'requirement' | 'risk' | 'review' | 'report'
type Choice = {id:string; label:string}
const props=defineProps<{projectId:string; kind:Kind; disabled:boolean; api:(path:string)=>Promise<any>}>()
const selected=defineModel<string>({required:true})
const titles:Record<Kind,string>={charter:'立项卡',requirement:'需求',risk:'风险',review:'评审',report:'报告'}
const reviewType:Record<string,string>={charter:'立项评审',feasibility:'落地评审',closing:'结项评审'}
const reviewState:Record<string,string>={draft:'草稿',pending:'待评审',decided:'已裁决'}
const reportState:Record<string,string>={draft:'草稿',submitted:'已提交',confirmed:'已发布'}
const entries=ref<Choice[]>([]), page=ref(1), total=ref(0), loading=ref(false), error=ref(''), manual=ref(false)
const size=20
let generation=0
async function load(n:number){
  const request=++generation, kind=props.kind
  const base=`projects/${props.projectId}/`
  const query=`?page=${n}&pageSize=${size}`
  loading.value=true
  error.value=''
  try{
    let choices:Choice[]
    let count:number
    if(kind==='charter'){
      const {data}=await props.api(base+'charter')
      const card=data.charter
      choices=card?[{id:card.charterId,label:`本项目立项卡 · 版本 ${card.revision}${card.targetClient?' · '+card.targetClient:''}`}]:[]
      count=choices.length
    }else{
      const paths={requirement:'requirements',risk:'risks',report:'reports',review:'reviews'}
      const {data}=await props.api(base+paths[kind]+(kind==='review'?'':query))
      count=data.total
      switch(kind){
        case 'requirement': choices=data.items.map((r:{requirementId:string;priority:string;desc:string})=>({id:r.requirementId,label:`${r.priority} · ${r.desc}`}));break
        case 'risk': choices=data.items.map((r:{riskId:string;statement:string})=>({id:r.riskId,label:r.statement}));break
        case 'report': choices=data.items.map((r:{reportId:string;period:string;type:string;versionNo:number;status:string})=>({id:r.reportId,label:`${r.period} · ${r.type==='daily'?'日报':'周报'} v${r.versionNo} · ${reportState[r.status]??r.status}`}));break
        case 'review': choices=data.items.map((r:{reviewId:string;type:string;status:string;revision:number})=>({id:r.reviewId,label:`${reviewType[r.type]??r.type} · ${reviewState[r.status]??r.status} · 版本 ${r.revision}`}));break
      }
    }
    if(request!==generation)return
    entries.value=choices
    total.value=count
    page.value=n
    if(!choices.some(row=>row.id===selected.value))selected.value=''
  }catch(e){
    if(request!==generation)return
    entries.value=[]
    total.value=0
    selected.value=''
    error.value=e instanceof Error?e.message:'无法读取关联资料。'
  }finally{if(request===generation)loading.value=false}
}
function switchEntry(){selected.value='';manual.value=!manual.value}
watch(()=>[props.projectId,props.kind],()=>{selected.value='';manual.value=false;void load(1)},{immediate:true})
onBeforeUnmount(()=>{generation++})
</script>
<template>
  <div class="picker">
    <label v-if="manual">关联资源编号<input v-model.trim="selected" required :disabled="disabled"/></label>
    <template v-else>
      <label>关联{{titles[kind]}}<select v-model="selected" required :disabled="disabled||loading||!!error">
        <option disabled value="">{{loading?'正在读取…':error?'读取失败':entries.length?'请选择本项目的'+titles[kind]:'本项目暂无可选'+titles[kind]}}</option>
        <option v-for="item in entries" :key="item.id" :value="item.id">{{item.label}} · {{item.id.slice(0,8)}}</option>
      </select></label>
      <p v-if="error" role="alert">{{error}}</p>
      <p v-if="kind==='review'&&total>entries.length">列表显示最近 {{entries.length}} 次评审。更早的记录可以使用已知评审编号。</p>
      <div class="tools">
        <el-pagination v-if="kind!=='review'&&total>size" small layout="prev,pager,next" :current-page="page" :page-size="size" :total="total" :disabled="disabled||loading" @current-change="load"/>
        <el-button size="small" :disabled="disabled||loading" @click="load(page)">重读关联资料</el-button>
      </div>
    </template>
    <el-button text size="small" :disabled="disabled||loading" @click="switchEntry">{{manual?'从本项目选择':'使用已知资源编号'}}</el-button>
  </div>
</template>
<style scoped>
.picker{display:grid;gap:5px;min-width:0}.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.picker select{max-width:100%}.picker p{margin:0;font-size:13px}
</style>
