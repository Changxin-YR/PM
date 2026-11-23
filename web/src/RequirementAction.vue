<script setup lang="ts">
import {computed,ref} from 'vue'
import {FileCheck2,RotateCcw} from 'lucide-vue-next'
import {isRejectedWrite,isUnconfirmedWrite} from './api-error'
const props=defineProps<{api:(path:string,options?:RequestInit)=>Promise<any>;projectId:string;row:{requirementId:string;revision:number;status:string;desc:string;acceptanceCriteria:string};actions:string[]}>()
const emit=defineEmits<{saved:[]}>()
type Attempt={path:string;revision:number;body:string;key:string}
const steps:Record<string,[string,string]>={baseline:['deliver','标记交付'],delivered:['accept','内部验收'],accepted:['customer-confirm','客户确认']}
const open=ref(false),busy=ref(false),error=ref(''),selected=ref(''),page=ref(1),total=ref(0),files=ref<{originalName:string;evidenceRef:string;evidenceId:string}[]>([])
const editing=ref<{action:string;label:string;revision:number}|null>(null)
const retry=ref<Attempt|null>(null)
const locked=computed(()=>busy.value||!!retry.value)
const canSubmit=computed(()=>{
  const current=editing.value
  return !!selected.value&&!!current&&props.row.revision===current.revision&&props.actions.includes(current.action)
})

async function load(n:number){
  if(locked.value)return
  busy.value=true;error.value='';selected.value='';files.value=[]
  try{
    const q=new URLSearchParams({page:String(n),requirementId:props.row.requirementId,verifiedOnly:'true',currentBaselineOnly:'true'})
    const {data}=await props.api(`projects/${props.projectId}/evidence?${q}`)
    files.value=data.items;page.value=n;total.value=data.total
  }catch(e){error.value=e instanceof Error?e.message:'未能读取已核验证据。'}
  finally{busy.value=false}
}
async function begin(){
  const step=steps[props.row.status]
  if(locked.value||!step||!props.actions.includes(step[0]))return
  editing.value={action:step[0],label:step[1],revision:props.row.revision}
  open.value=true
  await load(1)
}
function submit(){
  const current=editing.value
  if(locked.value||!current||!canSubmit.value)return
  void send({path:`requirements/${props.row.requirementId}/${current.action}`,revision:current.revision,
    body:JSON.stringify({evidenceRef:selected.value}),key:crypto.randomUUID()})
}
async function send(attempt:Attempt){
  if(busy.value)return
  busy.value=true;error.value=''
  try{
    await props.api(attempt.path,{method:'POST',body:attempt.body,
      headers:{'If-Match':String(attempt.revision),'Idempotency-Key':attempt.key}})
    retry.value=null;open.value=false;emit('saved')
  }catch(e){
    if(isUnconfirmedWrite(e)){
      retry.value=attempt;error.value='未能确认提交结果，可按原请求重试。'
    }else{
      retry.value=null
      if(isRejectedWrite(e))emit('saved')
      error.value=e instanceof Error?e.message:'未能提交需求操作。'
    }
  }finally{busy.value=false}
}
</script>
<template>
  <el-button v-if="steps[row.status]&&actions.includes(steps[row.status][0])" text :title="steps[row.status][1]"
    :disabled="locked" @click="begin"><FileCheck2 :size="16"/>{{steps[row.status][1]}}</el-button>
  <span v-else-if="row.status==='accepted'&&actions.includes('policy-pending')">策略待签核</span>
  <el-dialog v-model="open" append-to-body :title="editing?.label||'需求确认'" width="min(440px,94vw)"
    :close-on-click-modal="false" :close-on-press-escape="!locked" :show-close="!locked">
    <form @submit.prevent="submit">
      <p>{{row.desc}}</p>
      <p>验收标准：{{row.acceptanceCriteria}}</p>
      <label>已核验证据
        <select v-model="selected" :disabled="locked" required>
          <option disabled value="">{{files.length?'请选择':'暂无已核验证据'}}</option>
          <option v-for="f in files" :key="f.evidenceId" :value="f.evidenceRef">{{f.originalName}} · {{f.evidenceId.slice(0,8)}}</option>
        </select>
      </label>
      <el-pagination v-if="total>20" :current-page="page" :page-size="20" :total="total" layout="prev,pager,next"
        :disabled="locked" @current-change="load"/>
      <el-alert v-if="error" :title="error" type="error" :closable="false"/>
      <div class="actions">
        <el-button :disabled="locked" @click="open=false">取消</el-button>
        <el-button v-if="retry" :loading="busy" @click="send(retry)"><RotateCcw :size="15"/>重试上次提交</el-button>
        <el-button type="primary" native-type="submit" :disabled="locked||!canSubmit" :loading="busy">确认提交</el-button>
      </div>
    </form>
  </el-dialog>
</template>
