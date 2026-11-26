<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Download,RefreshCw,X} from 'lucide-vue-next'

type Evidence={evidenceId:string;evidenceRef:string;projectId:string;originalName:string;mimeType:string;sizeBytes:number;
  status:string;linkedResourceType:string;linkedResourceId:string;description:string}
type Page={items:Evidence[];page:number;total:number}
const props=defineProps<{projectId:string;resourceType:'project'|'risk'|'requirement'|'report'|'cost_posting';resourceId?:string;resourceIds?:string[];label:string;
  api:(path:string,options?:RequestInit)=>Promise<any>;mimeType?:string;maxBytes?:number;limit?:number;disabled?:boolean}>()
const selected=defineModel<string[]>({required:true})
const page=ref(1),total=ref(0),pageSize=40,loading=ref(false),error=ref(''),rows=ref<Evidence[]>([])
const names=ref<Record<string,string>>({})

async function load(next=page.value){
  if(loading.value)return
  loading.value=true;error.value=''
  try{
    const q=new URLSearchParams({verifiedOnly:'true',page:String(next),pageSize:String(pageSize)})
    if(props.resourceType==='report'&&props.resourceId)q.set('reportId',props.resourceId)
    const {data}:{data:Page}=await props.api(`projects/${props.projectId}/evidence?${q}`)
    rows.value=data.items.filter(item=>item.status==='verified'&&item.projectId===props.projectId&&
      item.linkedResourceType===props.resourceType&&(item.linkedResourceId===props.resourceId||props.resourceIds?.includes(item.linkedResourceId))&&
      (!props.mimeType||item.mimeType===props.mimeType)&&(!props.maxBytes||item.sizeBytes<=props.maxBytes))
    page.value=data.page;total.value=data.total
  }catch(e){rows.value=[];error.value=e instanceof Error?e.message:'未能读取项目附件。'}
  finally{loading.value=false}
}

function remove(source:string){selected.value=selected.value.filter(ref=>ref!==source)}
function pick(item:Evidence){
  if(props.disabled||loading.value)return
  const ref=item.evidenceRef,limit=props.limit??1
  if(limit===1)selected.value=[ref]
  else if(selected.value.includes(ref))remove(ref)
  else if(selected.value.length<limit)selected.value=[...selected.value,ref]
  names.value[ref]=item.originalName
}
onMounted(()=>load(1))
</script>

<template>
  <fieldset class="evidence-picker">
    <legend>{{label}}</legend>
    <div class="picker-tools">
      <span class="meta">已选 {{selected.length}} / {{limit??1}}</span>
      <el-button size="small" title="重读已核验附件" aria-label="重读已核验附件" :disabled="disabled||loading" @click="load()"><RefreshCw :size="14"/></el-button>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
    <ul v-if="selected.length" class="selected-list" aria-label="已选附件">
      <li v-for="ref in selected" :key="ref">
        <span>{{names[ref]??ref}}</span>
        <el-button text size="small" :disabled="disabled" :aria-label="`移除 ${names[ref]??ref}`" @click="remove(ref)"><X :size="14"/></el-button>
      </li>
    </ul>
    <div class="options" v-loading="loading">
      <p v-if="!rows.length&&!error&&!loading" class="meta">{{total>pageSize?'本页没有符合条件的附件':'暂无符合条件的已核验附件'}}</p>
      <div v-for="item in rows" :key="item.evidenceId" class="option-row">
        <label class="option">
          <input type="checkbox" :checked="selected.includes(item.evidenceRef)"
            :disabled="disabled||loading||((limit??1)>1&&selected.length>=(limit??1)&&!selected.includes(item.evidenceRef))" @change="selected.includes(item.evidenceRef)?remove(item.evidenceRef):pick(item)"/>
          <span>{{item.originalName}} <small v-if="item.description"> · {{item.description}}</small><code>{{item.evidenceRef}}</code></span>
        </label>
        <a :href="`/api/v1/evidence/${item.evidenceId}?download=true`" download :aria-label="`下载原件 ${item.originalName}`" title="下载原件"><Download :size="15"/></a>
      </div>
    </div>
    <el-pagination v-if="total>pageSize" :current-page="page" :total="total" :page-size="pageSize"
      layout="prev,pager,next" small :disabled="disabled||loading" @current-change="load"/>
  </fieldset>
</template>

<style scoped>
.evidence-picker{border:1px solid var(--el-border-color);border-radius:var(--el-border-radius-base);padding:.6rem .75rem;margin:0;min-width:0}
legend{padding:0 .25rem;font-weight:600}
.picker-tools,.selected-list li,.option-row{display:flex;justify-content:space-between;align-items:center;gap:.6rem}
.selected-list{list-style:none;padding:0;margin:.35rem 0;overflow-wrap:anywhere}
.options{min-height:2rem;max-height:12rem;overflow:auto}
.option-row{border-top:1px solid var(--el-border-color-lighter);padding:.35rem 0}
.option{display:flex;gap:.45rem;align-items:baseline;min-width:0;overflow-wrap:anywhere}
.option input{width:auto;flex:none}
.option code{display:block;font-size:12px;color:var(--el-text-color-secondary)}
.option-row a{display:inline-flex;color:var(--pmo-primary-hover);padding:8px}
.meta{color:var(--el-text-color-secondary);font-size:13px}
</style>
