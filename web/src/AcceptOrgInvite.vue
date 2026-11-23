<script setup lang="ts">
import {ref} from 'vue'
import {ArrowLeft,Check,UserRoundPlus} from 'lucide-vue-next'
const props=defineProps<{api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{back:[];accepted:[string]}>()
const invitationCode=ref(''),username=ref(''),displayName=ref(''),password=ref(''),confirm=ref('')
const busy=ref(false),error=ref('')
let attempt=crypto.randomUUID()
function changed(){attempt=crypto.randomUUID()}
async function accept(){
  if(busy.value)return
  if(password.value!==confirm.value){error.value='两次密码不一致。';return}
  busy.value=true;error.value=''
  try{
    const {data}=await props.api('auth/invitations/accept',{method:'POST',headers:{'Idempotency-Key':attempt,'If-Match':'1'},
      body:JSON.stringify({invitationCode:invitationCode.value,username:username.value,displayName:displayName.value,password:password.value})})
    emit('accepted',data.username)
  }catch(e){error.value=e instanceof Error?e.message:'接受邀请失败。'}
  finally{busy.value=false}
}
</script>

<template>
  <div class="mark"><UserRoundPlus :size="25" aria-hidden="true"/></div>
  <h1>接受组织邀请</h1>
  <p class="auth-subtitle">使用邀请码加入组织</p>
  <form aria-label="接受组织邀请" @input="changed" @submit.prevent="accept">
    <label>邀请码<input v-model.trim="invitationCode" type="password" autocomplete="off" minlength="43" maxlength="43" required :disabled="busy"></label>
    <label>显示姓名<input v-model.trim="displayName" maxlength="120" required :disabled="busy"></label>
    <label>账号<input v-model.trim="username" autocomplete="username" minlength="3" maxlength="120" pattern="[A-Za-z0-9][A-Za-z0-9_.\-]*" required :disabled="busy"></label>
    <label>设置密码<input v-model="password" autocomplete="new-password" type="password" minlength="14" maxlength="256" required :disabled="busy"></label>
    <label>再次输入密码<input v-model="confirm" autocomplete="new-password" type="password" minlength="14" maxlength="256" required :disabled="busy"></label>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-button type="primary" native-type="submit" :loading="busy"><Check :size="15"/>确认接受</el-button>
    <el-button text :disabled="busy" @click="emit('back')"><ArrowLeft :size="15"/>返回登录</el-button>
  </form>
</template>
