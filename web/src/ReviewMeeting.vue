<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {CalendarDays,FileText,RotateCcw} from 'lucide-vue-next'
import {isUnconfirmedWrite} from './api-error'

type Person={personId:string;name:string}
type Meeting={reviewId:string;revision:number;scheduledAt?:string;attendeeIds:string[];attendeeOptions:Person[];minutes?:string;
  canSchedule:boolean;canRecordMinutes:boolean}
type Attempt={body:string;key:string;revision:number}
const props=defineProps<{reviewId:string;revision:number;disabled:boolean;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const state=ref<Meeting>(),loading=ref(false),saving=ref(false),retry=ref<Attempt>(),editing=ref(false)
const error=ref(''),notice=ref(''),datetime=ref(''),attendeeIds=ref<string[]>([]),minutes=ref('')
const navigationLocked=computed(()=>saving.value||!!retry.value)
defineExpose({navigationLocked})
let sequence=0
const availableAttendees=computed(()=>state.value?.attendeeOptions??[])
const displayAttendees=computed(()=>state.value?.attendeeIds.map(id=>availableAttendees.value.find(p=>p.personId===id)?.name??id)??[])
const path=()=> `reviews/${props.reviewId}/meeting`

function shanghai(value:string){
  return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
}
async function read(){
  const readId=++sequence
  loading.value=true;error.value=''
  try{
    const {data}=await props.api(path())
    if(readId===sequence)state.value=data
  }catch(e){
    if(readId===sequence){state.value=undefined;error.value=e instanceof Error?e.message:'未能读取会议资料。'}
  }finally{if(readId===sequence)loading.value=false}
}
watch(()=>props.revision,()=>{if(!editing.value&&!navigationLocked.value)void read()},{immediate:true})

function begin(){
  const meeting=state.value
  if(!meeting)return
  datetime.value=meeting.scheduledAt?new Date(new Date(meeting.scheduledAt).getTime()+8*3600000).toISOString().slice(0,16):''
  attendeeIds.value=[...meeting.attendeeIds];minutes.value=meeting.minutes??''
  editing.value=true;error.value='';notice.value=''
}
function cancel(){
  editing.value=false;error.value=''
  void read()
}
async function save(previous?:Attempt){
  if(saving.value||props.disabled&&!previous||!state.value)return
  const meeting=state.value
  const command=meeting.canSchedule?{
    action:'schedule',scheduledAt:datetime.value?new Date(`${datetime.value}+08:00`).toISOString():null,attendeeIds:attendeeIds.value,
  }:{action:'minutes',minutes:minutes.value.trim()}
  const attempt=previous??{body:JSON.stringify(command),key:crypto.randomUUID(),revision:meeting.revision}
  saving.value=true;error.value='';notice.value=''
  try{
    const {data}=await props.api(path(),{method:'PUT',body:attempt.body,headers:{
      'If-Match':String(attempt.revision),'Idempotency-Key':attempt.key,
    }})
    state.value=data;retry.value=undefined;editing.value=false;notice.value='会议资料已保存。'
  }catch(e){
    if(isUnconfirmedWrite(e)){retry.value=attempt;error.value='未能确认保存结果，可按原请求重试。'}
    else{retry.value=undefined;error.value=e instanceof Error?e.message:'保存会议资料失败。'}
  }finally{saving.value=false}
}
</script>

<template>
  <section class="meeting" aria-label="评审会议资料" :aria-busy="loading||saving">
    <div class="meeting-heading">
      <h4>评审会议资料</h4>
      <el-button v-if="!editing&&(state?.canSchedule||state?.canRecordMinutes)" :disabled="disabled||loading" @click="begin">
        <CalendarDays v-if="state.canSchedule" :size="15" aria-hidden="true"/>
        <FileText v-else :size="15" aria-hidden="true"/>
        {{state.canSchedule?'安排评审会议':'记录会议纪要'}}
      </el-button>
    </div>
    <p v-if="loading" role="status">正在读取会议资料…</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <el-button v-if="error&&!navigationLocked" @click="cancel">{{editing?'放弃修改并重新读取会议资料':'重新读取会议资料'}}</el-button>
    <p v-if="notice" role="status">{{notice}}</p>
    <dl v-if="state&&!editing">
      <dt>会议时间</dt><dd><time v-if="state.scheduledAt" :datetime="state.scheduledAt">{{shanghai(state.scheduledAt)}} 北京时间</time><span v-else>未安排</span></dd>
      <dt>参会人</dt><dd>{{displayAttendees.length?displayAttendees.join('、'):'未登记'}}</dd>
      <dt>会议纪要</dt><dd class="meeting-minutes">{{state.minutes??'未登记'}}</dd>
    </dl>
    <form v-if="state&&editing" @submit.prevent="save()">
      <fieldset :disabled="disabled||navigationLocked">
        <template v-if="state.canSchedule">
          <label>会议时间（北京时间）<input v-model="datetime" type="datetime-local"></label>
          <label>参会人<select v-model="attendeeIds" aria-label="参会人" multiple>
            <option v-for="person in availableAttendees" :key="person.personId" :value="person.personId">{{person.name}} · {{person.personId}}</option>
          </select></label>
        </template>
        <label v-else>会议纪要<textarea v-model="minutes" required maxlength="8000" rows="5"/></label>
      </fieldset>
      <div class="actions">
        <el-button :disabled="navigationLocked||disabled" @click="cancel">取消</el-button>
        <el-button v-if="retry" :loading="saving" @click="save(retry)"><RotateCcw :size="15" aria-hidden="true"/>重试保存会议资料</el-button>
        <el-button v-else native-type="submit" type="primary" :disabled="disabled||navigationLocked||(!state.canSchedule&&!minutes.trim())">保存会议资料</el-button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.meeting{padding:12px 16px;border:1px solid var(--pmo-line);border-radius:8px;margin-top:12px}
.meeting-heading{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap}
.meeting-heading h4{margin:0}
.meeting dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 16px;margin:12px 0 0}
.meeting dt{color:var(--pmo-secondary)}
.meeting dd{margin:0}
.meeting-minutes{white-space:pre-wrap}
fieldset{min-width:0;border:0;padding:0;margin:12px 0 0;display:grid;gap:12px}
select[multiple]{min-height:120px}
</style>
