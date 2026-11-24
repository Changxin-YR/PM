<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {RefreshCw,ShieldOff} from 'lucide-vue-next'
import {isRejectedWrite,isUnconfirmedWrite} from './api-error'

type Member={personId:string;name:string;role:string;acceptedAt:string;canRevoke:boolean}
type MemberPage={items:Member[];page:number;pageSize:number;total:number;projectRevision:number}
type Revocation={body:string;revision:number;key:string}
const props=defineProps<{projectId:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{changed:[]}>()
const members=ref<Member[]>([]),page=ref(1),pageSize=ref(20),total=ref(0),revision=ref(0),loading=ref(false),error=ref('')
const target=ref<Member>(),targetRevision=ref(0),reason=ref(''),saving=ref(false),actionError=ref(''),notice=ref(''),stale=ref(false)
const retry=ref<Revocation>(),locked=computed(()=>saving.value||!!retry.value)
const roles:Record<string,string>={initiator:'草稿发起人',sponsor:'Sponsor',pm:'项目经理',member:'项目成员',reviewer:'评审人',client_representative:'客户代表'}
const maximumReasonLength=500
let seq=0

function nameFor(role:string){return roles[role]??role}

async function load(next=page.value){
  const n=++seq;loading.value=true;error.value=''
  try{
    const {data}:{data:MemberPage}=await props.api(`projects/${props.projectId}/members?page=${next}`)
    if(n!==seq)return
    const last=Math.max(1,Math.ceil(data.total/data.pageSize))
    if(next>last){await load(last);return}
    members.value=data.items;total.value=data.total;page.value=data.page;pageSize.value=data.pageSize;revision.value=data.projectRevision
  }catch(e){
    if(n===seq){members.value=[];total.value=0;revision.value=0;error.value=e instanceof Error?e.message:'读取项目成员失败。'}
  }finally{if(n===seq)loading.value=false}
}

function begin(member:Member){
  if(locked.value||loading.value||!member.canRevoke||!revision.value)return
  target.value=member;targetRevision.value=revision.value;reason.value='';actionError.value='';notice.value='';stale.value=false
}
function close(){if(!locked.value)target.value=undefined}

async function revoke(request:Revocation){
  if(saving.value)return
  saving.value=true;actionError.value=''
  try{
    await props.api(`projects/${props.projectId}/members/revoke`,{method:'POST',
      headers:{'If-Match':String(request.revision),'Idempotency-Key':request.key},body:request.body})
    retry.value=undefined;target.value=undefined;notice.value='已撤销该人员的项目角色。';emit('changed');await load()
  }catch(e){
    if(isUnconfirmedWrite(e)){
      retry.value=request;actionError.value='未能确认撤销结果，可重试上次提交。'
    }else{
      retry.value=undefined;actionError.value=e instanceof Error?e.message:'撤销项目角色失败。'
      if(isRejectedWrite(e)&&[401,403,404,409].includes(e.status)){
        stale.value=true;await load()
      }
    }
  }finally{saving.value=false}
}
function confirm(){
  const member=target.value,why=reason.value.trim()
  if(locked.value||stale.value||!member||why.length<4||why.length>maximumReasonLength)return
  const body=JSON.stringify({personId:member.personId,role:member.role,reason:why})
  void revoke({body,revision:targetRevision.value,key:crypto.randomUUID()})
}
onMounted(()=>void load())
</script>

<template>
  <section class="panel member-panel">
    <div class="toolbar">
      <span v-if="!error">有效成员角色 · {{total}}</span>
      <el-button title="刷新项目成员" aria-label="刷新项目成员" :disabled="loading||locked" @click="load()"><RefreshCw :size="15"/></el-button>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
    <p v-if="notice" class="member-notice" role="status">{{notice}}</p>
    <el-table v-if="!error" :data="members" v-loading="loading" :empty-text="loading?'正在读取项目成员…':'暂无有效成员'">
      <el-table-column prop="name" label="姓名" min-width="130"/>
      <el-table-column label="项目角色" min-width="130"><template #default="{row}">{{nameFor(row.role)}}</template></el-table-column>
      <el-table-column label="生效日期" min-width="115"><template #default="{row}">{{new Date(row.acceptedAt).toLocaleDateString('zh-CN')}}</template></el-table-column>
      <el-table-column v-if="members.some(member=>member.canRevoke)" label="操作" width="126">
        <template #default="{row}">
          <el-button v-if="row.canRevoke" size="small" :disabled="loading||locked" @click="begin(row)"><ShieldOff :size="15"/> 撤销角色</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p v-if="members.some(member=>member.role==='client_representative')" class="member-help">客户代表签署基线、验收等材料时，还需与本项目经核验的客户联系人建立绑定。</p>
    <el-pagination v-if="total>pageSize" :current-page="page" :total="total" :page-size="pageSize" :disabled="loading||locked" layout="prev,pager,next" @current-change="load"/>
    <el-dialog :model-value="!!target" title="撤销项目角色" width="min(560px,94vw)" destroy-on-close
      :close-on-click-modal="false" :close-on-press-escape="!locked" :show-close="!locked" @update:model-value="(value:boolean)=>{if(!value)close()}">
      <form v-if="target" aria-label="撤销项目角色" @submit.prevent="confirm">
        <p>即将撤销 {{target.name}} 的「{{nameFor(target.role)}}」项目角色。</p>
        <label>撤销原因<textarea v-model="reason" rows="3" required minlength="4" :maxlength="maximumReasonLength" :disabled="locked||stale"/></label>
        <el-alert v-if="actionError" type="error" :title="actionError" :closable="false" show-icon/>
        <p v-if="stale" class="member-help">请关闭此对话框，并从刷新后的成员列表重新选择角色。</p>
        <div class="actions">
          <el-button :disabled="locked" @click="close">取消</el-button>
          <el-button v-if="retry" :loading="saving" @click="revoke(retry)">重试上次提交</el-button>
          <el-button v-else native-type="submit" type="danger" :loading="saving" :disabled="stale||reason.trim().length<4">确认撤销项目角色</el-button>
        </div>
      </form>
    </el-dialog>
  </section>
</template>

<style scoped>
.member-help{margin:12px 0 0;color:var(--pmo-muted);font-size:13px}
.member-notice{color:var(--pmo-primary-hover)}
.member-panel form>p{margin:0}
</style>
