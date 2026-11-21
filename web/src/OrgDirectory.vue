<script setup lang="ts">
import {ref} from 'vue'
import {Check,Copy,RefreshCw,ShieldOff,UserPlus,Users,X} from 'lucide-vue-next'

type Tab='users'|'members'|'invitations'
type User={userId:string;personId:string;username:string;name:string;isActive:boolean;personActive:boolean;revision:number;canChangeStatus:boolean}
type Invitation={invitationId:string;email:string;roleHint:string;revision:number;expiresAt:string;state:string}
type Member={personId:string;name:string;displayRole?:string;userId?:string;isActive:boolean;revision:number}
type Target={kind:'user';item:User}|{kind:'invitation';item:Invitation}
const props=defineProps<{api:(path:string,options?:RequestInit,headers?:(h:Headers)=>void)=>Promise<any>}>()
const shown=ref(false),tab=ref<Tab>('users'),page=ref(1),total=ref(0),reading=ref(false),busy=ref(false)
const users=ref<User[]>([]),members=ref<Member[]>([]),invites=ref<Invitation[]>([])
const issueForm=ref(false),email=ref(''),role=ref('member'),secret=ref(''),target=ref<Target|null>(null),reason=ref('')
const error=ref(''),notice=ref('')
const roles:Record<string,string>={member:'成员',pm:'项目经理',reviewer:'评审人',sponsor:'Sponsor',client_representative:'客户代表'}
const inviteStatus:Record<string,string>={pending:'待接受',accepted:'已接受',expired:'已过期',revoked:'已撤销'}
const paths:Record<Tab,string>={users:'admin/users',members:'admin/members',invitations:'admin/users/invitations'}
let generation=0,sequence=0,attempt:{signature:string;key:string}|undefined

function time(at:string){return new Date(at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}
function status(item:User){return item.isActive&&item.personActive?'正常':'已停用'}
function message(e:unknown){return e instanceof Error?e.message:'请求失败。'}
function key(signature:string){if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()};return attempt.key}
function clear(){issueForm.value=false;target.value=null;reason.value='';secret.value='';notice.value='';error.value='';attempt=undefined}
function close(){++generation;clear();busy.value=false;reading.value=false}
function show(){shown.value=true;tab.value='users';void read(1)}
function navigate(next:Tab){if(busy.value)return;clear();tab.value=next;void read(1)}
function startIssue(){clear();email.value='';role.value='member';issueForm.value=true}
function edit(item:Target){clear();target.value=item}
async function read(n=page.value){
  const id=++sequence,scope=generation,tabAt=tab.value
  reading.value=true;error.value=''
  try{
    const {data}=await props.api(`${paths[tabAt]}?page=${n}`)
    if(id!==sequence||scope!==generation)return
    if(tabAt==='users')users.value=data.items
    else if(tabAt==='members')members.value=data.items
    else invites.value=data.items
    page.value=data.page;total.value=data.total
  }catch(e){if(id===sequence&&scope===generation)error.value=message(e)}
  finally{if(id===sequence&&scope===generation)reading.value=false}
}
async function invite(){
  const scope=generation,body=JSON.stringify({email:email.value,role:role.value})
  busy.value=true;error.value=''
  try{
    await props.api(paths.invitations,{method:'POST',headers:{'Idempotency-Key':key(body)},body},headers=>{
      if(scope===generation)secret.value=headers.get('X-PMO-Org-Invitation')??''
    })
    if(scope!==generation)return
    issueForm.value=false;attempt=undefined;notice.value=secret.value?'邀请码已签发，仅显示一次。':'邀请已签发，原邀请码不可重读。'
    await read(1)
  }catch(e){if(scope===generation)error.value=message(e)}
  finally{if(scope===generation)busy.value=false}
}
async function confirm(){
  const item=target.value,scope=generation
  if(!item)return
  const path=item.kind==='user'?`admin/users/${item.item.userId}/status`:`admin/users/invitations/${item.item.invitationId}/revoke`
  const rev=item.item.revision, body=JSON.stringify({reason:reason.value,...(item.kind==='user'?{isActive:!item.item.isActive}:{})})
  busy.value=true;error.value=''
  try{
    await props.api(path,{method:item.kind==='user'?'PATCH':'POST',headers:{'Idempotency-Key':key(`${path}:${rev}:${body}`),'If-Match':String(rev)},body})
    if(scope!==generation)return
    target.value=null;attempt=undefined;notice.value=item.kind==='user'?'账号状态已更新。':'邀请已撤销。'
    await read()
  }catch(e){if(scope===generation)error.value=message(e)}
  finally{if(scope===generation)busy.value=false}
}
async function copy(){try{await navigator.clipboard.writeText(secret.value);notice.value='邀请码已复制。'}catch{error.value='无法访问剪贴板。'}}
</script>

<template>
  <el-button @click="show"><Users :size="15"/>组织人员</el-button>
  <el-drawer v-model="shown" title="组织人员" size="min(860px,100vw)" destroy-on-close @close="close">
    <el-tabs :model-value="tab" @tab-change="(name:string|number)=>navigate(name as Tab)">
      <el-tab-pane name="users" label="账号" :disabled="busy"/>
      <el-tab-pane name="members" label="成员" :disabled="busy"/>
      <el-tab-pane name="invitations" label="邀请" :disabled="busy"/>
    </el-tabs>
    <div class="org-toolbar">
      <span>{{total}} 条记录</span>
      <div class="org-actions">
        <el-button v-if="tab==='invitations'" :disabled="busy||issueForm" @click="startIssue"><UserPlus :size="15"/>邀请人员</el-button>
        <el-button aria-label="刷新组织人员" title="刷新组织人员" :disabled="busy||reading" @click="read()"><RefreshCw :size="15"/></el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <p v-if="notice" role="status">{{notice}}</p>
    <div v-if="secret" class="org-secret">
      <label>一次性邀请码<input :value="secret" readonly autocomplete="off"></label>
      <el-button aria-label="复制邀请码" title="复制邀请码" @click="copy"><Copy :size="15"/></el-button>
    </div>
    <form v-if="issueForm" aria-label="邀请人员" class="org-form" @submit.prevent="invite">
      <label>受邀邮箱<input v-model.trim="email" type="email" maxlength="254" required :disabled="busy"></label>
      <label>职务提示<select v-model="role" :disabled="busy"><option v-for="(label,k) in roles" :key="k" :value="k">{{label}}</option></select></label>
      <div class="org-actions"><el-button :disabled="busy" @click="clear"><X :size="15"/>取消</el-button>
        <el-button native-type="submit" type="primary" :loading="busy"><Check :size="15"/>签发邀请</el-button></div>
    </form>
    <form v-if="target" aria-label="确认组织操作" class="org-form" @submit.prevent="confirm">
      <strong>{{target.kind==='user'?`${target.item.isActive?'停用':'启用'} ${target.item.username}`:`撤销 ${target.item.email} 的邀请`}}</strong>
      <label>原因<input v-model.trim="reason" minlength="4" maxlength="500" required :disabled="busy"></label>
      <div class="org-actions"><el-button :disabled="busy" @click="clear"><X :size="15"/>取消</el-button>
        <el-button native-type="submit" type="primary" :loading="busy"><Check :size="15"/>确认变更</el-button></div>
    </form>
    <el-table v-if="tab==='users'" :data="users" v-loading="reading" row-key="userId" empty-text="暂无账号">
      <el-table-column prop="username" label="账号" min-width="160"/>
      <el-table-column prop="name" label="姓名" min-width="130"/>
      <el-table-column label="状态" min-width="92"><template #default="{row}"><el-tag size="small" :type="status(row)==='正常'?'success':'info'">{{status(row)}}</el-tag></template></el-table-column>
      <el-table-column prop="revision" label="版本" width="65"/>
      <el-table-column label="操作" width="90"><template #default="{row}">
        <el-button v-if="row.canChangeStatus && (row.isActive || row.personActive)" size="small" :disabled="busy" @click="edit({kind:'user',item:row})">
          <ShieldOff v-if="row.isActive" :size="14"/><Check v-else :size="14"/>{{row.isActive?'停用':'启用'}}</el-button></template></el-table-column>
    </el-table>
    <el-table v-else-if="tab==='members'" :data="members" v-loading="reading" row-key="personId" empty-text="暂无成员">
      <el-table-column prop="name" label="姓名" min-width="130"/>
      <el-table-column label="职务（资料）" min-width="125"><template #default="{row}">{{roles[row.displayRole]??row.displayRole??'未填写'}}</template></el-table-column>
      <el-table-column label="人员 ID" min-width="178"><template #default="{row}"><code class="org-id">{{row.personId}}</code></template></el-table-column>
      <el-table-column label="档案" width="90"><template #default="{row}">{{row.isActive?'有效':'停用'}}</template></el-table-column>
    </el-table>
    <el-table v-else :data="invites" v-loading="reading" row-key="invitationId" empty-text="暂无邀请">
      <el-table-column prop="email" label="受邀邮箱" min-width="195"/>
      <el-table-column label="状态" min-width="85"><template #default="{row}">{{inviteStatus[row.state]}}</template></el-table-column>
      <el-table-column label="过期时间" min-width="160"><template #default="{row}">{{time(row.expiresAt)}}</template></el-table-column>
      <el-table-column label="操作" width="95"><template #default="{row}"><el-button v-if="row.state==='pending'" size="small" :disabled="busy" @click="edit({kind:'invitation',item:row})"><ShieldOff :size="14"/>撤销</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="total>20" layout="prev,pager,next" :total="total" :page-size="20" :current-page="page" :disabled="reading||busy" @current-change="read"/>
  </el-drawer>
</template>

<style scoped>
.org-toolbar,.org-actions,.org-secret{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.org-toolbar{justify-content:space-between;margin-bottom:10px;color:var(--el-text-color-secondary)}
.org-secret{align-items:flex-end;margin-bottom:10px}.org-secret label{flex:1 1 260px;min-width:0}.org-form{display:grid;gap:9px;border-top:1px solid var(--el-border-color-light);padding:10px 0}
.org-form select{width:min(100%,270px);padding:7px 10px}.org-id{overflow-wrap:anywhere}.org-actions :deep(.el-button){margin-left:0}
</style>
